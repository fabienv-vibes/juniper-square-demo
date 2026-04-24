# Databricks notebook source

# MAGIC %md
# MAGIC # Lakebase Autoscale Sync: Gold Tables via Managed Synced Tables (Snapshot)
# MAGIC
# MAGIC Syncs the four benchmark gold tables from `juniper_square_demo_catalog.pipeline` into a
# MAGIC Lakebase Autoscale project using **managed Synced Tables** (Lakeflow-backed reverse-ETL)
# MAGIC in `SNAPSHOT` mode. The Streamlit benchmark harness then hits Postgres directly to compare
# MAGIC concurrency/latency against DBSQL.
# MAGIC
# MAGIC **Why synced tables (vs psycopg2 `COPY FROM STDIN`)?**
# MAGIC - Aligns with the `databricks-lakebase-autoscale` skill's recommended reverse-ETL pattern.
# MAGIC - Managed Lakeflow pipeline handles Delta read + Postgres write; no manual OAuth token
# MAGIC   juggling, no manual DDL, no `COPY` buffering.
# MAGIC - Snapshot mode is explicitly called out in the skill as best for "initial setup, historical
# MAGIC   analysis" — matches our one-shot-before-benchmark need.
# MAGIC - Data lineage shows up in Unity Catalog (UC creates a read-only managed table pointing at
# MAGIC   the Postgres copy).
# MAGIC - Trivial re-run: call the same API, it refreshes.
# MAGIC
# MAGIC **What the notebook does:**
# MAGIC 1. Ensures the Lakebase database + schema exist (still needs psycopg for the pre-sync bootstrap).
# MAGIC 2. Creates one synced table per gold table with `SyncedTableSchedulingPolicy.SNAPSHOT`.
# MAGIC 3. Polls until each synced table reaches an `ONLINE_*` state (data available).
# MAGIC 4. Adds the custom indexes we need for the benchmark queries (synced tables only create
# MAGIC    the PK automatically; secondary indexes must be added via `CREATE INDEX`).
# MAGIC 5. Runs `ANALYZE` and compares row counts Delta vs Lakebase for verification.
# MAGIC
# MAGIC **Source of reference:** the original psycopg2 implementation is preserved at
# MAGIC `06_lakebase_sync_psycopg2.py.bak` in this same directory.

# COMMAND ----------

# MAGIC %pip install -U "databricks-sdk>=0.81.0" "psycopg2-binary>=2.9" -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# Widgets
dbutils.widgets.text("lakebase_project", "juniper-sq-benchmark", "Lakebase project id")
dbutils.widgets.text("lakebase_database", "juniper_serving", "Lakebase database")
dbutils.widgets.text("lakebase_schema", "serving", "Lakebase target schema")
dbutils.widgets.text("catalog", "juniper_square_demo_catalog", "UC catalog")
dbutils.widgets.text("source_schema", "pipeline", "UC source schema")
dbutils.widgets.text(
    "synced_storage_catalog",
    "juniper_square_demo_catalog",
    "UC catalog that owns the synced-table staging pipeline",
)
dbutils.widgets.text(
    "synced_storage_schema",
    "lakebase_sync_staging",
    "UC schema for synced-table staging (pipeline state)",
)
dbutils.widgets.text("poll_timeout_seconds", "1800", "Max seconds to wait for each sync to go online")
dbutils.widgets.text("poll_interval_seconds", "15", "Seconds between status polls")

LAKEBASE_PROJECT = dbutils.widgets.get("lakebase_project")
LAKEBASE_DATABASE = dbutils.widgets.get("lakebase_database")
LAKEBASE_SCHEMA = dbutils.widgets.get("lakebase_schema")
CATALOG = dbutils.widgets.get("catalog")
SOURCE_SCHEMA = dbutils.widgets.get("source_schema")
SYNCED_STORAGE_CATALOG = dbutils.widgets.get("synced_storage_catalog")
SYNCED_STORAGE_SCHEMA = dbutils.widgets.get("synced_storage_schema")
POLL_TIMEOUT_S = int(dbutils.widgets.get("poll_timeout_seconds"))
POLL_INTERVAL_S = int(dbutils.widgets.get("poll_interval_seconds"))

print(f"Project:           {LAKEBASE_PROJECT}")
print(f"Database:          {LAKEBASE_DATABASE}")
print(f"Schema:            {LAKEBASE_SCHEMA}")
print(f"UC source:         {CATALOG}.{SOURCE_SCHEMA}")
print(f"UC sync staging:   {SYNCED_STORAGE_CATALOG}.{SYNCED_STORAGE_SCHEMA}")
print(f"Poll: interval={POLL_INTERVAL_S}s timeout={POLL_TIMEOUT_S}s")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Table specs
# MAGIC
# MAGIC Primary keys and the secondary indexes the benchmark queries rely on. Keep synchronized
# MAGIC with the gold-layer schema in the SDP pipeline. The synced-table machinery picks up column
# MAGIC list + types automatically from the Delta source, so we only declare PKs + desired indexes.

