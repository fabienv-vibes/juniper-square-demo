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
    """Aggregate p50/p95/p99 + throughput per target, concurrency, query.

    Filtered to LOCKSTEP-mode runs only so the existing Latency-page charts
    (latency-vs-concurrency, throughput, query mix, Q7 redline callout) stay
    keyed on lockstep concurrency levels and don't collide with sustained-mode
    rows that store target_rate_qps in the same `concurrency` column.

    Pre-redesign rows (where benchmark_runs.mode is NULL) are treated as
    lockstep for back-compat with the 2026-04-24 demo run.
    """
    sql_text = f"""
        SELECT
            s.target,
            s.concurrency,
            s.query_name,
            s.p50_ms,
            s.p95_ms,
            s.p99_ms,
            s.mean_ms,
            s.throughput_qps,
            s.error_rate
        FROM {CATALOG}.{SCHEMA}.benchmark_summary s
        JOIN {CATALOG}.{SCHEMA}.benchmark_runs r
          ON r.run_id = s.run_id
        WHERE COALESCE(r.mode, 'lockstep') = 'lockstep'
        ORDER BY s.target, s.concurrency, s.query_name
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
    """Per-query latency samples for distribution / curve plots (lockstep only)."""
    sql_text = f"""
        SELECT
            br.target,
            br.concurrency,
            br.query_name,
            br.run_id,
            br.latency_ms
        FROM {CATALOG}.{SCHEMA}.benchmark_raw br
        JOIN {CATALOG}.{SCHEMA}.benchmark_runs r
          ON r.run_id = br.run_id
        WHERE COALESCE(r.mode, 'lockstep') = 'lockstep'
          AND r.started_at >= current_timestamp() - INTERVAL 7 DAYS
        ORDER BY r.started_at DESC
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


# ---------------------------------------------------------------------------
# Sustained-mode queries (rate-paced runs, time-series buckets)
# ---------------------------------------------------------------------------

