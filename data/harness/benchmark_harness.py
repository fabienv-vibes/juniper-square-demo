# Databricks notebook source
# MAGIC %md
# MAGIC # Juniper Square Concurrency Benchmark Harness
# MAGIC
# MAGIC This is the exact Python harness used to produce the latency and throughput
# MAGIC numbers shown in the **juniper-benchmark-viewer** app.
# MAGIC
# MAGIC It was executed **locally from a laptop** against this workspace — cross-country
# MAGIC network round-trips are included in the measured ms. In-workspace runs would
# MAGIC be faster.
# MAGIC
# MAGIC ## What it does
# MAGIC
# MAGIC For each concurrency level (1, 5, 10, 20) and each target (DBSQL + Lakebase):
# MAGIC
# MAGIC 1. Expands the 6-query benchmark mix by weight (Reporting ×2, Ad-hoc ×3)
# MAGIC 2. Spawns N threads with `ThreadPoolExecutor`, each running one query from the mix (round-robin)
# MAGIC 3. Substitutes `{arena_id}` with a random arena from the pool per execution (multi-tenant simulation)
# MAGIC 4. Runs `warmup` rounds (discarded) + `iterations` rounds (measured)
# MAGIC 5. Computes p50 / p95 / p99 / min / max / mean / throughput QPS / error rate per (target, concurrency, query)
# MAGIC 6. Writes raw + summary CSVs to `results/<timestamp>/`
# MAGIC 7. Persists to Delta: `juniper_square_demo_catalog.pipeline.benchmark_runs` / `benchmark_summary` / `benchmark_raw`
# MAGIC
# MAGIC ## How it was run
# MAGIC
# MAGIC ```bash
# MAGIC # 1. Generate fresh OAuth tokens from the Databricks CLI profile into config_live.yaml
# MAGIC python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark
# MAGIC
# MAGIC # 2. Run the full benchmark against both targets at all concurrency levels
# MAGIC python3 concurrency_benchmark.py --config config_live.yaml --target both \
# MAGIC     --levels 1,5,10,20 --iterations 5 --warmup 1
# MAGIC ```
# MAGIC
# MAGIC Results for the latest run live in `pipeline.benchmark_summary` — the app queries that table.
# MAGIC
# MAGIC ## Config (template)
# MAGIC
# MAGIC ```yaml
# MAGIC dbsql:
# MAGIC   hostname: fevm-juniper-square-demo.cloud.databricks.com
# MAGIC   http_path: /sql/1.0/warehouses/133b52f9331b883d
# MAGIC   token: <generated from CLI>
# MAGIC
# MAGIC lakebase:
# MAGIC   host: ep-curly-sun-d24e8bfa.database.us-east-1.cloud.databricks.com
# MAGIC   port: 5432
# MAGIC   database: juniper_serving
# MAGIC   user: fabien.vaucheret@databricks.com
# MAGIC   password: <generated from CLI>
# MAGIC   sslmode: require
# MAGIC
# MAGIC benchmark:
# MAGIC   levels: [1, 5, 10, 20]
# MAGIC   iterations: 5
# MAGIC   warmup: 1
# MAGIC
# MAGIC delta_persistence:
# MAGIC   catalog: juniper_square_demo_catalog
# MAGIC   schema: pipeline
# MAGIC
# MAGIC arena_id_pool:
# MAGIC   - ARN-00000
# MAGIC   - ARN-00042
# MAGIC   - ...  # 100 arenas total
# MAGIC
# MAGIC queries:
# MAGIC   - name: fund_performance_arena
# MAGIC     weight: 2
# MAGIC     sql_dbsql: |
# MAGIC       SELECT * FROM juniper_square_demo_catalog.pipeline.gold_fund_performance
# MAGIC       WHERE arena_id = '{arena_id}' ORDER BY total_aum DESC
# MAGIC     sql_lakebase: |
# MAGIC       SELECT * FROM serving.gold_fund_performance
# MAGIC       WHERE arena_id = '{arena_id}' ORDER BY total_aum DESC
# MAGIC   # ... 5 more queries
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## `concurrency_benchmark.py`
# MAGIC The harness itself. This is the code that ran on a laptop to produce the measurements.

