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
    # Sustained-mode fields (None for lockstep mode)
    scheduled_arrival_offset_ms: Optional[float] = None
    actual_start_offset_ms: Optional[float] = None
    queue_time_ms: Optional[float] = None
    total_latency_ms: Optional[float] = None
    is_warmup: bool = False


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


@dataclass
class TimeseriesBucket:
    """30s (or configurable) bucket of sustained-run samples — feeds the latency-over-time chart."""
    target: str
    query_name: str  # "__all__" for aggregate, or specific query name
    bucket_start_offset_s: int
    bucket_end_offset_s: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    achieved_qps: float
    error_count: int
    is_warmup: bool


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

def expand_queries_by_weight(queries: list[dict], target: str | None = None) -> list[dict]:
    """Expand the query list by each query's 'weight' field (default 1).

    Filters by:
    - `enabled` (default true) — drop queries explicitly disabled
    - `targets` (default [dbsql, lakebase]) — drop queries that don't apply to the
      current target (e.g. Q7 worst-case is `targets: [dbsql]`)

    Example: if Q2 has weight=3 and Q1 has weight=1, the returned list contains
    [Q1, Q2, Q2, Q2]. The harness then round-robins this expanded list across
    threads, simulating a realistic query mix.
    """
    expanded: list[dict] = []
    for q in queries:
        if not q.get("enabled", True):
            continue
        if target is not None:
            allowed = q.get("targets", ["dbsql", "lakebase"])
            if target not in allowed:
                continue
        weight = max(1, int(q.get("weight", 1)))
        expanded.extend([q] * weight)
    return expanded


def substitute_arena_id(sql: str, arena_pool: list[str]) -> str:
    """Replace '{arena_id}' in the SQL with a random arena from the pool.

    If the pool is empty or the placeholder isn't present, returns sql unchanged.
    Literal substitution; no SQL prepared statements.
    """
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
    """Run all queries at a given concurrency level for N iterations.

    `queries` should already be weight-expanded. arena_id substitution happens
    per-execution (each submitted future gets its own random arena).
    """
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
                # Round-robin queries across threads (queries list is already weight-expanded)
                q = queries[thread_id % len(queries)]
                query_sql = q[f"sql_{target}"] if f"sql_{target}" in q else q["sql"]
                # Substitute {arena_id} per execution so each future gets its own arena
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
            # Throughput approximation for lockstep: queries served per worst-case-iteration's
            # wall-clock duration (max latency in iteration ≈ iteration wall time at level=N
            # because all N fired simultaneously). Multiplied by concurrency to estimate
            # aggregate QPS the system would sustain at this level.
            iteration_wall_s = max(latencies) / 1000 if latencies else 1.0
            est_iterations = max(1, total // max(1, concurrency))
            wall_clock_s = max(iteration_wall_s * est_iterations, 0.001)
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
                throughput_qps=len(qr) / wall_clock_s,
                error_rate=failed / total if total > 0 else 0,
            ))

    return all_results, summaries


# ---------------------------------------------------------------------------
# Sustained-rate benchmark mode
# ---------------------------------------------------------------------------

import math
import threading
from concurrent.futures import Future


def _next_arrival_offset_uniform(i: int, rate: float) -> float:
    """Uniform pacing — query i scheduled at i / rate seconds from start."""
    return i / rate


def _build_poisson_arrivals(rate: float, duration_s: float) -> list[float]:
    """Generate a Poisson process arrival schedule, in seconds from t=0.

    Inter-arrival times are exponential with mean 1/rate. The schedule is the
    cumulative sum, truncated to <= duration_s. Done up-front so all arrivals
    are deterministically scheduled before the run starts (avoids coordinated
    omission — see plan).
    """
    arrivals: list[float] = []
    t = 0.0
    while t < duration_s:
        gap = random.expovariate(rate)
        t += gap
        if t < duration_s:
            arrivals.append(t)
    return arrivals


