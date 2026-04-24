#!/usr/bin/env bash
# run_benchmark.sh
# One-command execution of the Juniper Square concurrency benchmark.
# Preconditions: SDP pipeline has materialized gold_* and silver_* tables;
# Lakebase has synced gold_* tables via notebooks/06_lakebase_sync_benchmark.py.
#
# Usage:
#   ./run_benchmark.sh                            # smoke test then full matrix
#   ./run_benchmark.sh --smoke-only               # smoke test only
#   WAREHOUSE_ID=133b52f9331b883d ./run_benchmark.sh  # override warehouse

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${PROFILE:-juniper-square-demo}"
PROJECT="${PROJECT:-juniper-sq-benchmark}"
WAREHOUSE_ID="${WAREHOUSE_ID:-133b52f9331b883d}"
SMOKE_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --smoke-only) SMOKE_ONLY=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Activate virtual environment
# ---------------------------------------------------------------------------
VENV_DIR="${SCRIPT_DIR}/../.venv"
if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  echo "ERROR: .venv not found at ${VENV_DIR}" >&2
  echo "Run: cd $(dirname "${SCRIPT_DIR}") && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
echo "[1/5] Virtual environment activated: $(python --version)"

# ---------------------------------------------------------------------------
# 2. Generate config_live.yaml with real arena pool
# ---------------------------------------------------------------------------
echo "[2/5] Generating config_live.yaml with live arena pool..."
python3 "${SCRIPT_DIR}/gen_config.py" \
  --profile "${PROFILE}" \
  --project "${PROJECT}" \
  --warehouse-id "${WAREHOUSE_ID}" \
  --arena-pool-from-workspace \
  --output "${SCRIPT_DIR}/config_live.yaml"

CONFIG="${SCRIPT_DIR}/config_live.yaml"
echo "      Config: ${CONFIG}"

# ---------------------------------------------------------------------------
# 3. Smoke test (1 level, 2 iterations, 1 warmup, DBSQL only, no Delta write)
# ---------------------------------------------------------------------------
echo "[3/5] Running smoke test (1 level x 2 iterations, DBSQL only)..."
python3 "${SCRIPT_DIR}/concurrency_benchmark.py" \
  --config "${CONFIG}" \
  --levels 1 \
  --iterations 2 \
  --warmup 1 \
  --target dbsql \
  --skip-delta-write

echo "      Smoke test passed."

if [[ "${SMOKE_ONLY}" == "true" ]]; then
  echo ""
  echo "Smoke-only mode — stopping here."
  echo "To run the full matrix: ./run_benchmark.sh"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Full benchmark matrix (both targets, writes results to Delta)
# ---------------------------------------------------------------------------
echo "[4/5] Running full benchmark matrix (levels 1,5,10,20 x 20 iterations x both targets)..."
python3 "${SCRIPT_DIR}/concurrency_benchmark.py" \
  --config "${CONFIG}" \
  --levels 1,5,10,20 \
  --iterations 20 \
  --warmup 3 \
  --target both

# ---------------------------------------------------------------------------
# 5. Print run summary
# ---------------------------------------------------------------------------
echo ""
echo "[5/5] Benchmark complete."
echo ""

# Extract run_id from the most recent results directory
LATEST_RESULTS_DIR=$(ls -dt "${SCRIPT_DIR}/results/"*/ 2>/dev/null | head -1)
if [[ -n "${LATEST_RESULTS_DIR}" ]]; then
  RUN_ID=$(basename "${LATEST_RESULTS_DIR}")
  echo "  Run ID      : ${RUN_ID}"
  echo "  Local CSV   : ${LATEST_RESULTS_DIR}summary.csv"
fi

echo "  Delta tables: juniper_square_demo_catalog.pipeline.benchmark_runs"
echo "                juniper_square_demo_catalog.pipeline.benchmark_summary"
echo "                juniper_square_demo_catalog.pipeline.benchmark_raw"
echo ""
echo "To inspect results in DBSQL:"
echo "  SELECT * FROM juniper_square_demo_catalog.pipeline.benchmark_summary ORDER BY run_id DESC, level, target;"
