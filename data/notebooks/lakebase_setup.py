# Databricks notebook source
# MAGIC %md
# MAGIC # Juniper Square — Lakebase post-deploy bootstrap
# MAGIC
# MAGIC Run this **after** `databricks bundle deploy` to:
# MAGIC 1. Verify the Lakebase Autoscale project exists (create via UI/CLI if not — manual step, see README)
# MAGIC 2. Ensure the target Postgres database exists
# MAGIC 3. Create 4 SNAPSHOT synced tables (gold_* → Lakebase) with correct PKs
# MAGIC 4. Poll until each reaches ONLINE
# MAGIC 5. Capture the per-table sync pipeline_ids
# MAGIC 6. Stitch those pipeline_ids into the `juniper-benchmark-refresh` job as 4 parallel
# MAGIC    `pipeline_task` fan-out tasks (idempotent — replaces prior sync_* tasks every run)
# MAGIC
# MAGIC Idempotent: safe to re-run. Will drop+recreate synced tables (PG data persists; sync
# MAGIC just re-snapshots from gold).

# COMMAND ----------

# MAGIC %pip install -U "databricks-sdk>=0.81.0" "psycopg2-binary>=2.9" -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "juniper_square_demo_catalog", "UC catalog")
dbutils.widgets.text("pipeline_schema", "pipeline", "Gold tables schema")
dbutils.widgets.text("serving_schema", "serving", "Lakebase target schema (UC mirror)")
dbutils.widgets.text("lakebase_project", "juniper-sq-benchmark", "Lakebase project name")
dbutils.widgets.text("lakebase_database", "databricks_postgres", "Postgres database name")
dbutils.widgets.text("sdp_pipeline_id", "", "SDP medallion pipeline id (from DAB)")
dbutils.widgets.text("refresh_job_id", "", "juniper-benchmark-refresh job id (from DAB)")
dbutils.widgets.text("poll_timeout_seconds", "1800", "Per-table max wait for ONLINE")
dbutils.widgets.text("poll_interval_seconds", "15", "Status poll cadence")

CATALOG = dbutils.widgets.get("catalog")
PIPELINE_SCHEMA = dbutils.widgets.get("pipeline_schema")
SERVING_SCHEMA = dbutils.widgets.get("serving_schema")
LAKEBASE_PROJECT = dbutils.widgets.get("lakebase_project")
LAKEBASE_DATABASE = dbutils.widgets.get("lakebase_database")
SDP_PIPELINE_ID = dbutils.widgets.get("sdp_pipeline_id")
REFRESH_JOB_ID = dbutils.widgets.get("refresh_job_id")
POLL_TIMEOUT_S = int(dbutils.widgets.get("poll_timeout_seconds"))
POLL_INTERVAL_S = int(dbutils.widgets.get("poll_interval_seconds"))

assert SDP_PIPELINE_ID, "sdp_pipeline_id must be set (DAB-resolved)"
assert REFRESH_JOB_ID, "refresh_job_id must be set (DAB-resolved)"

