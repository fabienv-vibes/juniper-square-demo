#!/usr/bin/env bash
# Re-run Q8 sustained scenarios to populate benchmark_raw with strip-plot-grade samples.
# - q8_silver_long:           1 QPS,  30 min  (~75 samples after warmup)
# - q8_medallion_peak:        5 QPS,  10 min  (~3000 samples)
# - q8_medallion_headroom_2x: 10 QPS, 10 min  (~6000 samples)
# Total wall clock ~50 min + warmups ~5 min + per-scenario gen_config overhead.
set -e

cd "$(dirname "$0")"
source ../.venv/bin/activate

run() {
  scenario="$1"
  echo ""
  echo "=========================================="
  echo "=== $scenario @ $(date) ==="
  echo "=========================================="
  # Pin the benchmark warehouse explicitly — gen_config picks "first RUNNING"
  # which can wrongly resolve to Serverless Starter if it's been woken up.
  python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark \
    --warehouse-id aae8e7baf626bd0d
  python3 concurrency_benchmark.py \
    --config config_live.yaml \
    --mode sustained \
    --scenario "$scenario" \
    --target dbsql
  echo "--- $scenario done @ $(date) ---"
}

run q8_silver_long
run q8_medallion_peak
run q8_medallion_headroom_2x

echo ""
echo "All 3 Q8 re-run scenarios complete @ $(date)"
echo "Verify with:"
echo "  SELECT query_name, COUNT(*) FROM juniper_square_demo_catalog.pipeline.benchmark_raw"
echo "  WHERE query_name IN ('q8_shape','q8_refactored') GROUP BY query_name;"
