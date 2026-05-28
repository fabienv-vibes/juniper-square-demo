#!/usr/bin/env bash
# Run the 4 Q8 sustained scenarios sequentially, refreshing OAuth between each.
# Usage: ./run_q8_benchmark.sh
set -e

cd "$(dirname "$0")"

source ../.venv/bin/activate

run() {
  scenario="$1"
  echo "=== $scenario ==="
  python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark
  python3 concurrency_benchmark.py \
    --config config_live.yaml \
    --mode sustained \
    --scenario "$scenario" \
    --target dbsql
  echo "--- $scenario done ---"
}

run sustained_q8a_shape
run sustained_q8a_refactored
run sustained_q8b_shape
run sustained_q8b_refactored

echo ""
echo "All 4 Q8 sustained scenarios complete."
echo "Check benchmark_summary for q8a_shape / q8a_refactored / q8b_shape / q8b_refactored rows."
