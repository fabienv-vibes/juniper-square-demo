#!/usr/bin/env python3
"""Backfill Delta tables from local CSV results (recovery for failed persist_to_delta).

When persist_to_delta() fails (e.g., warehouse auto-stopped between phases), the
scenario's CSVs are still in results/<timestamp>/. This script reads them and
inserts the equivalent rows into benchmark_runs / benchmark_summary /
benchmark_raw / benchmark_summary_timeseries.

Usage:
    python3 backfill_delta.py --results-dir results/20260428_153245 \\
        --scenario sustained_headroom_2x --rate 10 --duration 600 --warmup-seconds 90

The run_id is derived from the directory timestamp (UTC).
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse harness helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from concurrency_benchmark import (
    _sql_str, _sql_array_str, _sql_array_int, _insert_in_chunks, load_config,
)


def _parse_dir_to_run_id(dir_name: str) -> tuple[str, datetime]:
    """`20260428_153245` -> (`2026-04-28T15:32:45Z`, datetime utc)."""
    base = Path(dir_name).name
    dt = datetime.strptime(base, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    run_id = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return run_id, dt


def _opt_double(v):
    if v is None or v == "" or v == "None":
        return "NULL"
    try:
        return f"{float(v)}"
    except ValueError:
        return "NULL"


def _opt_int(v):
    if v is None or v == "" or v == "None":
        return "NULL"
    try:
        return f"{int(float(v))}"
    except ValueError:
        return "NULL"


def _bool_sql(v):
    return "TRUE" if str(v).strip().lower() == "true" else "FALSE"


def main():
    parser = argparse.ArgumentParser(description="Backfill Delta from local CSVs")
    parser.add_argument("--config", default="config_live.yaml")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--scenario", required=True, help="e.g. sustained_headroom_2x")
    parser.add_argument("--rate", type=float, required=True, help="QPS")
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--warmup-seconds", type=int, default=90)
    parser.add_argument("--arrival", default="poisson")
    args = parser.parse_args()

    config = load_config(args.config)

    results_dir = Path(args.results_dir)
    raw_csv = results_dir / "raw_results.csv"
    summary_csv = results_dir / "summary.csv"
    buckets_csv = results_dir / "timeseries_buckets.csv"

    if not raw_csv.exists() or not summary_csv.exists():
        print(f"[ERROR] Missing required CSV files in {results_dir}")
        sys.exit(1)

    run_id, started_at = _parse_dir_to_run_id(results_dir.name)
    print(f"Backfilling run_id={run_id} (scenario={args.scenario}, rate={args.rate} QPS)")

    # Load raw results to derive ended_at and targets
    with open(raw_csv) as f:
        raw_rows = list(csv.DictReader(f))
    if not raw_rows:
        print(f"[ERROR] {raw_csv} is empty")
        sys.exit(1)
    targets = sorted(set(r["target"] for r in raw_rows))
    max_offset_ms = max(
        float(r.get("actual_start_offset_ms") or 0) for r in raw_rows
    )
    # Add a tail buffer for in-flight queries' completion
    ended_at = datetime.fromtimestamp(
        started_at.timestamp() + max_offset_ms / 1000 + 30, tz=timezone.utc
    )

    print(f"  targets: {targets}")
    print(f"  raw rows: {len(raw_rows)}")
    print(f"  ended_at (estimated): {ended_at.isoformat()}")

    with open(summary_csv) as f:
        summary_rows = list(csv.DictReader(f))
    print(f"  summary rows: {len(summary_rows)}")

    bucket_rows = []
    if buckets_csv.exists():
        with open(buckets_csv) as f:
            bucket_rows = list(csv.DictReader(f))
    print(f"  bucket rows: {len(bucket_rows)}")

    # Build SQL inserts
    dp = config.get("delta_persistence", {}) or {}
    catalog = dp.get("catalog", "juniper_square_demo_catalog")
    schema = dp.get("schema", "pipeline")
    fq = f"{catalog}.{schema}"
    runs_tbl = f"{fq}.benchmark_runs"
    summary_tbl = f"{fq}.benchmark_summary"
    raw_tbl = f"{fq}.benchmark_raw"
    timeseries_tbl = f"{fq}.benchmark_summary_timeseries"

    workspace = config.get("dbsql", {}).get("hostname", "")
    notes = f"5/10 redesign — {args.scenario} (backfilled from {results_dir})"

    runs_values = (
        f"({_sql_str(run_id)}, "
        f"TIMESTAMP {_sql_str(started_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"TIMESTAMP {_sql_str(ended_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"{_sql_str('')}, "  # git_sha
        f"{_sql_str(workspace)}, "
        f"{_sql_array_str(targets)}, "
        f"{_sql_array_int([])}, "  # concurrency_levels (empty for sustained)
        f"0, 0, "                   # iterations, warmup (lockstep) — 0 in sustained
        f"{_sql_str(notes)}, "
        f"{_sql_str('sustained')}, "
        f"{float(args.rate)}, "
        f"{_sql_str(args.arrival)}, "
        f"{int(args.duration)}, "
        f"{int(args.warmup_seconds)})"
    )

    from databricks import sql as dbsql
    conn = dbsql.connect(
        server_hostname=config["dbsql"]["hostname"],
        http_path=config["dbsql"]["http_path"],
        access_token=config["dbsql"]["token"],
    )
    try:
        cursor = conn.cursor()
        # Idempotency: delete any prior backfill for this run_id
        for tbl in (raw_tbl, summary_tbl, timeseries_tbl, runs_tbl):
            cursor.execute(f"DELETE FROM {tbl} WHERE run_id = {_sql_str(run_id)}")
        print(f"  Cleared any prior rows for {run_id}")

        cursor.execute(f"INSERT INTO {runs_tbl} VALUES {runs_values}")
        print(f"  Wrote 1 row to {runs_tbl}")

        # Summary rows
        if summary_rows:
            rows_sql = []
            for s in summary_rows:
                rows_sql.append(
                    f"({_sql_str(run_id)}, {_sql_str(s['target'])}, {int(s['concurrency'])}, "
                    f"{_sql_str(s['query_name'])}, {int(s['total_queries'])}, {int(s['successful'])}, "
                    f"{int(s['failed'])}, {float(s['p50_ms'])}, {float(s['p95_ms'])}, "
                    f"{float(s['p99_ms'])}, {float(s['min_ms'])}, {float(s['max_ms'])}, "
                    f"{float(s['mean_ms'])}, {float(s['throughput_qps'])}, {float(s['error_rate'])})"
                )
            _insert_in_chunks(cursor, summary_tbl, rows_sql, chunk_size=200)
            print(f"  Wrote {len(rows_sql)} rows to {summary_tbl}")

        # Raw rows (with all sustained columns)
        if raw_rows:
            rows_sql = []
            for r in raw_rows:
                rows_sql.append(
                    f"({_sql_str(run_id)}, {_sql_str(r['target'])}, {int(r['concurrency'])}, "
                    f"{_sql_str(r['query_name'])}, {int(r['iteration'])}, {int(r['thread_id'])}, "
                    f"{float(r['latency_ms'])}, {_bool_sql(r['success'])}, "
                    f"{_sql_str(r['error']) if r['error'] else 'NULL'}, "
                    f"{_opt_double(r.get('scheduled_arrival_offset_ms'))}, "
                    f"{_opt_double(r.get('actual_start_offset_ms'))}, "
                    f"{_opt_double(r.get('queue_time_ms'))}, "
                    f"{_opt_double(r.get('total_latency_ms'))}, "
                    f"{_bool_sql(r.get('is_warmup','False'))})"
                )
            _insert_in_chunks(cursor, raw_tbl, rows_sql, chunk_size=500)
            print(f"  Wrote {len(rows_sql)} rows to {raw_tbl}")

        # Bucket rows
        if bucket_rows:
            rows_sql = []
            for b in bucket_rows:
                rows_sql.append(
                    f"({_sql_str(run_id)}, {_sql_str(b['target'])}, {_sql_str(b['query_name'])}, "
                    f"{int(b['bucket_start_offset_s'])}, {int(b['bucket_end_offset_s'])}, "
                    f"{float(b['p50_ms'])}, {float(b['p95_ms'])}, {float(b['max_ms'])}, "
                    f"{float(b['achieved_qps'])}, {int(b['error_count'])}, "
                    f"{_bool_sql(b['is_warmup'])})"
                )
            _insert_in_chunks(cursor, timeseries_tbl, rows_sql, chunk_size=200)
            print(f"  Wrote {len(rows_sql)} rows to {timeseries_tbl}")

        cursor.close()
        print(f"\nBackfill complete for run_id={run_id}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