# COMMAND ----------

# Each entry: source table name -> primary_key_columns + postgres indexes to create after the
# sync lands. Indexes are (name, "(col, ...)"). The PK gets a unique index automatically so we
# don't duplicate it here.
TABLE_SPECS = {
    "gold_arena_overview": {
        "pk": ["arena_id"],
        "indexes": [],  # PK on arena_id covers the only access pattern
    },
    "gold_fund_performance": {
        "pk": ["fund_id"],
        "indexes": [
            ("ix_fund_perf_arena", "(arena_id)"),
        ],
    },
    "gold_property_financials": {
        "pk": ["property_id", "month"],
        "indexes": [
            ("ix_prop_fin_arena",       "(arena_id)"),
            ("ix_prop_fin_arena_month", "(arena_id, month)"),
        ],
    },
    "gold_gl_monthly_summary": {
        "pk": ["arena_id", "fund_id", "month", "category"],
        "indexes": [
            ("ix_gl_arena",       "(arena_id)"),
            ("ix_gl_arena_month", "(arena_id, month)"),
        ],
    },
}

SYNC_ORDER = [
    "gold_arena_overview",
    "gold_fund_performance",
    "gold_property_financials",
    "gold_gl_monthly_summary",
]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Workspace client + source-availability guard

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
print(f"SDK version OK — connected as {w.current_user.me().user_name}")

def source_exists(table: str) -> bool:
    try:
        return spark.catalog.tableExists(f"{CATALOG}.{SOURCE_SCHEMA}.{table}")
    except Exception:
        return False

missing = [t for t in SYNC_ORDER if not source_exists(t)]
if missing:
    print(f"WARN: source tables not materialized yet: {missing}")
    print("These will be skipped. Re-run after the SDP pipeline finishes.")
present = [t for t in SYNC_ORDER if t not in missing]
print(f"Sources present: {present}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pre-sync bootstrap: ensure Lakebase database + target schema exist
# MAGIC
# MAGIC Synced tables land inside a Postgres database + schema that must already exist. We reuse
# MAGIC the same bootstrap flow from the psycopg2 version: a single short-lived OAuth token plus
# MAGIC `CREATE DATABASE / CREATE SCHEMA IF NOT EXISTS`.

# COMMAND ----------

import psycopg2
from psycopg2 import sql

def _endpoint_name():
    parent = f"projects/{LAKEBASE_PROJECT}/branches/production"
    endpoints = list(w.postgres.list_endpoints(parent=parent))
    if not endpoints:
        raise RuntimeError(f"No endpoints found under {parent}")
    return endpoints[0].name

EP_NAME = _endpoint_name()
EP = w.postgres.get_endpoint(name=EP_NAME)
HOST = EP.status.hosts.host
PG_USER = w.current_user.me().user_name
print(f"Endpoint: {EP_NAME}")
print(f"Host:     {HOST}")
print(f"PG user:  {PG_USER}")

def _fresh_pg_conn(dbname: str):
    cred = w.postgres.generate_database_credential(endpoint=EP_NAME)
    conn = psycopg2.connect(
        host=HOST, dbname=dbname, user=PG_USER, password=cred.token,
        sslmode="require", connect_timeout=30,
    )
    conn.autocommit = True
    return conn

# 1) Ensure the target database exists (create via default `databricks_postgres` if needed).
# psycopg2 `with conn:` starts an implicit transaction even when autocommit=True was set,
# which breaks CREATE DATABASE. Use the connection directly, not as a context manager.
try:
    conn = _fresh_pg_conn(LAKEBASE_DATABASE)
    conn.close()
    print(f"Database '{LAKEBASE_DATABASE}' exists.")
except Exception as e:
    if "does not exist" in str(e):
        print(f"Database '{LAKEBASE_DATABASE}' not found — creating via databricks_postgres")
        boot = _fresh_pg_conn("databricks_postgres")
        try:
            cur = boot.cursor()
            try:
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(LAKEBASE_DATABASE))
                )
            finally:
                cur.close()
        finally:
            boot.close()
        print(f"Created database {LAKEBASE_DATABASE}")
    else:
        raise

# 2) Ensure target schema exists
with _fresh_pg_conn(LAKEBASE_DATABASE) as conn:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(LAKEBASE_SCHEMA))
        )
