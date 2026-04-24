"""
Laptop-driven Lakebase sync for tables needed by the benchmark queries.

Reads from DBSQL warehouse, writes to Lakebase via psycopg2 COPY FROM STDIN.
Skips gold_gl_monthly_summary by default (already present, 7.7M rows).

Includes 3 gold tables + 3 silver tables (needed for Lakebase query joins).
Column specs derived from actual DESCRIBE TABLE output, not notebook spec drift.

Usage:
  /tmp/lakebase-venv/bin/python sync_lakebase_laptop.py
  /tmp/lakebase-venv/bin/python sync_lakebase_laptop.py --include-gl   # re-sync gl_monthly
  /tmp/lakebase-venv/bin/python sync_lakebase_laptop.py --only gold_arena_overview
"""
import argparse
import csv
import io
import json
import subprocess
import sys
import time

import psycopg2
from databricks import sql
from databricks.sdk import WorkspaceClient

PROFILE = "juniper-square-demo"
WORKSPACE_HOST = "fevm-juniper-square-demo.cloud.databricks.com"
WAREHOUSE_HTTP_PATH = "/sql/1.0/warehouses/133b52f9331b883d"
CATALOG = "juniper_square_demo_catalog"
SOURCE_SCHEMA = "pipeline"

LAKEBASE_ENDPOINT = "projects/juniper-sq-benchmark/branches/production/endpoints/primary"
LAKEBASE_HOST = "ep-curly-sun-d24e8bfa.database.us-east-1.cloud.databricks.com"
LAKEBASE_DB = "juniper_serving"
LAKEBASE_SCHEMA = "serving"

# Column order matches: (name, PG type). The SELECT emits columns in this order
# and we COPY in the same order. Internal Delta audit columns (_rescued_data,
# _ingested_at, _source_file) are intentionally excluded.
TABLE_SPECS = {
    "gold_arena_overview": {
        "pk": ["arena_id"],
        "columns": [
            ("arena_id",          "TEXT"),
            ("arena_name",        "TEXT"),
            ("total_aum",         "DOUBLE PRECISION"),
            ("total_properties",  "BIGINT"),
            ("total_investors",   "BIGINT"),
            ("total_commitments", "DOUBLE PRECISION"),
            ("total_revenue",     "DOUBLE PRECISION"),
            ("total_noi",         "DOUBLE PRECISION"),
        ],
        "indexes": [],
    },
    "gold_fund_performance": {
        "pk": ["fund_id"],
        "columns": [
            ("arena_id",                "TEXT"),
            ("fund_id",                 "TEXT"),
            ("fund_name",               "TEXT"),
            ("strategy",                "TEXT"),
            ("total_aum",               "DOUBLE PRECISION"),
            ("property_count",          "BIGINT"),
            ("total_invested",          "DOUBLE PRECISION"),
            ("current_portfolio_value", "DOUBLE PRECISION"),
            ("unrealized_gain_loss",    "DOUBLE PRECISION"),
            ("investor_count",          "BIGINT"),
            ("total_commitments",       "DOUBLE PRECISION"),
        ],
        "indexes": [("ix_fund_perf_arena", "(arena_id)")],
    },
    "gold_property_financials": {
        "pk": ["property_id", "month"],
        "columns": [
            ("arena_id",        "TEXT"),
            ("property_id",     "TEXT"),
            ("property_name",   "TEXT"),
            ("property_type",   "TEXT"),
            ("fund_name",       "TEXT"),
            ("month",           "TIMESTAMP"),
            ("revenue",         "DOUBLE PRECISION"),
            ("expenses",        "DOUBLE PRECISION"),
            ("noi",             "DOUBLE PRECISION"),
            ("occupancy_rate",  "DOUBLE PRECISION"),
        ],
        "indexes": [
            ("ix_prop_fin_arena",       "(arena_id)"),
            ("ix_prop_fin_arena_month", "(arena_id, month)"),
        ],
    },
    "gold_gl_monthly_summary": {
        "pk": ["arena_id", "fund_id", "month", "category"],
        "columns": [
            ("arena_id",               "TEXT"),
            ("fund_id",                "TEXT"),
            ("fund_name",              "TEXT"),
            ("category",               "TEXT"),
            ("month",                  "TIMESTAMP"),
            ("total_amount",           "DOUBLE PRECISION"),
            ("transaction_count",      "BIGINT"),
            ("avg_transaction_amount", "DOUBLE PRECISION"),
        ],
        "indexes": [
            ("ix_gl_arena",       "(arena_id)"),
            ("ix_gl_arena_month", "(arena_id, month)"),
            ("ix_gl_fund",        "(fund_id)"),
        ],
    },
    "silver_arenas": {
        "pk": ["arena_id"],
        "columns": [
            ("arena_id",   "TEXT"),
            ("arena_name", "TEXT"),
            ("created_at", "TEXT"),
            ("tier",       "TEXT"),
        ],
        "indexes": [],
    },
    "silver_funds": {
        "pk": ["fund_id"],
        "columns": [
            ("arena_id",          "TEXT"),
            ("aum",               "DOUBLE PRECISION"),
            ("fund_id",           "TEXT"),
            ("fund_name",         "TEXT"),
            ("status",            "TEXT"),
            ("strategy",          "TEXT"),
            ("target_return_pct", "DOUBLE PRECISION"),
            ("vintage_year",      "BIGINT"),
        ],
        "indexes": [("ix_funds_arena", "(arena_id)")],
    },
    "silver_investors": {
        # Investor can appear across multiple funds, so composite PK
        "pk": ["investor_id", "fund_id"],
        "columns": [
            ("investor_id",       "TEXT"),
            ("arena_id",          "TEXT"),
            ("investor_name",     "TEXT"),
            ("type",              "TEXT"),
            ("commitment_amount", "DOUBLE PRECISION"),
            ("fund_id",           "TEXT"),
            ("city",              "TEXT"),
            ("state",             "TEXT"),
        ],
        "indexes": [
            ("ix_investors_arena", "(arena_id)"),
            ("ix_investors_fund",  "(fund_id)"),
        ],
    },
}