def benchmark_sustained(
    config: dict,
    target: str,
    queries: list[dict],
    rate_qps: float,
    duration_s: int,
    warmup_s: int,
    arrival_distribution: str = "poisson",
    arena_pool: Optional[list[str]] = None,
) -> tuple[list[QueryResult], list[LevelSummary], list[TimeseriesBucket]]:
    """Run sustained-rate workload for fixed wall-clock duration.

    Submitter thread paces arrivals on an independent timeline. Workers run in
    a thread pool sized generously so threads are never the bottleneck. Each
    QueryResult records scheduled vs actual arrival offset and queue time —
    fixes coordinated omission.

    Returns: (raw results, per-query summary, time-series buckets).
    """
    arena_pool = arena_pool or []
    total_duration_s = warmup_s + duration_s

    # Build the full arrival schedule (warmup + measurement) up-front
    if arrival_distribution == "poisson":
        arrivals = _build_poisson_arrivals(rate_qps, total_duration_s)
    elif arrival_distribution == "uniform":
        n = int(rate_qps * total_duration_s)
        arrivals = [_next_arrival_offset_uniform(i, rate_qps) for i in range(n)]
    else:
        raise ValueError(f"Unknown arrival_distribution: {arrival_distribution}")

    print(f"    rate={rate_qps} QPS, duration={duration_s}s + {warmup_s}s warmup, "
          f"{len(arrivals)} arrivals scheduled ({arrival_distribution})")

    # Generous worker pool: never let threads be the bottleneck.
    # Sized for rate * P95-assumption * buffer. Floor at 16, ceiling at 256.
    assumed_p95_s = 5.0
    max_workers = max(16, min(256, int(math.ceil(rate_qps * assumed_p95_s * 3))))

    results: list[QueryResult] = []
    results_lock = threading.Lock()
    base_time = time.perf_counter()

    def _record(res: QueryResult) -> None:
        with results_lock:
            results.append(res)

    def _worker(
        query_name: str,
        query_sql: str,
        scheduled_offset_s: float,
        actual_start_s: float,
        is_warmup: bool,
    ) -> None:
        queue_time_ms = max(0.0, (actual_start_s - scheduled_offset_s) * 1000)
        try:
            if target == "dbsql":
                latency_ms = run_single_query_dbsql(config, query_sql)
            else:
                latency_ms = run_single_query_lakebase(config, query_sql)
            res = QueryResult(
                query_name=query_name,
                target=target,
                concurrency=int(rate_qps),  # for sustained: store target rate as a hint
                iteration=-1,                # not iteration-based; -1 sentinel
                thread_id=-1,                # not thread-indexed
                latency_ms=latency_ms,
                success=True,
                scheduled_arrival_offset_ms=scheduled_offset_s * 1000,
                actual_start_offset_ms=actual_start_s * 1000,
                queue_time_ms=queue_time_ms,
                total_latency_ms=queue_time_ms + latency_ms,
                is_warmup=is_warmup,
            )
        except Exception as e:
            res = QueryResult(
                query_name=query_name,
                target=target,
                concurrency=int(rate_qps),
                iteration=-1,
                thread_id=-1,
                latency_ms=-1,
                success=False,
                error=str(e),
                scheduled_arrival_offset_ms=scheduled_offset_s * 1000,
                actual_start_offset_ms=actual_start_s * 1000,
                queue_time_ms=queue_time_ms,
                total_latency_ms=None,
                is_warmup=is_warmup,
            )
        _record(res)

    progress_marker = max(1, len(arrivals) // 20)
    submitted = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i, scheduled_offset in enumerate(arrivals):
            # Sleep until scheduled wall-clock time
            now = time.perf_counter() - base_time
            if scheduled_offset > now:
                time.sleep(scheduled_offset - now)
            actual_start = time.perf_counter() - base_time
            is_warmup = scheduled_offset < warmup_s

            # Choose query (round-robin over weighted-expanded list)
            q = queries[i % len(queries)]
            query_sql = q[f"sql_{target}"] if f"sql_{target}" in q else q["sql"]
            query_sql = substitute_arena_id(query_sql, arena_pool)

            executor.submit(_worker, q["name"], query_sql, scheduled_offset, actual_start, is_warmup)
            submitted += 1
            if submitted % progress_marker == 0:
                pct = 100.0 * submitted / len(arrivals)
                print(f"      {submitted}/{len(arrivals)} submitted ({pct:.0f}%)", flush=True)

        # Workers drain on context exit
        print(f"    All arrivals submitted; draining worker pool...", flush=True)

    # Summarize: per-query stats over MEASUREMENT samples (exclude warmup)
    summaries: list[LevelSummary] = []
    seen = set()
    measurement_results = [r for r in results if not r.is_warmup]
    for q in queries:
        if q["name"] in seen:
            continue
        seen.add(q["name"])
        qr = [r for r in measurement_results if r.query_name == q["name"] and r.success]
        latencies = [r.total_latency_ms for r in qr if r.total_latency_ms is not None]
        total = sum(1 for r in measurement_results if r.query_name == q["name"])
        failed = total - len(qr)
        if latencies:
            summaries.append(LevelSummary(
                target=target,
                concurrency=int(rate_qps),
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
                throughput_qps=len(qr) / duration_s,
                error_rate=failed / total if total > 0 else 0,
            ))

    # Time-series buckets: 30s windows over the full run (warmup + measurement)
    buckets: list[TimeseriesBucket] = []
    bucket_size_s = 30
    n_buckets = math.ceil(total_duration_s / bucket_size_s)
    for b in range(n_buckets):
        b_start = b * bucket_size_s
        b_end = b_start + bucket_size_s
        is_warmup_bucket = b_end <= warmup_s
        # Aggregate across all queries in this bucket
        bucket_results = [
            r for r in results
            if r.actual_start_offset_ms is not None
            and b_start * 1000 <= r.actual_start_offset_ms < b_end * 1000
        ]
        success_results = [r for r in bucket_results if r.success]
        latencies = [r.total_latency_ms for r in success_results if r.total_latency_ms is not None]
        if not latencies:
            continue
        buckets.append(TimeseriesBucket(
            target=target,
            query_name="__all__",
            bucket_start_offset_s=b_start,
            bucket_end_offset_s=min(b_end, total_duration_s),
            p50_ms=percentile(latencies, 50),
            p95_ms=percentile(latencies, 95),
            max_ms=max(latencies),
            achieved_qps=len(success_results) / bucket_size_s,
            error_count=len(bucket_results) - len(success_results),
            is_warmup=is_warmup_bucket,
        ))

    return results, summaries, buckets


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
    """Write raw results to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "query_name", "target", "concurrency", "iteration",
            "thread_id", "latency_ms", "success", "error",
            "scheduled_arrival_offset_ms", "actual_start_offset_ms",
            "queue_time_ms", "total_latency_ms", "is_warmup",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    print(f"Raw results written to {output_path}")


def write_buckets_csv(buckets: list[TimeseriesBucket], output_path: Path):
    """Write time-series bucket rows to CSV (sustained mode)."""
    if not buckets:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "target", "query_name", "bucket_start_offset_s", "bucket_end_offset_s",
            "p50_ms", "p95_ms", "max_ms", "achieved_qps", "error_count", "is_warmup",
        ])
        writer.writeheader()
        for b in buckets:
            writer.writerow(asdict(b))
    print(f"Time-series buckets written to {output_path}")


def write_summary_csv(summaries: list[LevelSummary], output_path: Path):
    """Write level summaries to CSV."""
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

    # Left: P95 latency vs concurrency
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

    # Right: Throughput (QPS) vs concurrency
    ax2 = axes[1]
    for target in targets:
        # Aggregate QPS across all queries at each concurrency level
        concurrency_levels = sorted(set(
            s.concurrency for s in summaries if s.target == target
        ))
        for level in concurrency_levels:
            level_summaries = [
                s for s in summaries
                if s.target == target and s.concurrency == level
            ]
        agg_qps = {}
        for level in concurrency_levels:
            level_sums = [
                s for s in summaries
                if s.target == target and s.concurrency == level
            ]
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
    """Return the current HEAD short SHA, or empty string if not in a git repo."""
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
    """Escape a Python value for literal SQL inclusion. Basic escaping only."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    # Strings: single-quote, escape internal single quotes
    return "'" + str(val).replace("'", "''") + "'"


def _sql_array_str(vals: list[str]) -> str:
    """Build a SQL ARRAY(...) literal of strings."""
    if not vals:
        return "ARRAY()"
    return "ARRAY(" + ", ".join(_sql_str(v) for v in vals) + ")"


def _sql_array_int(vals: list[int]) -> str:
    """Build a SQL ARRAY(...) literal of ints."""
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
    mode: str = "lockstep",
    rate_qps: Optional[float] = None,
    arrival_distribution: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    warmup_seconds: Optional[int] = None,
    buckets: Optional[list["TimeseriesBucket"]] = None,
):
    """Write benchmark_runs / benchmark_summary / benchmark_raw / benchmark_summary_timeseries.

    Uses CREATE TABLE IF NOT EXISTS + INSERT INTO VALUES. Best-effort; the
    caller wraps this in a try/except so a failure here doesn't wipe the CSV.

    Sustained-mode params (mode, rate_qps, arrival_distribution, duration_seconds,
    warmup_seconds, buckets) are written when present; in lockstep mode they're NULL.
    """
    from databricks import sql as dbsql

    dp = config.get("delta_persistence", {}) or {}
    catalog = dp.get("catalog", "juniper_square_demo_catalog")
    schema = dp.get("schema", "pipeline")
    fq = f"{catalog}.{schema}"

    runs_tbl = f"{fq}.benchmark_runs"
    summary_tbl = f"{fq}.benchmark_summary"
    raw_tbl = f"{fq}.benchmark_raw"
    timeseries_tbl = f"{fq}.benchmark_summary_timeseries"

    # New sustained columns are added via ALTER (already migrated 2026-04-28).
    # CREATE TABLE IF NOT EXISTS is a no-op when tables exist; the existing
    # tables already have the post-migration schema.
    ddl_runs = f"""
    CREATE TABLE IF NOT EXISTS {runs_tbl} (
      run_id STRING,
      started_at TIMESTAMP,
      ended_at TIMESTAMP,
      git_sha STRING,
      workspace STRING,
      targets ARRAY<STRING>,
      concurrency_levels ARRAY<INT>,
      iterations INT,
      warmup INT,
      notes STRING,
      mode STRING,
      target_rate_qps DOUBLE,
      arrival_distribution STRING,
      duration_seconds INT,
      warmup_seconds INT
    ) USING DELTA
    """.strip()

    ddl_summary = f"""
    CREATE TABLE IF NOT EXISTS {summary_tbl} (
      run_id STRING,
      target STRING,
      concurrency INT,
      query_name STRING,
      total_queries INT,
      successful INT,
      failed INT,
      p50_ms DOUBLE,
      p95_ms DOUBLE,
      p99_ms DOUBLE,
      min_ms DOUBLE,
      max_ms DOUBLE,
      mean_ms DOUBLE,
      throughput_qps DOUBLE,
      error_rate DOUBLE
    ) USING DELTA
    """.strip()

    ddl_raw = f"""
    CREATE TABLE IF NOT EXISTS {raw_tbl} (
      run_id STRING,
      target STRING,
      concurrency INT,
      query_name STRING,
      iteration INT,
      thread_id INT,
      latency_ms DOUBLE,
      success BOOLEAN,
      error STRING,
      scheduled_arrival_offset_ms DOUBLE,
      actual_start_offset_ms DOUBLE,
      queue_time_ms DOUBLE,
      total_latency_ms DOUBLE,
      is_warmup BOOLEAN
    ) USING DELTA
    """.strip()

    ddl_timeseries = f"""
    CREATE TABLE IF NOT EXISTS {timeseries_tbl} (
      run_id STRING,
      target STRING,
      query_name STRING,
      bucket_start_offset_s INT,
      bucket_end_offset_s INT,
      p50_ms DOUBLE,
      p95_ms DOUBLE,
      max_ms DOUBLE,
      achieved_qps DOUBLE,
      error_count INT,
      is_warmup BOOLEAN
    ) USING DELTA
    """.strip()

    workspace = config.get("dbsql", {}).get("hostname", "")
    git_sha = _git_sha()

    # Build runs row (with sustained columns)
    rate_sql = "NULL" if rate_qps is None else f"{float(rate_qps)}"
    duration_sql = "NULL" if duration_seconds is None else f"{int(duration_seconds)}"
    warmup_sec_sql = "NULL" if warmup_seconds is None else f"{int(warmup_seconds)}"

    runs_values = (
        f"({_sql_str(run_id)}, "
        f"TIMESTAMP {_sql_str(started_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"TIMESTAMP {_sql_str(ended_at.strftime('%Y-%m-%d %H:%M:%S'))}, "
        f"{_sql_str(git_sha)}, "
        f"{_sql_str(workspace)}, "
        f"{_sql_array_str(targets)}, "
        f"{_sql_array_int(levels)}, "
        f"{int(iterations)}, "
        f"{int(warmup)}, "
        f"{_sql_str(notes)}, "
        f"{_sql_str(mode)}, "
        f"{rate_sql}, "
        f"{_sql_str(arrival_distribution)}, "
        f"{duration_sql}, "
        f"{warmup_sec_sql})"
    )

    conn = dbsql.connect(
        server_hostname=config["dbsql"]["hostname"],
        http_path=config["dbsql"]["http_path"],
        access_token=config["dbsql"]["token"],
    )
    def _opt_double(v):
        return "NULL" if v is None else f"{float(v)}"

    try:
        cursor = conn.cursor()
        cursor.execute(ddl_runs)
        cursor.execute(ddl_summary)
        cursor.execute(ddl_raw)
        cursor.execute(ddl_timeseries)

        cursor.execute(f"INSERT INTO {runs_tbl} VALUES {runs_values}")
        print(f"  Wrote 1 row to {runs_tbl}")

        # Insert summary rows in chunks
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

        # Insert raw rows in chunks (now includes sustained columns)
        if results:
            rows = []
            for r in results:
                rows.append(
                    f"({_sql_str(run_id)}, {_sql_str(r.target)}, {int(r.concurrency)}, "
                    f"{_sql_str(r.query_name)}, {int(r.iteration)}, {int(r.thread_id)}, "
                    f"{float(r.latency_ms)}, {'TRUE' if r.success else 'FALSE'}, "
                    f"{_sql_str(r.error)}, "
                    f"{_opt_double(r.scheduled_arrival_offset_ms)}, "
                    f"{_opt_double(r.actual_start_offset_ms)}, "
                    f"{_opt_double(r.queue_time_ms)}, "
                    f"{_opt_double(r.total_latency_ms)}, "
                    f"{'TRUE' if r.is_warmup else 'FALSE'})"
                )
            _insert_in_chunks(cursor, raw_tbl, rows, chunk_size=500)
            print(f"  Wrote {len(rows)} rows to {raw_tbl}")

        # Insert sustained-mode time-series buckets
        if buckets:
            rows = []
            for b in buckets:
                rows.append(
                    f"({_sql_str(run_id)}, {_sql_str(b.target)}, {_sql_str(b.query_name)}, "
                    f"{int(b.bucket_start_offset_s)}, {int(b.bucket_end_offset_s)}, "
                    f"{float(b.p50_ms)}, {float(b.p95_ms)}, {float(b.max_ms)}, "
                    f"{float(b.achieved_qps)}, {int(b.error_count)}, "
                    f"{'TRUE' if b.is_warmup else 'FALSE'})"
                )
            _insert_in_chunks(cursor, timeseries_tbl, rows, chunk_size=200)
            print(f"  Wrote {len(rows)} rows to {timeseries_tbl}")

        cursor.close()
    finally:
        conn.close()