# COMMAND ----------

#!/usr/bin/env python3
"""
Concurrency Benchmark: DBSQL vs Lakebase

Fires N parallel queries at configurable concurrency levels, measures latency
percentiles and throughput, and identifies the "redline" where performance degrades.

Usage:
    # Run against DBSQL
    python3 concurrency_benchmark.py --config config.yaml --target dbsql

    # Run against Lakebase
    python3 concurrency_benchmark.py --config config.yaml --target lakebase

    # Run both and compare
    python3 concurrency_benchmark.py --config config.yaml --target both

    # Quick smoke test
    python3 concurrency_benchmark.py --config config.yaml --target dbsql --levels 1,4 --iterations 3

    # Skip Delta persistence (write only local CSV)
    python3 concurrency_benchmark.py --config config.yaml --target dbsql --skip-delta-write
"""

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    query_name: str
    target: str
    concurrency: int
    iteration: int
    thread_id: int
    latency_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class LevelSummary:
    target: str
    concurrency: int
    query_name: str
    total_queries: int
    successful: int
    failed: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    throughput_qps: float  # queries per second
    error_rate: float


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_dbsql_connection(config: dict):
    """Create a DBSQL connection using databricks-sql-connector."""
    from databricks import sql as dbsql

    return dbsql.connect(
        server_hostname=config["dbsql"]["hostname"],
        http_path=config["dbsql"]["http_path"],
        access_token=config["dbsql"]["token"],
    )


def get_lakebase_connection(config: dict):
    """Create a Lakebase (Postgres) connection using psycopg2."""
    import psycopg2

    lb = config["lakebase"]
    return psycopg2.connect(
        host=lb["host"],
        port=lb.get("port", 5432),
        dbname=lb["database"],
        user=lb["user"],
        password=lb["password"],
        sslmode=lb.get("sslmode", "require"),
    )


def run_single_query_dbsql(config: dict, query: str) -> float:
    """Execute a single query on DBSQL and return latency in ms."""
    conn = get_dbsql_connection(config)
    try:
        cursor = conn.cursor()
        start = time.perf_counter()
        cursor.execute(query)
        cursor.fetchall()
        elapsed_ms = (time.perf_counter() - start) * 1000
        cursor.close()
        return elapsed_ms
    finally:
        conn.close()


def run_single_query_lakebase(config: dict, query: str) -> float:
    """Execute a single query on Lakebase and return latency in ms."""
    conn = get_lakebase_connection(config)
    try:
        cursor = conn.cursor()
        start = time.perf_counter()
        cursor.execute(query)
        cursor.fetchall()
        elapsed_ms = (time.perf_counter() - start) * 1000
        cursor.close()
        return elapsed_ms
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query preparation (weighted expansion + arena_id substitution)
# ---------------------------------------------------------------------------

def expand_queries_by_weight(queries: list[dict]) -> list[dict]:
    """Expand the query list by each query's 'weight' field (default 1).

    Example: if Q2 has weight=3 and Q1 has weight=1, the returned list contains
    [Q1, Q2, Q2, Q2]. The harness then round-robins this expanded list across
    threads, simulating a realistic query mix.
    """
    expanded: list[dict] = []
    for q in queries:
        weight = max(1, int(q.get("weight", 1)))
        expanded.extend([q] * weight)
    return expanded


def substitute_arena_id(sql: str, arena_pool: list[str]) -> str:
    """Replace '{arena_id}' in the SQL with a random arena from the pool."""
    if not arena_pool or "{arena_id}" not in sql:
        return sql
    return sql.replace("{arena_id}", random.choice(arena_pool))


# ---------------------------------------------------------------------------
# Benchmark engine
# ---------------------------------------------------------------------------

def run_query_task(
    config: dict,
    target: str,
    query_name: str,
    query_sql: str,
    concurrency: int,
    iteration: int,
    thread_id: int,
) -> QueryResult:
    """Execute one query in a thread and return the result."""
    try:
        if target == "dbsql":
            latency = run_single_query_dbsql(config, query_sql)
        else:
            latency = run_single_query_lakebase(config, query_sql)
        return QueryResult(
            query_name=query_name,
            target=target,
            concurrency=concurrency,
            iteration=iteration,
            thread_id=thread_id,
            latency_ms=latency,
            success=True,
        )
    except Exception as e:
        return QueryResult(
            query_name=query_name,
            target=target,
            concurrency=concurrency,
            iteration=iteration,
            thread_id=thread_id,
            latency_ms=-1,
            success=False,
            error=str(e),
        )