def get_pat():
    out = subprocess.check_output(
        ["databricks", "--profile", PROFILE, "auth", "token"],
        text=True,
    )
    return json.loads(out)["access_token"]


def lakebase_connect(dbname=LAKEBASE_DB):
    w = WorkspaceClient(profile=PROFILE)
    cred = w.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT)
    return psycopg2.connect(
        host=LAKEBASE_HOST,
        dbname=dbname,
        user=w.current_user.me().user_name,
        password=cred.token,
        sslmode="require",
        connect_timeout=30,
    )


def ensure_schema():
    conn = lakebase_connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{LAKEBASE_SCHEMA}"')
    finally:
        conn.close()


def build_ddl(table, spec):
    cols_sql = ",\n    ".join(f'"{c}" {t}' for c, t in spec["columns"])
    pk_sql = ", ".join(f'"{c}"' for c in spec["pk"])
    return (
        f'CREATE TABLE "{LAKEBASE_SCHEMA}"."{table}" (\n'
        f"    {cols_sql},\n"
        f"    PRIMARY KEY ({pk_sql})\n"
        f")"
    )


def recreate_table(conn, table, spec):
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{LAKEBASE_SCHEMA}"."{table}" CASCADE')
        cur.execute(build_ddl(table, spec))
        for idx_name, cols_expr in spec.get("indexes", []):
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS {idx_name} '
                f'ON "{LAKEBASE_SCHEMA}"."{table}" {cols_expr}'
            )
    conn.commit()


def copy_rows(conn, table, col_names, rows_iter, batch_size=50_000):
    col_sql = ", ".join(f'"{c}"' for c in col_names)
    copy_sql = (
        f'COPY "{LAKEBASE_SCHEMA}"."{table}" ({col_sql}) '
        f"FROM STDIN WITH (FORMAT csv, NULL '\\N')"
    )
    total = 0
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)

    with conn.cursor() as cur:
        def flush():
            nonlocal buf, writer
            buf.seek(0)
            cur.copy_expert(copy_sql, buf)
            buf = io.StringIO()
            writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
            return writer

        count_in_batch = 0
        for row in rows_iter:
            writer.writerow(["\\N" if v is None else v for v in row])
            count_in_batch += 1
            total += 1
            if count_in_batch >= batch_size:
                writer = flush()
                count_in_batch = 0
                if total % 500_000 == 0:
                    print(f"    ...{total:,} rows")
        if count_in_batch:
            flush()
    conn.commit()
    return total


def sync_table(table, spec, sql_conn):
    print(f"\n=== {table} ===")
    col_names = [c for c, _ in spec["columns"]]
    col_sql_select = ", ".join(col_names)

    with sql_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {CATALOG}.{SOURCE_SCHEMA}.{table}")
        src_count = cur.fetchone()[0]
        print(f"source rows: {src_count:,}")

        conn = lakebase_connect()
        try:
            recreate_table(conn, table, spec)

            t0 = time.time()
            cur.execute(
                f"SELECT {col_sql_select} FROM {CATALOG}.{SOURCE_SCHEMA}.{table}"
            )

            def row_iter():
                while True:
                    rows = cur.fetchmany(10_000)
                    if not rows:
                        break
                    for r in rows:
                        yield tuple(r)

            loaded = copy_rows(conn, table, col_names, row_iter())
            elapsed = time.time() - t0
            rps = loaded / max(elapsed, 0.001)
            print(f"COPY loaded {loaded:,} rows in {elapsed:.1f}s ({rps:,.0f} rows/s)")

            with conn.cursor() as pgc:
                pgc.execute(f'ANALYZE "{LAKEBASE_SCHEMA}"."{table}"')
                pgc.execute(f'SELECT COUNT(*) FROM "{LAKEBASE_SCHEMA}"."{table}"')
                pg_count = pgc.fetchone()[0]
            conn.commit()
        finally:
            conn.close()

    if pg_count != src_count:
        raise RuntimeError(
            f"Row count mismatch: delta={src_count:,} lakebase={pg_count:,}"
        )
    print(f"OK: delta={src_count:,} == lakebase={pg_count:,}")
    return {"table": table, "src": src_count, "pg": pg_count, "elapsed_s": elapsed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-gl", action="store_true",
                        help="Re-sync gold_gl_monthly_summary (already landed; default skip)")
    parser.add_argument("--only", action="append", default=[],
                        help="Only sync the given table (can be repeated)")
    args = parser.parse_args()

    ensure_schema()
    print(f"Schema ready: {LAKEBASE_SCHEMA}")

    if args.only:
        tables = [(t, TABLE_SPECS[t]) for t in args.only]
    else:
        tables = [
            (t, spec) for t, spec in TABLE_SPECS.items()
            if args.include_gl or t != "gold_gl_monthly_summary"
        ]
    print(f"Plan: {[t for t, _ in tables]}")

    pat = get_pat()
    results = []
    failures = []
    with sql.connect(
        server_hostname=WORKSPACE_HOST,
        http_path=WAREHOUSE_HTTP_PATH,
        access_token=pat,
    ) as sql_conn:
        for table, spec in tables:
            try:
                results.append(sync_table(table, spec, sql_conn))
            except Exception as e:
                print(f"FAILED {table}: {e}")
                results.append({"table": table, "error": str(e)})
                failures.append(table)

    print("\n=== SUMMARY ===")
    for r in results:
        print(r)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