def _insert_in_chunks(cursor, table: str, rows: list[str], chunk_size: int = 500):
    """Execute INSERT INTO ... VALUES (...), (...), ... in chunks."""
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        stmt = f"INSERT INTO {table} VALUES " + ", ".join(chunk)
        cursor.execute(stmt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load YAML config file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def parse_levels(levels_str: str) -> list[int]:
    """Parse comma-separated concurrency levels."""
    return [int(x.strip()) for x in levels_str.split(",")]


def main():
    parser = argparse.ArgumentParser(description="Concurrency Benchmark: DBSQL vs Lakebase")
    parser.add_argument("--config", required=True, help="Path to config YAML")
    parser.add_argument("--target", choices=["dbsql", "lakebase", "both"], default="both")
    parser.add_argument(
        "--mode",
        choices=["lockstep", "sustained"],
        default="lockstep",
        help="lockstep: fire N concurrent then wait (current default). "
             "sustained: pace arrivals at target QPS for fixed wall-clock duration.",
    )
    # Lockstep-specific
    parser.add_argument("--levels", default=None, help="[lockstep] Comma-separated concurrency levels")
    parser.add_argument("--iterations", type=int, default=None, help="[lockstep] Iterations per level")
    parser.add_argument("--warmup", type=int, default=None, help="[lockstep] Warmup rounds per level")
    # Sustained-specific
    parser.add_argument("--scenario", default=None,
                        help="[sustained] Named scenario from config.yaml sustained_scenarios (e.g. sustained_peak)")
    parser.add_argument("--rate", type=float, default=None,
                        help="[sustained] Target arrival rate in QPS (overrides scenario)")
    parser.add_argument("--duration", type=int, default=None,
                        help="[sustained] Measurement duration in seconds (overrides scenario)")
    parser.add_argument("--warmup-seconds", type=int, default=None,
                        help="[sustained] Warmup duration in seconds (overrides scenario)")
    parser.add_argument("--arrival", choices=["poisson", "uniform"], default=None,
                        help="[sustained] Arrival distribution (overrides scenario, default poisson)")
    parser.add_argument("--query-filter", default=None,
                        help="[sustained] Comma-separated query names to include (filters dashboard mix). "
                             "Example: --query-filter worst_case_yoy_growth for Q7-only run.")
    # Common
    parser.add_argument("--output-dir", default="results", help="Output directory for CSV/charts")
    parser.add_argument("--no-chart", action="store_true", help="Skip chart generation")
    parser.add_argument(
        "--skip-delta-write",
        action="store_true",
        help="Skip writing benchmark_runs / benchmark_summary / benchmark_raw to DBSQL",
    )
    parser.add_argument("--notes", default="", help="Free-form notes stored on the benchmark_runs row")
    args = parser.parse_args()

    config = load_config(args.config)

    # Resolve parameters (CLI overrides > scenario > config > defaults)
    bench_config = config.get("benchmark", {})
    levels = parse_levels(args.levels) if args.levels else bench_config.get("levels", [1, 2, 4, 8, 16, 32])
    iterations = args.iterations or bench_config.get("iterations", 5)
    warmup = args.warmup if args.warmup is not None else bench_config.get("warmup", 1)
    raw_queries = config["queries"]
    arena_pool = config.get("arena_id_pool", []) or []

    # Sustained-mode parameter resolution from named scenario + CLI overrides
    sustained_scenarios = config.get("sustained_scenarios", {}) or {}
    scenario_cfg: dict = {}
    if args.mode == "sustained":
        scenario_name = args.scenario or "sustained_peak"
        scenario_cfg = sustained_scenarios.get(scenario_name, {})
        if not scenario_cfg and not (args.rate and args.duration):
            print(f"[ERROR] sustained mode requires either --scenario <name> "
                  f"or both --rate and --duration. "
                  f"Available scenarios: {list(sustained_scenarios.keys())}")
            sys.exit(1)

    # Targets resolved first; per-target query expansion happens inside the loop
    # so per-query `targets` filtering can scope queries to the right backends.
    targets = ["dbsql", "lakebase"] if args.target == "both" else [args.target]
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) / timestamp

    all_results: list[QueryResult] = []
    all_summaries: list[LevelSummary] = []
    all_buckets: list[TimeseriesBucket] = []

    # Sustained-mode resolved parameters (from CLI > scenario > defaults)
    if args.mode == "sustained":
        rate_qps = args.rate if args.rate is not None else scenario_cfg.get("rate_qps", 5.0)
        duration_s = args.duration if args.duration is not None else scenario_cfg.get("duration_seconds", 600)
        warmup_seconds = (args.warmup_seconds if args.warmup_seconds is not None
                          else scenario_cfg.get("warmup_seconds", 90))
        arrival_dist = args.arrival or scenario_cfg.get("arrival_distribution", "poisson")
        scenario_query_filter = scenario_cfg.get("query_filter")
        cli_query_filter = args.query_filter.split(",") if args.query_filter else None
        query_filter = cli_query_filter or scenario_query_filter
    else:
        rate_qps = None
        duration_s = None
        warmup_seconds = None
        arrival_dist = None
        query_filter = None

    # Log the weighted expansion (using all targets) so we can confirm the mix
    mix_counts: dict[str, int] = {}
    for q in expand_queries_by_weight(raw_queries):
        mix_counts[q["name"]] = mix_counts.get(q["name"], 0) + 1

    print(f"\n{'='*60}")
    print(f"  Concurrency Benchmark — mode={args.mode}")
    print(f"  Run ID:     {run_id}")
    print(f"  Targets:    {', '.join(targets)}")
    if args.mode == "lockstep":
        print(f"  Levels:     {levels}")
        print(f"  Iterations: {iterations} (+ {warmup} warmup)")
    else:
        scenario_lbl = args.scenario or "(custom)"
        print(f"  Scenario:   {scenario_lbl}")
        print(f"  Rate:       {rate_qps} QPS ({arrival_dist})")
        print(f"  Duration:   {duration_s}s + {warmup_seconds}s warmup")
        if query_filter:
            print(f"  Query filter: {query_filter}")
    enabled_count = sum(1 for q in raw_queries if q.get("enabled", True))
    print(f"  Queries:    {len(raw_queries)} declared / {enabled_count} enabled (per-target slot count printed below)")
    for name, count in mix_counts.items():
        print(f"              {name}: x{count}")
    print(f"  Arena pool: {len(arena_pool)} arenas" + (f" (e.g. {arena_pool[0]})" if arena_pool else ""))
    print(f"  Output:     {output_dir}")
    print(f"  Delta:      {'SKIPPED' if args.skip_delta_write else 'enabled'}")
    print(f"{'='*60}\n")

    for target in targets:
        # Per-target expansion: applies enabled + targets filter for this target
        target_queries = expand_queries_by_weight(raw_queries, target=target)
        # Sustained query_filter further scopes the mix (e.g. Q7-only)
        if query_filter:
            target_queries = [q for q in target_queries if q["name"] in query_filter]
        if not target_queries:
            print(f"\n--- Target: {target.upper()} ---  (no enabled queries — skipping)\n")
            continue
        target_mix = ", ".join(sorted({q["name"] for q in target_queries}))
        print(f"\n--- Target: {target.upper()} ---  ({len(target_queries)} weighted slots: {target_mix})\n")

        if args.mode == "lockstep":
            for level in levels:
                print(f"  Level: {level} concurrent connections")
                results, summaries = benchmark_level(
                    config, target, target_queries, level, iterations, warmup,
                    arena_pool=arena_pool,
                )
                all_results.extend(results)
                all_summaries.extend(summaries)
                print()
        else:  # sustained
            results, summaries, buckets = benchmark_sustained(
                config, target, target_queries,
                rate_qps=rate_qps,
                duration_s=duration_s,
                warmup_s=warmup_seconds,
                arrival_distribution=arrival_dist,
                arena_pool=arena_pool,
            )
            all_results.extend(results)
            all_summaries.extend(summaries)
            all_buckets.extend(buckets)
            print()

    ended_at = datetime.now(timezone.utc)

    # Output (CSV always written first, so it remains source of truth if Delta fails)
    print_summary_table(all_summaries)
    write_csv(all_results, output_dir / "raw_results.csv")
    write_summary_csv(all_summaries, output_dir / "summary.csv")
    if all_buckets:
        write_buckets_csv(all_buckets, output_dir / "timeseries_buckets.csv")

    if not args.no_chart and args.mode == "lockstep":
        plot_redline(all_summaries, output_dir / "redline_chart.png")

    # Delta persistence (after local CSV, and only if not skipped)
    if args.skip_delta_write:
        print("Delta persistence skipped (--skip-delta-write).")
    else:
        notes = args.notes or (config.get("delta_persistence", {}) or {}).get("notes", "")
        if args.mode == "sustained":
            scenario_label = args.scenario or f"custom_{rate_qps}qps_{duration_s}s"
            notes = (notes + f" | sustained scenario={scenario_label}").strip(" |")
        try:
            print("\nPersisting results to Delta...")
            persist_to_delta(
                config=config,
                run_id=run_id,
                started_at=started_at,
                ended_at=ended_at,
                targets=targets,
                levels=levels if args.mode == "lockstep" else [],
                iterations=iterations if args.mode == "lockstep" else 0,
                warmup=warmup if args.mode == "lockstep" else 0,
                notes=notes,
                results=all_results,
                summaries=all_summaries,
                mode=args.mode,
                rate_qps=rate_qps,
                arrival_distribution=arrival_dist,
                duration_seconds=duration_s,
                warmup_seconds=warmup_seconds,
                buckets=all_buckets,
            )
            print(f"Delta persistence complete for run_id={run_id}")
        except Exception as e:
            print(f"[WARN] Delta persistence failed: {e}")
            print("       Local CSV remains the source of truth at:")
            print(f"       {output_dir}/")

    print(f"\nBenchmark complete. Results in {output_dir}/")


if __name__ == "__main__":
    main()
