#!/usr/bin/env bash
# Just the two medallion scenarios. Silver was backfilled separately from
# system.query.history because the harness's Thrift transport deadlocked on
# the long-running silver queries during drain.
set -e

cd "$(dirname "$0")"
source ../.venv/bin/activate

run() {
  scenario="$1"
  echo ""
  echo "=========================================="
  echo "=== $scenario @ $(date) ==="
  echo "=========================================="
  python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark \
    --warehouse-id aae8e7baf626bd0d
  python3 concurrency_benchmark.py \
    --config config_live.yaml \
    --mode sustained \
    --scenario "$scenario" \
    --target dbsql
  echo "--- $scenario done @ $(date) ---"
}

run q8_medallion_peak
run q8_medallion_headroom_2x

echo ""
echo "Medallion scenarios complete @ $(date)"