def percentile(data: list[float], pct: float) -> float:
    """Calculate percentile from a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (pct / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    d = k - f
    return sorted_data[f] + d * (sorted_data[c] - sorted_data[f])


def benchmark_level(
    config: dict,
    target: str,
    queries: list[dict],
    concurrency: int,
    iterations: int,
    warmup: int = 0,
    arena_pool: Optional[list[str]] = None,
) -> tuple[list[QueryResult], list[LevelSummary]]:
    """Run all queries at a given concurrency level for N iterations."""
    all_results: list[QueryResult] = []
    total_rounds = warmup + iterations
    arena_pool = arena_pool or []

    for i in range(total_rounds):
        is_warmup = i < warmup
        round_label = f"warmup {i+1}/{warmup}" if is_warmup else f"iter {i-warmup+1}/{iterations}"
        print(f"    {round_label} @ concurrency={concurrency}...", end=" ", flush=True)

        round_start = time.perf_counter()
        futures = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            for thread_id in range(concurrency):
                q = queries[thread_id % len(queries)]
                query_sql = q[f"sql_{target}"] if f"sql_{target}" in q else q["sql"]
                query_sql = substitute_arena_id(query_sql, arena_pool)
                future = executor.submit(
                    run_query_task,
                    config,
                    target,
                    q["name"],
                    query_sql,
                    concurrency,
                    i - warmup if not is_warmup else -1,
                    thread_id,
                )
                futures.append(future)

            results = [f.result() for f in as_completed(futures)]

        round_elapsed = (time.perf_counter() - round_start) * 1000
        successes = sum(1 for r in results if r.success)
        print(f"{successes}/{len(results)} ok, {round_elapsed:.0f}ms wall time")

        if not is_warmup:
            all_results.extend(results)

    # Summarize per query (dedupe by query name since the expanded list has repeats)
    summaries = []
    seen_names = set()
    for q in queries:
        if q["name"] in seen_names:
            continue
        seen_names.add(q["name"])
        qr = [r for r in all_results if r.query_name == q["name"] and r.success]
        latencies = [r.latency_ms for r in qr]
        total = sum(1 for r in all_results if r.query_name == q["name"])
        failed = total - len(qr)

        if latencies:
            summaries.append(LevelSummary(
                target=target,
                concurrency=concurrency,
                query_name=q["name"],
                total_queries=total,
                successful=len(qr),
                failed=failed,
                p50_ms=percentile(latencies, 50),
                p95_ms=percentile(latencies, 95),
                p99_ms=percentile(latencies, 99),
                min_ms=min(latencies),
                max_ms=max(latencies),
                mean_ms=statistics.mean(latencies),
                throughput_qps=len(qr) / (max(latencies) / 1000) if latencies else 0,
                error_rate=failed / total if total > 0 else 0,
            ))

    return all_results, summaries


# ---------------------------------------------------------------------------
# Output / reporting
# ---------------------------------------------------------------------------

def print_summary_table(summaries: list[LevelSummary]):
    """Print a formatted summary table to console."""
    if not summaries:
        print("No results to display.")
        return

    header = (
        f"{'Target':<10} {'Conc':>5} {'Query':<30} "
        f"{'P50':>8} {'P95':>8} {'P99':>8} "
        f"{'Mean':>8} {'QPS':>8} {'Err%':>6}"
    )
    print(f"\n{'='*len(header)}")
    print(header)
    print(f"{'-'*len(header)}")

    for s in sorted(summaries, key=lambda x: (x.target, x.concurrency, x.query_name)):
        print(
            f"{s.target:<10} {s.concurrency:>5} {s.query_name:<30} "
            f"{s.p50_ms:>7.1f}{'ms':1} {s.p95_ms:>7.1f}{'ms':1} {s.p99_ms:>7.1f}{'ms':1} "
            f"{s.mean_ms:>7.1f}{'ms':1} {s.throughput_qps:>7.1f} {s.error_rate*100:>5.1f}%"
        )

    print(f"{'='*len(header)}\n")


def write_csv(results: list[QueryResult], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "query_name", "target", "concurrency", "iteration",
            "thread_id", "latency_ms", "success", "error",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"Raw results written to {output_path}")


def write_summary_csv(summaries: list[LevelSummary], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "target", "concurrency", "query_name", "total_queries",
            "successful", "failed", "p50_ms", "p95_ms", "p99_ms",
            "min_ms", "max_ms", "mean_ms", "throughput_qps", "error_rate",
        ])
        writer.writeheader()
        for s in summaries:
            writer.writerow(asdict(s))
    print(f"Summary written to {output_path}")


def plot_redline(summaries: list[LevelSummary], output_path: Path):
    """Generate a latency-vs-concurrency chart (the 'redline' curve)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed, skipping chart generation.")
        return

    targets = sorted(set(s.target for s in summaries))
    queries = sorted(set(s.query_name for s in summaries))

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    ax1 = axes[0]
    for target in targets:
        for query in queries:
            points = sorted(
                [s for s in summaries if s.target == target and s.query_name == query],
                key=lambda x: x.concurrency,
            )
            if points:
                x = [p.concurrency for p in points]
                y = [p.p95_ms for p in points]
                label = f"{target} - {query[:20]}"
                ax1.plot(x, y, marker="o", label=label)

    ax1.set_xlabel("Concurrency Level")
    ax1.set_ylabel("P95 Latency (ms)")
    ax1.set_title("P95 Latency vs Concurrency")
    ax1.legend(fontsize=7, loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.set_xscale("log", base=2)

    ax2 = axes[1]
    for target in targets:
        concurrency_levels = sorted(set(s.concurrency for s in summaries if s.target == target))
        agg_qps = {}
        for level in concurrency_levels:
            level_sums = [s for s in summaries if s.target == target and s.concurrency == level]
            agg_qps[level] = sum(s.throughput_qps for s in level_sums)

        if agg_qps:
            x = list(agg_qps.keys())
            y = list(agg_qps.values())
            ax2.plot(x, y, marker="s", label=f"{target} (aggregate)")

    ax2.set_xlabel("Concurrency Level")
    ax2.set_ylabel("Throughput (queries/sec)")
    ax2.set_title("Throughput vs Concurrency")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.set_xscale("log", base=2)

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150)
    print(f"Redline chart saved to {output_path}")
    plt.close()


