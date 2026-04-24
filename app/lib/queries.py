"""SQL query helpers + fallback to mock CSVs.

All queries target the `juniper_square_demo_catalog.pipeline` schema for
benchmark tables, and `system.*` for audit/lineage/lakeflow queries.

If the SQL connection fails OR a table returns zero rows, the helpers read
the corresponding CSV in `mock_data/` and flag `preview_mode=True` so the UI
can show a banner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd


# Defaults; overridable via env (set by app.yaml)
CATALOG = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "pipeline")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "133b52f9331b883d")
HTTP_PATH = f"/sql/1.0/warehouses/{WAREHOUSE_ID}"

MOCK_DIR = Path(__file__).resolve().parent.parent / "mock_data"


@dataclass
class QueryResult:
    df: pd.DataFrame
    preview_mode: bool
    error: Optional[str] = None
    sql: Optional[str] = None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_connection():
    """Return a databricks-sql-connector Connection, or None if unavailable.

    Uses:
      - DATABRICKS_HOST  (set in Databricks Apps runtime)
      - DATABRICKS_TOKEN (for local dev) or service-principal OAuth in-app
    """
    try:
        from databricks import sql  # type: ignore
    except ImportError:
        return None

    host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN")
    client_id = os.environ.get("DATABRICKS_CLIENT_ID")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET")

    if not host:
        return None

    try:
        if token:
            return sql.connect(
                server_hostname=host,
                http_path=HTTP_PATH,
                access_token=token,
            )
        elif client_id and client_secret:
            # Service-principal path (Databricks Apps runtime injects these)
            return sql.connect(
                server_hostname=host,
                http_path=HTTP_PATH,
                credentials_provider=lambda: _oauth_m2m(host, client_id, client_secret),
            )
    except Exception:
        return None
    return None


def _oauth_m2m(host, client_id, client_secret):
    """Minimal m2m provider shim -- real impl uses databricks-sdk."""
    try:
        from databricks.sdk.core import Config, oauth_service_principal
        cfg = Config(
            host=f"https://{host}",
            client_id=client_id,
            client_secret=client_secret,
        )
        return oauth_service_principal(cfg)
    except Exception:
        return None


def _run_sql(sql_text: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Return (df, error_message). df is None if the query failed."""
    conn = _get_connection()
    if conn is None:
        return None, "no-connection"
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description] if cur.description else []
        conn.close()
        if not rows:
            return pd.DataFrame(columns=cols), None
        return pd.DataFrame(rows, columns=cols), None
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return None, str(e)


def _mock_csv(name: str) -> pd.DataFrame:
    path = MOCK_DIR / f"{name}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Benchmark queries (juniper_square_demo_catalog.pipeline.benchmark_*)
# ---------------------------------------------------------------------------