print(f"Schema ready: {LAKEBASE_SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ensure UC staging schema exists
# MAGIC
# MAGIC Synced-table pipelines stage their state in a UC catalog/schema you designate.
# MAGIC We use `<catalog>.<schema>` (by default `juniper_square_demo_catalog.lakebase_sync_staging`).

# COMMAND ----------

spark.sql(
    f"CREATE SCHEMA IF NOT EXISTS `{SYNCED_STORAGE_CATALOG}`.`{SYNCED_STORAGE_SCHEMA}`"
)
print(f"UC staging schema ready: {SYNCED_STORAGE_CATALOG}.{SYNCED_STORAGE_SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create Synced Tables
# MAGIC
# MAGIC One synced table per gold table, snapshot mode. The resulting Postgres table is
# MAGIC accessible at `{lakebase_database}.{lakebase_schema}.{table_name}` and the UC read-only
# MAGIC mirror at `juniper_square_demo_catalog.serving.{table_name}` (catalog is a Lakebase
# MAGIC registered catalog — the synced-table API takes the three-part name pointing at Lakebase).
# MAGIC
# MAGIC **Naming note:** `SyncedDatabaseTable.name` is the three-part UC name that maps to the
# MAGIC Lakebase catalog. For autoscale, the Lakebase database registers as a UC catalog and the
# MAGIC Postgres database/schema show up as UC catalog/schema. We use `{LAKEBASE_DATABASE}` as the
# MAGIC catalog segment to match the Lakebase DB name.

# COMMAND ----------

from databricks.sdk.service.database import (
    SyncedDatabaseTable,
    SyncedTableSpec,
    NewPipelineSpec,
    SyncedTableSchedulingPolicy,
)

def _synced_uc_name(table: str) -> str:
    # UC three-part name where the catalog points at Lakebase
    return f"{LAKEBASE_DATABASE}.{LAKEBASE_SCHEMA}.{table}"

def create_or_get_synced(table: str, pk_cols: list[str]):
    """Create the synced table; if it already exists, fetch and return it."""
    uc_name = _synced_uc_name(table)
    src_fqn = f"{CATALOG}.{SOURCE_SCHEMA}.{table}"
    try:
        existing = w.database.get_synced_database_table(name=uc_name)
        print(f"  -> already exists: {uc_name}")
        return existing
    except Exception:
        pass

    print(f"  creating synced table: {uc_name}")
    created = w.database.create_synced_database_table(
        SyncedDatabaseTable(
            name=uc_name,
            spec=SyncedTableSpec(
                source_table_full_name=src_fqn,
                primary_key_columns=pk_cols,
                scheduling_policy=SyncedTableSchedulingPolicy.SNAPSHOT,
                new_pipeline_spec=NewPipelineSpec(
                    storage_catalog=SYNCED_STORAGE_CATALOG,
                    storage_schema=SYNCED_STORAGE_SCHEMA,
                ),
            ),
        )
    )
    return created

created_tables = {}
for table in SYNC_ORDER:
    print(f"\n=== {table} ===")
    if table in missing:
        print(f"  skipping — source not materialized")
        continue
    spec = TABLE_SPECS[table]
    created_tables[table] = create_or_get_synced(table, spec["pk"])

print(f"\nSynced-table create phase done for {len(created_tables)} table(s).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Poll until each sync reports ONLINE / data available
# MAGIC
# MAGIC Valid terminal states for a snapshot pipeline land in `ONLINE_*` (the initial snapshot has
# MAGIC completed and data is queryable). `PROVISIONING`/`PIPELINE_STARTING`/`TRIGGERED_UPDATE_IN_PROGRESS`
# MAGIC are transient. `PIPELINE_FAILED` / `OFFLINE_FAILED` are fatal.

# COMMAND ----------

TERMINAL_OK = {
    "ONLINE",
    "ONLINE_NO_PENDING_UPDATE",
    "ONLINE_CONTINUOUS_UPDATE",
    "ONLINE_UPDATING_PIPELINE_RESOURCES",
    "ONLINE_TRIGGERED_UPDATE",
}
TERMINAL_FAIL = {
    "OFFLINE_FAILED",
    "PIPELINE_FAILED",
    "OFFLINE",
}

def wait_for_online(table: str) -> str:
    uc_name = _synced_uc_name(table)
    deadline = time.time() + POLL_TIMEOUT_S
    last_state = None
    while time.time() < deadline:
        status = w.database.get_synced_database_table(name=uc_name)
        state = (
            status.data_synchronization_status.detailed_state
            if status.data_synchronization_status
            else None
        )
        state_str = state.value if hasattr(state, "value") else str(state)
        if state_str != last_state:
            msg = (
                status.data_synchronization_status.message
                if status.data_synchronization_status else ""
            )
            print(f"  [{table}] state={state_str} {('-- ' + msg) if msg else ''}")
            last_state = state_str
        if state_str in TERMINAL_OK:
            return state_str
        if state_str in TERMINAL_FAIL:
            raise RuntimeError(
                f"Sync for {table} reached fatal state {state_str}: "
                f"{status.data_synchronization_status.message}"
            )
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(
        f"Sync for {table} did not reach ONLINE within {POLL_TIMEOUT_S}s "
        f"(last state: {last_state})"
    )

poll_results = {}
for table in created_tables:
    print(f"\nWaiting for {table} ...")
    poll_results[table] = wait_for_online(table)
    print(f"  -> {poll_results[table]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create secondary indexes
# MAGIC
# MAGIC Synced tables auto-create the PK unique index but not additional indexes. We add the ones
# MAGIC the benchmark queries need (`arena_id`, `(arena_id, month)`) via plain `CREATE INDEX
# MAGIC IF NOT EXISTS`. Safe to re-run.

# COMMAND ----------

def create_indexes(table: str, indexes: list[tuple[str, str]]):
    if not indexes:
        print(f"  [{table}] no secondary indexes needed")
        return
    with _fresh_pg_conn(LAKEBASE_DATABASE) as conn:
        with conn.cursor() as cur:
            for idx_name, cols_expr in indexes:
                ddl = (
                    f'CREATE INDEX IF NOT EXISTS {idx_name} '
                    f'ON "{LAKEBASE_SCHEMA}"."{table}" {cols_expr}'
                )
                print(f"  [{table}] {ddl}")
                cur.execute(ddl)

for table in created_tables:
    create_indexes(table, TABLE_SPECS[table].get("indexes", []))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Post-sync ANALYZE + row-count verification

# COMMAND ----------

def verify(table: str) -> dict:
    src_fqn = f"{CATALOG}.{SOURCE_SCHEMA}.{table}"
    delta_count = spark.table(src_fqn).count()

    with _fresh_pg_conn(LAKEBASE_DATABASE) as conn:
        with conn.cursor() as cur:
            cur.execute(f'ANALYZE "{LAKEBASE_SCHEMA}"."{table}"')
            cur.execute(f'SELECT COUNT(*) FROM "{LAKEBASE_SCHEMA}"."{table}"')
            pg_count = cur.fetchone()[0]

    ok = pg_count == delta_count
    status = "ok" if ok else "mismatch"
    print(
        f"  [{table}] delta={delta_count:,} lakebase={pg_count:,} -> {status}"
    )
    return {
        "table": table,
        "status": status,
        "delta_rows": delta_count,
        "pg_rows": pg_count,
    }

verification = []
for table in created_tables:
    verification.append(verify(table))

# Also record skipped tables so the summary shows the full picture
for table in missing:
    verification.append({
        "table": table,
        "status": "skipped_source_missing",
        "delta_rows": 0,
        "pg_rows": 0,
    })

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

from pyspark.sql import Row

summary_df = spark.createDataFrame([Row(**r) for r in verification])
display(summary_df)

mismatches = [r for r in verification if r["status"] == "mismatch"]
if mismatches:
    raise RuntimeError(
        f"Row count mismatch on {len(mismatches)} table(s): {[r['table'] for r in mismatches]}"
    )

ok_count = sum(1 for r in verification if r["status"] == "ok")
skipped_count = sum(1 for r in verification if r["status"].startswith("skipped"))
print(f"\nSync complete. {ok_count} ok, {skipped_count} skipped.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Notes
# MAGIC
# MAGIC - **Snapshot vs Triggered:** snapshot is best for one-shot demo/benchmark loads. For a
# MAGIC   live serving layer that tracks gold changes, switch the scheduling policy to `TRIGGERED`
# MAGIC   and enable CDF on the source Delta tables (`ALTER TABLE ... SET TBLPROPERTIES
# MAGIC   (delta.enableChangeDataFeed = true)`). Continuous mode is overkill for these ~MB-scale
# MAGIC   gold tables.
# MAGIC - **Indexes:** the synced-table API does not accept custom Postgres indexes directly.
# MAGIC   Secondary indexes are created here via psycopg against the materialized table; the PK
# MAGIC   index is automatic.
# MAGIC - **Staging catalog/schema:** `new_pipeline_spec.storage_catalog/schema` is where the
# MAGIC   Lakeflow pipeline keeps its managed state (delta tables, pipeline metadata). Do not drop.
# MAGIC - **Re-running:** calling `create_synced_database_table` again on an existing table errors
# MAGIC   out; the helper falls back to `get_synced_database_table` so the notebook is idempotent.
# MAGIC   To force a full re-snapshot, delete the synced table (from Catalog Explorer or SDK) and
# MAGIC   rerun.
# MAGIC - **Teardown:** deleting a synced table requires dropping from BOTH Unity Catalog AND
# MAGIC   Postgres (see skill docs). For a 30-day-lifetime workspace we don't bother.
# MAGIC - **Reference implementation:** the original psycopg2 `COPY FROM STDIN` version is kept at
# MAGIC   `06_lakebase_sync_psycopg2.py.bak` — useful if we ever need the ultra-fast bulk-load path.