# ---------------------------------------------------------------------------
# Delta persistence
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def _sql_str(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("'", "''") + "'"


def _sql_array_str(vals: list[str]) -> str:
    if not vals:
        return "ARRAY()"
    return "ARRAY(" + ", ".join(_sql_str(v) for v in vals) + ")"


def _sql_array_int(vals: list[int]) -> str:
    if not vals:
        return "ARRAY()"
    return "ARRAY(" + ", ".join(str(int(v)) for v in vals) + ")"


def persist_to_delta(
    config: dict,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    targets: list[str],
    levels: list[int],
    iterations: int,
    warmup: int,
    notes: str,
    results: list[QueryResult],
    summaries: list[LevelSummary],
):
    """Write benchmark_runs / benchmark_summary / benchmark_raw to DBSQL."""
    from databricks import sql as dbsql

    dp = config.get("delta_persistence", {}) or {}
    catalog = dp.get("catalog", "juniper_square_demo_catalog")
    schema = dp.get("schema", "pipeline")
    fq = f"{catalog}.{schema}"

    runs_tbl = f"{fq}.benchmark_runs"
    summary_tbl = f"{fq}.benchmark_summary"
    raw_tbl = f"{fq}.benchmark_raw"

    ddl_runs = f"""
    CREATE TABLE IF NOT EXISTS {runs_tbl} (
      run_id STRING, started_at TIMESTAMP, ended_at TIMESTAMP,
      git_sha STRING, workspace STRING, targets ARRAY<STRING>,
      concurrency_levels ARRAY<INT>, iterations INT, warmup INT, notes STRING
    ) USING DELTA
    """.strip()

    ddl_summary = f"""
    CREATE TABLE IF NOT EXISTS {summary_tbl} (
      run_id STRING, target STRING, concurrency INT, query_name STRING,
      total_queries INT, successful INT, failed INT,
      p50_ms DOUBLE, p95_ms DOUBLE, p99_ms DOUBLE,
      min_ms DOUBLE, max_ms DOUBLE, mean_ms DOUBLE,
      throughput_qps DOUBLE, error_rate DOUBLE
    ) USING DELTA
    """.strip()

    ddl_raw = f"""
    CREATE TABLE IF NOT EXISTS {raw_tbl} (
      run_id STRING, target STRING, concurrency INT, query_name STRING,
      iteration INT, thread_id INT, latency_ms DOUBLE, success BOOLEAN, error STRING
    ) USING DELTA
    """.strip()

    workspace = config.get("dbsql", {}).get("hostname", "")
    git_sha = _git_sha()

    runs_values = (
        f"({_sql_str(run_id)}, "
        f"TIMESTAMP {_sql_str(started_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"TIMESTAMP {_sql_str(ended_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"{_sql_str(git_sha)}, {_sql_str(workspace)}, "
        f"{_sql_array_str(targets)}, {_sql_array_int(levels)}, "
        f"{int(iterations)}, {int(warmup)}, {_sql_str(notes)})"
    )

    conn = dbsql.connect(
        server_hostname=config["dbsql"]["hostname"],
        http_path=config["dbsql"]["http_path"],
        access_token=config["dbsql"]["token"],
    )
    try:
        cursor = conn.cursor()
        cursor.execute(ddl_runs)
        cursor.execute(ddl_summary)
        cursor.execute(ddl_raw)

        cursor.execute(f"INSERT INTO {runs_tbl} VALUES {runs_values}")
        print(f"  Wrote 1 row to {runs_tbl}")

        if summaries:
            rows = []
            for s in summaries:
                rows.append(
                    f"({_sql_str(run_id)}, {_sql_str(s.target)}, {int(s.concurrency)}, "
                    f"{_sql_str(s.query_name)}, {int(s.total_queries)}, {int(s.successful)}, "
                    f"{int(s.failed)}, {float(s.p50_ms)}, {float(s.p95_ms)}, {float(s.p99_ms)}, "
                    f"{float(s.min_ms)}, {float(s.max_ms)}, {float(s.mean_ms)}, "
                    f"{float(s.throughput_qps)}, {float(s.error_rate)})"
                )
            _insert_in_chunks(cursor, summary_tbl, rows, chunk_size=200)
            print(f"  Wrote {len(rows)} rows to {summary_tbl}")

        if results:
            rows = []
            for r in results:
                rows.append(
                    f"({_sql_str(run_id)}, {_sql_str(r.target)}, {int(r.concurrency)}, "
                    f"{_sql_str(r.query_name)}, {int(r.iteration)}, {int(r.thread_id)}, "
                    f"{float(r.latency_ms)}, {'TRUE' if r.success else 'FALSE'}, "
                    f"{_sql_str(r.error)})"
                )
            _insert_in_chunks(cursor, raw_tbl, rows, chunk_size=500)
            print(f"  Wrote {len(rows)} rows to {raw_tbl}")

        cursor.close()
    finally:
        conn.close()


def _insert_in_chunks(cursor, table: str, rows: list[str], chunk_size: int = 500):
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        stmt = f"INSERT INTO {table} VALUES " + ", ".join(chunk)
        cursor.execute(stmt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def parse_levels(levels_str: str) -> list[int]:
    return [int(x.strip()) for x in levels_str.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Concurrency Benchmark: DBSQL vs Lakebase")
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument("--target", choices=["dbsql", "lakebase", "both"], default="both")
    parser.add_argument("--levels", default=None, help="Comma-separated concurrency levels (overrides config)")
    parser.add_argument("--iterations", type=int, default=None, help="Iterations per level (overrides config)")
    parser.add_argument("--warmup", type=int, default=None, help="Warmup rounds per level (overrides config)")
    parser.add_argument("--output-dir", default="results", help="Output directory for CSV/charts")
    parser.add_argument("--no-chart", action="store_true", help="Skip chart generation")
    parser.add_argument("--skip-delta-write", action="store_true", help="Skip writing to Delta tables")
    parser.add_argument("--notes", default="", help="Free-form notes stored on the benchmark_runs row")
    args = parser.parse_args()

    config = load_config(args.config)

    bench_config = config.get("benchmark", {})
    levels = parse_levels(args.levels) if args.levels else bench_config.get("levels", [1, 2, 4, 8, 16, 32])
    iterations = args.iterations or bench_config.get("iterations", 5)
    warmup = args.warmup if args.warmup is not None else bench_config.get("warmup", 1)
    raw_queries = config["queries"]
    arena_pool = config.get("arena_id_pool", []) or []

    queries = expand_queries_by_weight(raw_queries)

    targets = ["dbsql", "lakebase"] if args.target == "both" else [args.target]
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) / timestamp

    all_results: list[QueryResult] = []
    all_summaries: list[LevelSummary] = []

    mix_counts: dict[str, int] = {}
    for q in queries:
        mix_counts[q["name"]] = mix_counts.get(q["name"], 0) + 1

    print(f"\n{'='*60}")
    print(f"  Concurrency Benchmark")
    print(f"  Run ID:     {run_id}")
    print(f"  Targets:    {', '.join(targets)}")
    print(f"  Levels:     {levels}")
    print(f"  Iterations: {iterations} (+ {warmup} warmup)")
    print(f"  Queries:    {len(raw_queries)} unique -> {len(queries)} weighted slots")
    for name, count in mix_counts.items():
        print(f"              {name}: x{count}")
    print(f"  Arena pool: {len(arena_pool)} arenas" + (f" (e.g. {arena_pool[0]})" if arena_pool else ""))
    print(f"  Output:     {output_dir}")
    print(f"  Delta:      {'SKIPPED' if args.skip_delta_write else 'enabled'}")
    print(f"{'='*60}\n")

    for target in targets:
        print(f"\n--- Target: {target.upper()} ---\n")
        for level in levels:
            print(f"  Level: {level} concurrent connections")
            results, summaries = benchmark_level(
                config, target, queries, level, iterations, warmup,
                arena_pool=arena_pool,
            )
            all_results.extend(results)
            all_summaries.extend(summaries)
            print()

    ended_at = datetime.now(timezone.utc)

    print_summary_table(all_summaries)
    write_csv(all_results, output_dir / "raw_results.csv")
    write_summary_csv(all_summaries, output_dir / "summary.csv")

    if not args.no_chart:
        plot_redline(all_summaries, output_dir / "redline_chart.png")

    if args.skip_delta_write:
        print("Delta persistence skipped (--skip-delta-write).")
    else:
        notes = args.notes or (config.get("delta_persistence", {}) or {}).get("notes", "")
        try:
            print("\nPersisting results to Delta...")
            persist_to_delta(
                config=config, run_id=run_id,
                started_at=started_at, ended_at=ended_at,
                targets=targets, levels=levels,
                iterations=iterations, warmup=warmup, notes=notes,
                results=all_results, summaries=all_summaries,
            )
            print(f"Delta persistence complete for run_id={run_id}")
        except Exception as e:
            print(f"[WARN] Delta persistence failed: {e}")
            print(f"       Local CSV remains the source of truth at: {output_dir}/")

    print(f"\nBenchmark complete. Results in {output_dir}/")


if __name__ == "__main__":
    main()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Results land in Delta
# MAGIC
# MAGIC After each run, the harness writes three tables back to the workspace:
# MAGIC
# MAGIC | Table | Contents |
# MAGIC |---|---|
# MAGIC | `juniper_square_demo_catalog.pipeline.benchmark_runs` | One row per run (start/end, targets, levels) |
# MAGIC | `juniper_square_demo_catalog.pipeline.benchmark_summary` | Per-(target, concurrency, query) stats: p50/p95/p99, mean, QPS, error rate |
# MAGIC | `juniper_square_demo_catalog.pipeline.benchmark_raw` | Every individual query execution (for distribution plots) |
# MAGIC
# MAGIC The **juniper-benchmark-viewer** Databricks App reads from `benchmark_summary` to render the latency and throughput charts on its Data Latency page.