def get_benchmark_summary() -> QueryResult:
    """Aggregate p50/p95/p99 + throughput per target, concurrency, query."""
    sql_text = f"""
        SELECT
            target,
            concurrency,
            query_name,
            p50_ms,
            p95_ms,
            p99_ms,
            mean_ms,
            throughput_qps,
            error_rate
        FROM {CATALOG}.{SCHEMA}.benchmark_summary
        ORDER BY target, concurrency, query_name
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("benchmark_summary"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_benchmark_raw() -> QueryResult:
    """Per-query latency samples for distribution / curve plots."""
    sql_text = f"""
        SELECT
            target,
            concurrency,
            query_name,
            run_ts,
            latency_ms
        FROM {CATALOG}.{SCHEMA}.benchmark_raw
        WHERE run_ts >= current_timestamp() - INTERVAL 7 DAYS
        ORDER BY run_ts DESC
        LIMIT 50000
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("benchmark_raw"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_benchmark_runs() -> QueryResult:
    """Metadata about each benchmark run (arena count, dataset size, date)."""
    sql_text = f"""
        SELECT
            run_id,
            run_ts,
            arena_count,
            dataset_size_tb,
            target,
            notes
        FROM {CATALOG}.{SCHEMA}.benchmark_runs
        ORDER BY run_ts DESC
        LIMIT 20
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        # No CSV fallback needed -- return empty with preview flag
        return QueryResult(df=pd.DataFrame(), preview_mode=True, error=err, sql=sql_text)
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


# ---------------------------------------------------------------------------
# System-table queries
# ---------------------------------------------------------------------------

def get_lakeflow_pipelines() -> QueryResult:
    """Active pipelines + their latest state change from system.lakeflow.pipelines.

    Filtered to Juniper-related pipelines only (by name prefix). Avoids leaking
    unrelated workspace tenants into a customer-facing demo.
    """
    sql_text = """
        SELECT
            pipeline_id,
            name,
            pipeline_type,
            created_by,
            create_time,
            change_time
        FROM system.lakeflow.pipelines
        WHERE delete_time IS NULL
          AND (
            lower(name) LIKE '%juniper%'
            OR name ILIKE 'Synced table: juniper%'
          )
        ORDER BY change_time DESC
        LIMIT 50
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("lakeflow_pipelines"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_audit_log(hours: int = 24) -> QueryResult:
    """Recent activity scoped to the Juniper demo from system.access.audit.

    Filters to queries that actually touched the demo catalog or Lakebase
    project, so a customer-facing view doesn't expose unrelated workspace tenants.
    """
    sql_text = f"""
        SELECT
            event_time,
            user_identity.email AS user_email,
            service_name,
            action_name,
            source_ip_address,
            request_params,
            response.status_code AS status_code
        FROM system.access.audit
        WHERE event_time >= current_timestamp() - INTERVAL {hours} HOURS
          AND (
            CAST(request_params AS STRING) LIKE '%{CATALOG}%'
            OR CAST(request_params AS STRING) LIKE '%juniper-sq-benchmark%'
            OR CAST(response AS STRING) LIKE '%{CATALOG}%'
          )
        ORDER BY event_time DESC
        LIMIT 200
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("audit_log"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_catalog_grants() -> QueryResult:
    """Grants currently on the demo catalog."""
    sql_text = f"SHOW GRANTS ON CATALOG {CATALOG}"
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("catalog_grants"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_security_grants_variety() -> QueryResult:
    """Grants across catalog, schema, and several tables — shows RBAC granularity.

    Union of SHOW GRANTS output across multiple UC securables, tagged with the
    object type and key so the UI can show that Databricks enforces RBAC at
    every level (catalog → schema → table → column), not just at the top.
    """
    objects = [
        ("CATALOG", CATALOG),
        ("SCHEMA", f"{CATALOG}.{SCHEMA}"),
        ("TABLE", f"{CATALOG}.{SCHEMA}.silver_gl_transactions"),
        ("TABLE", f"{CATALOG}.{SCHEMA}.gold_gl_monthly_summary"),
        ("TABLE", f"{CATALOG}.{SCHEMA}.silver_investors"),
        ("TABLE", f"{CATALOG}.{SCHEMA}.benchmark_summary"),
    ]
    parts = []
    for obj_type, obj_key in objects:
        parts.append(
            f"SELECT '{obj_type}' AS object_type, "
            f"'{obj_key}' AS object_key, * "
            f"FROM (SHOW GRANTS ON {obj_type} {obj_key})"
        )
    sql_text = "\nUNION ALL\n".join(parts)
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("security_grants_variety"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_demo_table_stats() -> QueryResult:
    """Current row counts + last-altered timestamps for the Juniper demo tables.

    Makes "Keeping the lights on" tangible: the dashboard shows that the actual
    demo pipeline is materialized and fresh, not an empty placeholder. Uses
    information_schema.tables for the commit timestamp (cheap lookup, no
    DESCRIBE HISTORY subquery which doesn't compose in Spark SQL).
    """
    tables = [
        "silver_gl_transactions",
        "gold_gl_monthly_summary",
        "gold_property_financials",
        "gold_fund_performance",
        "gold_arena_overview",
    ]
    unions = []
    for t in tables:
        unions.append(f"""
SELECT
    '{t}' AS table_name,
    (SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.{t}) AS row_count,
    (
      SELECT last_altered FROM {CATALOG}.information_schema.tables
      WHERE table_schema = '{SCHEMA}' AND table_name = '{t}'
      LIMIT 1
    ) AS last_altered
""".strip())
    sql_text = "\nUNION ALL\n".join(unions) + "\nORDER BY table_name"
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("demo_table_stats"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def describe_table_extended(table: str) -> QueryResult:
    """DESCRIBE EXTENDED on a specific table (for provenance page)."""
    sql_text = f"DESCRIBE EXTENDED {CATALOG}.{SCHEMA}.{table}"
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("describe_extended"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)