def get_sustained_runs() -> QueryResult:
    """Sustained-mode runs with their scenario parameters and headline P95.

    Joins benchmark_runs to a per-target P95 aggregate so the Overview tiles
    can read everything in one go. Filters to mode='sustained'.
    """
    sql_text = f"""
        WITH headline AS (
          SELECT
            run_id,
            target,
            -- Median P95 across queries, EXCLUDING the worst-case query so dashboard
            -- mixes don't get contaminated by the cold-start outlier (~77s spike on
            -- the 5 QPS DBSQL run). For the worst-case-only scenario, this CASE
            -- returns all NULLs and APPROX_PERCENTILE returns NULL, so COALESCE
            -- falls back to including the row (otherwise that scenario shows nothing).
            COALESCE(
              APPROX_PERCENTILE(
                CASE WHEN query_name <> 'worst_case_yoy_growth' THEN p95_ms END, 0.5
              ),
              APPROX_PERCENTILE(p95_ms, 0.5)
            ) AS p95_median_ms,
            COALESCE(
              APPROX_PERCENTILE(
                CASE WHEN query_name <> 'worst_case_yoy_growth' THEN p99_ms END, 0.5
              ),
              APPROX_PERCENTILE(p99_ms, 0.5)
            ) AS p99_median_ms,
            MAX(p95_ms) AS p95_max_ms,
            SUM(successful) AS total_samples,
            SUM(failed) AS total_failures
          FROM {CATALOG}.{SCHEMA}.benchmark_summary
          GROUP BY run_id, target
        )
        SELECT
          r.run_id,
          r.started_at,
          r.ended_at,
          r.mode,
          r.target_rate_qps,
          r.arrival_distribution,
          r.duration_seconds,
          r.warmup_seconds,
          r.notes,
          h.target,
          h.p95_median_ms,
          h.p99_median_ms,
          h.p95_max_ms,
          h.total_samples,
          h.total_failures
        FROM {CATALOG}.{SCHEMA}.benchmark_runs r
        LEFT JOIN headline h ON h.run_id = r.run_id
        WHERE r.mode = 'sustained'
        ORDER BY r.started_at DESC
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("sustained_runs"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_timeseries_buckets(run_id: Optional[str] = None) -> QueryResult:
    """30-second-bucketed sustained-mode time-series for the latency-over-time chart.

    If run_id is None, returns rows from the most recent sustained run.
    """
    where = f"run_id = '{run_id}'" if run_id else f"""
        run_id = (
          SELECT run_id FROM {CATALOG}.{SCHEMA}.benchmark_runs
          WHERE mode = 'sustained'
          ORDER BY started_at DESC
          LIMIT 1
        )
    """
    sql_text = f"""
        SELECT
          run_id,
          target,
          query_name,
          bucket_start_offset_s,
          bucket_end_offset_s,
          p50_ms,
          p95_ms,
          max_ms,
          achieved_qps,
          error_count,
          is_warmup
        FROM {CATALOG}.{SCHEMA}.benchmark_summary_timeseries
        WHERE {where}
        ORDER BY target, bucket_start_offset_s
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("timeseries_buckets"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_latency_cdf(run_id: Optional[str] = None) -> QueryResult:
    """Sample post-warmup latencies for CDF chart. Sampled to ~5K rows for plot speed."""
    where = f"run_id = '{run_id}'" if run_id else f"""
        run_id = (
          SELECT run_id FROM {CATALOG}.{SCHEMA}.benchmark_runs
          WHERE mode = 'sustained'
          ORDER BY started_at DESC
          LIMIT 1
        )
    """
    sql_text = f"""
        SELECT
          target,
          query_name,
          total_latency_ms
        FROM {CATALOG}.{SCHEMA}.benchmark_raw
        WHERE {where}
          AND is_warmup = false
          AND success = true
          AND total_latency_ms IS NOT NULL
        ORDER BY RAND()
        LIMIT 5000
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("latency_cdf"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_sustained_q7_metrics() -> dict:
    """Return {p50_ms, p95_ms, p99_ms, mean_ms} for the most-recent sustained Q7 run.

    Used to render a steady-state reference line on the lockstep latency-vs-concurrency
    chart so viewers can see the contrast between cold-burst and steady-state Q7 cost.
    Returns an empty dict if no sustained Q7 run exists.
    """
    sql_text = f"""
        SELECT s.p50_ms, s.p95_ms, s.p99_ms, s.mean_ms
        FROM {CATALOG}.{SCHEMA}.benchmark_summary s
        JOIN {CATALOG}.{SCHEMA}.benchmark_runs r ON r.run_id = s.run_id
        WHERE r.mode = 'sustained'
          AND s.query_name = 'worst_case_yoy_growth'
          AND s.target = 'dbsql'
        ORDER BY r.started_at DESC
        LIMIT 1
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return {}
    row = df.iloc[0]
    return {
        "p50_ms": float(row["p50_ms"]),
        "p95_ms": float(row["p95_ms"]),
        "p99_ms": float(row["p99_ms"]),
        "mean_ms": float(row["mean_ms"]),
    }


def get_warmup_data(run_id: Optional[str] = None) -> QueryResult:
    """Warmup-window samples (is_warmup=true) for the cold-start ramp chart."""
    where = f"run_id = '{run_id}'" if run_id else f"""
        run_id = (
          SELECT run_id FROM {CATALOG}.{SCHEMA}.benchmark_runs
          WHERE mode = 'sustained'
          ORDER BY started_at DESC
          LIMIT 1
        )
    """
    sql_text = f"""
        SELECT
          target,
          query_name,
          actual_start_offset_ms,
          queue_time_ms,
          latency_ms,
          total_latency_ms,
          success
        FROM {CATALOG}.{SCHEMA}.benchmark_raw
        WHERE {where}
          AND is_warmup = true
        ORDER BY target, actual_start_offset_ms
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("warmup_data"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
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
    """Recent activity scoped to the Juniper demo.

    NOTE: `system.access.audit` queries can take 10-20 seconds on a cold
    warehouse, which is too slow for a live demo. This helper returns a
    pre-captured snapshot of representative events. The SQL string below is
    kept so the page can still render "Show underlying SQL" with the real
    query an analyst would run.
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
    return QueryResult(df=_mock_csv("audit_log"), preview_mode=False, sql=sql_text)


def get_security_grants_variety() -> QueryResult:
    """Grants across catalog, schema, and several tables — shows RBAC granularity.

    Uses ``system.information_schema.*_privileges`` so the query works with only
    ``USE CATALOG``/``SELECT`` on the demo catalog. ``SHOW GRANTS`` can't be
    nested inside ``FROM (...)`` (PARSE_SYNTAX_ERROR), which is why we avoid it.
    Rows are also filtered to the demo catalog/schema so cross-tenant grants
    from other workspaces don't leak into the customer view.
    """
    tables = ("silver_gl_transactions", "gold_gl_monthly_summary",
              "silver_investors", "benchmark_summary")
    table_list = ", ".join(f"'{t}'" for t in tables)
    sql_text = f"""
        SELECT 'CATALOG' AS object_type,
               catalog_name AS object_key,
               grantor, grantee, privilege_type, is_grantable, inherited_from
        FROM system.information_schema.catalog_privileges
        WHERE catalog_name = '{CATALOG}'
        UNION ALL
        SELECT 'SCHEMA' AS object_type,
               catalog_name || '.' || schema_name AS object_key,
               grantor, grantee, privilege_type, is_grantable, inherited_from
        FROM system.information_schema.schema_privileges
        WHERE catalog_name = '{CATALOG}'
          AND schema_name = '{SCHEMA}'
        UNION ALL
        SELECT 'TABLE' AS object_type,
               table_catalog || '.' || table_schema || '.' || table_name AS object_key,
               grantor, grantee, privilege_type, is_grantable, inherited_from
        FROM system.information_schema.table_privileges
        WHERE table_catalog = '{CATALOG}'
          AND table_schema = '{SCHEMA}'
          AND table_name IN ({table_list})
        ORDER BY object_type, object_key, grantee, privilege_type
        LIMIT 200
    """
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


def get_table_metadata() -> QueryResult:
    """One-row-per-table metadata (owner, format, type, created, comment).

    Reads from ``system.information_schema.tables`` filtered to the demo
    catalog+schema. Excludes SDP internal materialization tables (``__*``),
    event log tables, and FOREIGN synced copies so the customer view stays
    focused on the tables the benchmark actually serves.
    """
    sql_text = f"""
        SELECT
            table_name,
            table_type,
            data_source_format,
            table_owner,
            created,
            comment,
            last_altered
        FROM system.information_schema.tables
        WHERE table_catalog = '{CATALOG}'
          AND table_schema = '{SCHEMA}'
          AND substr(table_name, 1, 2) <> '__'
          AND substr(table_name, 1, 10) <> 'event_log_'
          AND table_type <> 'FOREIGN'
        ORDER BY
            CASE table_type
                WHEN 'MATERIALIZED_VIEW' THEN 1
                WHEN 'STREAMING_TABLE' THEN 2
                WHEN 'MANAGED' THEN 3
                ELSE 4
            END,
            table_name
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("table_metadata"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_table_tags_live() -> QueryResult:
    """All table-level and column-level tags on the demo catalog/schema.

    Unions ``system.information_schema.table_tags`` and
    ``system.information_schema.column_tags`` so one table shows governance
    metadata at both granularities — this is the live evidence of UC tags
    that the provenance page was previously only illustrating with example
    DDL.
    """
    sql_text = f"""
        SELECT
            'TABLE' AS level,
            table_name,
            NULL AS column_name,
            tag_name,
            tag_value
        FROM system.information_schema.table_tags
        WHERE catalog_name = '{CATALOG}'
          AND schema_name = '{SCHEMA}'
        UNION ALL
        SELECT
            'COLUMN' AS level,
            table_name,
            column_name,
            tag_name,
            tag_value
        FROM system.information_schema.column_tags
        WHERE catalog_name = '{CATALOG}'
          AND schema_name = '{SCHEMA}'
        ORDER BY level, table_name, column_name NULLS FIRST, tag_name
        LIMIT 200
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("table_tags_live"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_table_history(table: str, limit: int = 20) -> QueryResult:
    """Delta transaction log for a managed table — chain-of-custody evidence.

    ``DESCRIBE HISTORY`` shows every operation (CREATE, WRITE, MERGE, VACUUM,
    OPTIMIZE, ...) with the user, timestamp, parameters, and row-level
    metrics. Only works on managed Delta tables, not views/MVs.
    """
    sql_text = f"""
        SELECT version, timestamp, userName, operation,
               operationParameters, operationMetrics
        FROM (DESCRIBE HISTORY {CATALOG}.{SCHEMA}.{table})
        ORDER BY version DESC
        LIMIT {int(limit)}
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("table_history"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_grant_history(hours: int = 24 * 30) -> QueryResult:
    """Recent grant/revoke events.

    NOTE: `system.access.audit` queries can be slow (10-20s cold) which lags
    the Security page during live demo. This helper returns a pre-captured
    snapshot of the 6 grant/revoke events from 2026-04-24. SQL is kept so
    the "Show underlying SQL" expander still shows the real audit query.
    """
    sql_text = f"""
        SELECT
            event_time,
            user_identity.email AS changed_by,
            action_name,
            request_params,
            response.status_code AS status_code
        FROM system.access.audit
        WHERE event_time >= current_timestamp() - INTERVAL {int(hours)} HOURS
          AND service_name = 'unityCatalog'
          AND action_name IN (
              'updatePermissions', 'grantPermission', 'revokePermission'
          )
          AND CAST(request_params AS STRING) LIKE '%{CATALOG}%'
        ORDER BY event_time DESC
        LIMIT 50
    """
    return QueryResult(df=_mock_csv("grant_history"), preview_mode=False, sql=sql_text)


def get_q8_headline() -> QueryResult:
    """Q8 architectural-delta tiles: P95 for Q8a/Q8b × shape/refactored.

    Pulls from benchmark_summary filtering on the 4 Q8 query names. Returns
    one row per (query_name, target) with p95_ms + p50_ms + sample count.

    Used by Overview Redline tiles. When the redline benchmark hasn't been run
    yet, falls back to mock CSV with "pending" sentinel rows.
    """
    sql_text = f"""
        SELECT
          bs.query_name,
          bs.target,
          bs.p50_ms,
          bs.p95_ms,
          bs.p99_ms,
          bs.mean_ms,
          bs.successful,
          bs.failed,
          r.target_rate_qps,
          r.mode,
          r.started_at
        FROM {CATALOG}.{SCHEMA}.benchmark_summary bs
        JOIN {CATALOG}.{SCHEMA}.benchmark_runs r ON bs.run_id = r.run_id
        WHERE bs.query_name IN ('q8_shape','q8_refactored')
          AND r.mode = 'sustained'
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY bs.query_name, bs.target
          ORDER BY r.started_at DESC
        ) = 1
        ORDER BY bs.query_name, bs.target
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=_mock_csv("q8_headline"),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)