print(f"Catalog:          {CATALOG}")
print(f"Pipeline schema:  {PIPELINE_SCHEMA}")
print(f"Serving schema:   {SERVING_SCHEMA}")
print(f"Lakebase project: {LAKEBASE_PROJECT}")
print(f"Lakebase db:      {LAKEBASE_DATABASE}")
print(f"SDP pipeline id:  {SDP_PIPELINE_ID}")
print(f"Refresh job id:   {REFRESH_JOB_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Table specs (PKs aligned with verified-unique gold-table grain)

# COMMAND ----------

TABLE_SPECS = {
    "gold_arena_overview": {
        "pk": ["arena_id"],
        "indexes": [],
    },
    "gold_fund_performance": {
        "pk": ["fund_id"],
        "indexes": [("ix_fund_perf_arena", "(arena_id)")],
    },
    "gold_property_financials": {
        # arena_id required: 1K properties shared across 10K arenas (verified collision in iter 2).
        "pk": ["arena_id", "property_id", "month"],
        "indexes": [
            ("ix_prop_fin_arena", "(arena_id)"),
            ("ix_prop_fin_arena_month", "(arena_id, month)"),
        ],
    },
    "gold_gl_monthly_summary": {
        "pk": ["arena_id", "fund_id", "month", "category"],
        "indexes": [
            ("ix_gl_arena", "(arena_id)"),
            ("ix_gl_arena_month", "(arena_id, month)"),
        ],
    },
}

SYNC_ORDER = list(TABLE_SPECS.keys())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify Lakebase project + endpoint exist

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
print(f"Connected as {w.current_user.me().user_name}")

parent = f"projects/{LAKEBASE_PROJECT}/branches/production"
try:
    endpoints = list(w.postgres.list_endpoints(parent=parent))
except Exception as e:
    raise RuntimeError(
        f"Lakebase project '{LAKEBASE_PROJECT}' / branch 'production' not found.\n"
        f"Create it first via UI (Compute → Lakebase) or CLI, autoscale 1-4 CU, "
        f"then re-run this notebook.\nSDK error: {e}"
    )
if not endpoints:
    raise RuntimeError(f"No endpoints under {parent}")

EP_NAME = endpoints[0].name
EP = w.postgres.get_endpoint(name=EP_NAME)
HOST = EP.status.hosts.host
PG_USER = w.current_user.me().user_name
print(f"Endpoint: {EP_NAME}")
print(f"Host:     {HOST}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ensure target Postgres database exists

# COMMAND ----------

import psycopg2
from psycopg2 import sql as pgsql

def _fresh_pg_conn(dbname: str):
    cred = w.postgres.generate_database_credential(endpoint=EP_NAME)
    conn = psycopg2.connect(
        host=HOST, dbname=dbname, user=PG_USER, password=cred.token,
        sslmode="require", connect_timeout=30,
    )
    conn.autocommit = True
    return conn

try:
    conn = _fresh_pg_conn(LAKEBASE_DATABASE)
    conn.close()
    print(f"Database '{LAKEBASE_DATABASE}' exists.")
except Exception as e:
    if "does not exist" in str(e):
        print(f"Database '{LAKEBASE_DATABASE}' not found — creating.")
        boot = _fresh_pg_conn("databricks_postgres")
        try:
            cur = boot.cursor()
            cur.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(LAKEBASE_DATABASE)))
            cur.close()
        finally:
            boot.close()
        print(f"Created database {LAKEBASE_DATABASE}")
    else:
        raise

# COMMAND ----------

# MAGIC %md
# MAGIC ## Guard: skip tables whose gold source isn't materialized yet

# COMMAND ----------

def _source_fqn(table: str) -> str:
    return f"{CATALOG}.{PIPELINE_SCHEMA}.{table}"

def source_exists(table: str) -> bool:
    try:
        return spark.catalog.tableExists(_source_fqn(table))
    except Exception:
        return False

missing = [t for t in SYNC_ORDER if not source_exists(t)]
present = [t for t in SYNC_ORDER if t not in missing]
if missing:
    print(f"WARN: gold tables not materialized yet: {missing}")
    print("Run the SDP pipeline first, then re-run this notebook.")
