#!/usr/bin/env bash
# Run all 4 sustained-rate scenarios sequentially.
# Regenerates config_live.yaml between scenarios so Lakebase OAuth tokens stay fresh
# (Lakebase tokens expire ~1hr; the full run is ~80 min).
#
# Usage: bash run_sustained_all.sh 2>&1 | tee sustained_run_$(date +%Y%m%d_%H%M%S).log

set -e
cd "$(dirname "$0")"
source ../.venv/bin/activate

PROFILE="juniper-square-demo"
PROJECT="juniper-sq-benchmark"

run_scenario() {
    local scenario="$1"
    local target="$2"
    echo ""
    echo "================================================================"
    echo "  SCENARIO: $scenario  (target=$target)"
    echo "  $(date)"
    echo "================================================================"
    python3 gen_config.py --profile "$PROFILE" --project "$PROJECT" 2>&1 | tail -3
    python3 concurrency_benchmark.py \
        --config config_live.yaml \
        --target "$target" \
        --mode sustained \
        --scenario "$scenario" \
        --notes "5/10 redesign — $scenario" \
        --no-chart 2>&1
    echo ""
    echo "[OK] $scenario complete at $(date)"
}

START=$(date +%s)

run_scenario "sustained_peak"            "both"
run_scenario "sustained_headroom_2x"     "both"
run_scenario "sustained_scale_4x"        "both"
run_scenario "sustained_worst_case_q7"   "dbsql"

END=$(date +%s)
ELAPSED=$((END - START))
echo ""
echo "================================================================"
echo "  ALL 4 SCENARIOS COMPLETE"
echo "  Total wall-clock: $((ELAPSED / 60))m $((ELAPSED % 60))s"
echo "================================================================"
