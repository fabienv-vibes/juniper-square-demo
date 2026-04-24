#!/usr/bin/env bash
# deploy_pipeline.sh
#
# Idempotent deploy for the Juniper Square benchmark medallion SDP pipeline.
# 1) Uploads the 3 pipeline notebooks to the workspace
# 2) Creates the pipeline (or updates it if it already exists)
# 3) Prints the pipeline ID and monitoring URL
#
# Does NOT trigger a run -- see the commented `start-update` line at the bottom,
# or use ./run_pipeline.sh once calibration data is landed.

set -euo pipefail

PROFILE="${DATABRICKS_CLI_PROFILE:-juniper-square-demo}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPEC_FILE="${SCRIPT_DIR}/pipeline_spec.json"

PIPELINE_NAME="juniper_benchmark_medallion"
WORKSPACE_BASE="/Users/fabien.vaucheret@databricks.com/juniper_benchmark/sdp"

NOTEBOOKS=(
  "bronze_ingestion"
  "silver_transformations"
  "gold_aggregations"
)

echo "=============================================="
echo "Juniper Square benchmark - SDP pipeline deploy"
echo "=============================================="
echo "CLI profile      : ${PROFILE}"
echo "Pipeline name    : ${PIPELINE_NAME}"
echo "Workspace target : ${WORKSPACE_BASE}"
echo "Spec file        : ${SPEC_FILE}"
echo

# ---------------------------------------------------------------------------
# Step 1: ensure the workspace parent directory exists, then upload notebooks
# ---------------------------------------------------------------------------
echo "[1/3] Ensuring workspace directory exists: ${WORKSPACE_BASE}"
databricks workspace mkdirs "${WORKSPACE_BASE}" --profile "${PROFILE}" >/dev/null 2>&1 || true

echo "[1/3] Uploading notebooks..."
for NB in "${NOTEBOOKS[@]}"; do
  SRC="${SCRIPT_DIR}/${NB}.py"
  DEST="${WORKSPACE_BASE}/${NB}"
  if [[ ! -f "${SRC}" ]]; then
    echo "  ERROR: local notebook not found: ${SRC}" >&2
    exit 1
  fi
  echo "  -> ${SRC}"
  echo "     ${DEST}"
  databricks workspace import \
    --overwrite \
    --language PYTHON \
    --format SOURCE \
    --file "${SRC}" \
    "${DEST}" \
    --profile "${PROFILE}"
done
echo "     All notebooks uploaded."
echo

# ---------------------------------------------------------------------------
# Step 2: look up existing pipeline by name (LIKE match)
# ---------------------------------------------------------------------------
echo "[2/3] Checking whether pipeline '${PIPELINE_NAME}' already exists..."
EXISTING_JSON="$(databricks pipelines list-pipelines \
  --filter "name LIKE '${PIPELINE_NAME}'" \
  --profile "${PROFILE}" \
  --output json 2>/dev/null || echo '[]')"

# Extract the pipeline_id of an exact-name match (LIKE is a prefix/wildcard filter,
# so we also filter client-side for exact match).
PIPELINE_ID="$(printf '%s' "${EXISTING_JSON}" \
  | jq -r --arg n "${PIPELINE_NAME}" \
      '[.[] | select(.name == $n)] | .[0].pipeline_id // empty')"

# ---------------------------------------------------------------------------
# Step 3: create or update using the JSON spec
# ---------------------------------------------------------------------------
if [[ -z "${PIPELINE_ID}" ]]; then
  echo "     No existing pipeline found. Creating a new one."
  echo "[3/3] Running: databricks pipelines create --json @${SPEC_FILE}"
  CREATE_OUT="$(databricks pipelines create \
    --json "@${SPEC_FILE}" \
    --profile "${PROFILE}" \
    --output json)"
  PIPELINE_ID="$(printf '%s' "${CREATE_OUT}" | jq -r '.pipeline_id')"
  echo "     Created pipeline. pipeline_id=${PIPELINE_ID}"
else
  echo "     Existing pipeline found. pipeline_id=${PIPELINE_ID}"
  echo "[3/3] Running: databricks pipelines update ${PIPELINE_ID} --json @${SPEC_FILE}"
  databricks pipelines update "${PIPELINE_ID}" \
    --json "@${SPEC_FILE}" \
    --profile "${PROFILE}" >/dev/null
  echo "     Pipeline updated."
fi
echo

# ---------------------------------------------------------------------------
# Resolve host to print a monitoring URL
# ---------------------------------------------------------------------------
HOST="$(databricks auth describe --profile "${PROFILE}" --output json 2>/dev/null \
  | jq -r '.details.host // .host // empty' | sed 's:/*$::')"
if [[ -n "${HOST}" ]]; then
  UI_URL="${HOST}/#joblist/pipelines/${PIPELINE_ID}"
else
  UI_URL="(set DATABRICKS_HOST or check your CLI profile to get a clickable URL) pipeline_id=${PIPELINE_ID}"
fi

echo "=============================================="
echo "Deploy complete."
echo "  Pipeline name : ${PIPELINE_NAME}"
echo "  Pipeline ID   : ${PIPELINE_ID}"
echo "  UI            : ${UI_URL}"
echo "=============================================="
echo
echo "To trigger a run once calibration data has landed:"
echo "  ./run_pipeline.sh"
echo
echo "Or directly via the CLI:"
echo "  # databricks pipelines start-update ${PIPELINE_ID} --profile ${PROFILE}"