print(f"Gold sources ready: {present}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create synced tables (SNAPSHOT mode, idempotent)

# COMMAND ----------

from databricks.sdk.service.database import (
    SyncedDatabaseTable,
    SyncedTableSpec,
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
)

def _synced_uc_name(table: str) -> str:
    # Three-part name where catalog points at the Lakebase database
    return f"{LAKEBASE_DATABASE}.{SERVING_SCHEMA}.{table}"

def drop_if_exists(table: str) -> None:
    name = _synced_uc_name(table)
    try:
        w.database.delete_synced_database_table(name=name)
        print(f"  dropped existing synced table: {name}")
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "does not exist" in msg:
            return
        print(f"  (delete info: {e})")

def create_synced(table: str, pk_cols: list[str]):
    name = _synced_uc_name(table)
    src = _source_fqn(table)
    print(f"  creating SNAPSHOT synced table: {name}  <- {src}")
    return w.database.create_synced_database_table(
        SyncedDatabaseTable(
            name=name,
            spec=SyncedTableSpec(
                source_table_full_name=src,
                primary_key_columns=pk_cols,
                scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
                new_pipeline_spec=NewPipelineSpec(
                    storage_catalog=CATALOG,
                    storage_schema=PIPELINE_SCHEMA,
                ),
            ),
        )
    )

created = {}
for table in present:
    print(f"\n=== {table} ===")
    drop_if_exists(table)
    created[table] = create_synced(table, TABLE_SPECS[table]["pk"])

print(f"\nCreated {len(created)} synced table(s).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Poll until ONLINE

# COMMAND ----------

TERMINAL_OK = {
    "ONLINE",
    "ONLINE_NO_PENDING_UPDATE",
    "ONLINE_CONTINUOUS_UPDATE",
    "ONLINE_UPDATING_PIPELINE_RESOURCES",
    "ONLINE_TRIGGERED_UPDATE",
}
TERMINAL_FAIL = {"OFFLINE_FAILED", "PIPELINE_FAILED", "OFFLINE"}

def wait_for_online(table: str) -> str:
    name = _synced_uc_name(table)
    deadline = time.time() + POLL_TIMEOUT_S
    last = None
    while time.time() < deadline:
        st = w.database.get_synced_database_table(name=name)
        state = st.data_synchronization_status.detailed_state if st.data_synchronization_status else None
        state_str = state.value if hasattr(state, "value") else str(state)
        if state_str != last:
            msg = st.data_synchronization_status.message if st.data_synchronization_status else ""
            print(f"  [{table}] {state_str}{(' -- ' + msg) if msg else ''}")
            last = state_str
        if state_str in TERMINAL_OK:
            return state_str
        if state_str in TERMINAL_FAIL:
            raise RuntimeError(f"{table} fatal state {state_str}: {st.data_synchronization_status.message}")
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"{table} did not reach ONLINE within {POLL_TIMEOUT_S}s (last: {last})")

for table in created:
    print(f"\nWaiting for {table} ...")
    wait_for_online(table)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Capture pipeline_ids

# COMMAND ----------

pipeline_ids = {}
for table in created:
    st = w.database.get_synced_database_table(name=_synced_uc_name(table))
    pid = None
    if st.data_synchronization_status:
        pid = getattr(st.data_synchronization_status, "pipeline_id", None)
    if pid is None and st.spec:
        pid = getattr(st.spec, "pipeline_id", None)
    pipeline_ids[table] = pid
    print(f"  {table:40s} pipeline_id = {pid}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Secondary indexes (psycopg2 — synced-table API doesn't accept Postgres indexes)

# COMMAND ----------

for table, spec in TABLE_SPECS.items():
    if table not in created:
        continue
    indexes = spec.get("indexes", [])
    if not indexes:
        print(f"  [{table}] no secondary indexes")
        continue
    with _fresh_pg_conn(LAKEBASE_DATABASE) as conn:
        with conn.cursor() as cur:
            for idx_name, cols in indexes:
                ddl = f'CREATE INDEX IF NOT EXISTS {idx_name} ON "{SERVING_SCHEMA}"."{table}" {cols}'
                print(f"  [{table}] {ddl}")
                cur.execute(ddl)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Stitch sync pipeline_ids into the juniper-benchmark-refresh job
# MAGIC
# MAGIC Idempotent: replaces any existing `sync_*` tasks with a fresh fan-out keyed on the
# MAGIC current pipeline_ids. Keeps the `sdp_medallion_pipeline` task as-is.

# COMMAND ----------

job = w.jobs.get(job_id=int(REFRESH_JOB_ID))
settings = job.settings

# Drop any prior sync_* tasks; keep the sdp task
keep_tasks = [t for t in settings.tasks if not t.task_key.startswith("sync_")]

# Make sure the kept task is the SDP one — sanity check
sdp_keys = [t.task_key for t in keep_tasks if t.task_key == "sdp_medallion_pipeline"]
assert sdp_keys, f"refresh job is missing sdp_medallion_pipeline task: {[t.task_key for t in keep_tasks]}"

from databricks.sdk.service.jobs import Task, PipelineTask, TaskDependency, JobSettings

new_sync_tasks = []
for table, pid in pipeline_ids.items():
    if not pid:
        print(f"  WARN: no pipeline_id captured for {table}, skipping task wiring")
        continue
    short = table.replace("gold_", "")
    new_sync_tasks.append(
        Task(
            task_key=f"sync_{short}",
            description=f"Lakebase synced table: {table}",
            depends_on=[TaskDependency(task_key="sdp_medallion_pipeline")],
            pipeline_task=PipelineTask(pipeline_id=pid, full_refresh=False),
            timeout_seconds=3600 if "gl_monthly" in short else 1800,
        )
    )

settings.tasks = keep_tasks + new_sync_tasks
w.jobs.reset(job_id=int(REFRESH_JOB_ID), new_settings=settings)
print(f"\nRewired juniper-benchmark-refresh: 1 SDP task + {len(new_sync_tasks)} sync tasks.")
for t in settings.tasks:
    print(f"  - {t.task_key}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Next:
# MAGIC - Run `juniper-data-gen-wider` to produce raw landing files (~12 min)
# MAGIC - Run `juniper-benchmark-refresh` to populate medallion + sync to Lakebase (~5 min)
# MAGIC - Restart the app: `databricks apps stop/start juniper-benchmark-viewer`