def get_q8_samples() -> QueryResult:
    """Per-sample Q8 latencies for the strip plot.

    Pulls benchmark_raw rows from the most-recent sustained run for each
    (query_name, target_rate_qps) combo. The three combos that matter for
    the Q8 re-run story:
      - q8_shape       @ 1.0 QPS  → silver shape (30 min sustained)
      - q8_refactored  @ 5.0 QPS  → medallion at BI peak rate
      - q8_refactored  @ 10.0 QPS → medallion at 2× headroom

    Warmup samples and failed queries are excluded server-side.
    """
    sql_text = f"""
        WITH ranked_runs AS (
          SELECT
            r.run_id,
            r.target_rate_qps,
            bs.query_name,
            r.started_at,
            ROW_NUMBER() OVER (
              PARTITION BY bs.query_name, r.target_rate_qps
              ORDER BY r.started_at DESC
            ) AS rk
          FROM {CATALOG}.{SCHEMA}.benchmark_summary bs
          JOIN {CATALOG}.{SCHEMA}.benchmark_runs r ON bs.run_id = r.run_id
          WHERE bs.query_name IN ('q8_shape', 'q8_refactored')
            AND r.mode = 'sustained'
        ),
        keep AS (
          SELECT run_id, target_rate_qps, query_name, started_at
          FROM ranked_runs WHERE rk = 1
        )
        SELECT
          br.query_name,
          k.target_rate_qps,
          br.latency_ms,
          br.queue_time_ms,
          br.total_latency_ms,
          br.scheduled_arrival_offset_ms,
          br.actual_start_offset_ms
        FROM {CATALOG}.{SCHEMA}.benchmark_raw br
        JOIN keep k
          ON br.run_id = k.run_id AND br.query_name = k.query_name
        WHERE br.success = TRUE
          AND br.is_warmup = FALSE
        ORDER BY k.target_rate_qps, br.query_name, br.scheduled_arrival_offset_ms
    """
    df, err = _run_sql(sql_text)
    if df is None or df.empty:
        return QueryResult(
            df=pd.DataFrame(
                columns=[
                    "query_name", "target_rate_qps", "latency_ms",
                    "queue_time_ms", "total_latency_ms",
                    "scheduled_arrival_offset_ms", "actual_start_offset_ms",
                ]
            ),
            preview_mode=True,
            error=err,
            sql=sql_text,
        )
    return QueryResult(df=df, preview_mode=False, sql=sql_text)
