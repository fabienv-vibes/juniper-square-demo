#!/usr/bin/env bash
# preflight.sh
#
# Run BEFORE ./run_pipeline.sh. Verifies:
#   1) Each landing subdir (arenas, funds, investors, properties, gl_transactions)
#      has at least one file.
#   2) The target catalog and pipeline schema exist.
#   3) The 3 pipeline notebooks are present at their expected workspace paths.
#
# Exits non-zero and prints what's wrong if any check fails.
#
# Env:
#   DATABRICKS_CLI_PROFILE   defaults to juniper-square-demo

set -euo pipefail

PROFILE="${DATABRICKS_CLI_PROFILE:-juniper-square-demo}"

CATALOG="juniper_square_demo_catalog"
SCHEMA="pipeline"
LANDING_ROOT="dbfs:/Volumes/${CATALOG}/raw/landing"
WORKSPACE_BASE="/Users/fabien.vaucheret@databricks.com/juniper_benchmark/sdp"

SUBDIRS=(arenas funds investors properties gl_transactions)
NOTEBOOKS=(bronze_ingestion silver_transformations gold_aggregations)

FAIL=0

echo "=============================================="
echo "Juniper Square benchmark - SDP preflight"
echo "=============================================="
echo "CLI profile : ${PROFILE}"
echo "Catalog     : ${CATALOG}"
echo "Schema      : ${SCHEMA}"
echo "Landing     : ${LANDING_ROOT}"
echo "Workspace   : ${WORKSPACE_BASE}"
echo

# ---------------------------------------------------------------------------
# Check 1: landing volume subdirs are non-empty
# ---------------------------------------------------------------------------
echo "[1/3] Checking landing volume subdirs for files..."

# Recursively count files under a volume path using `databricks fs ls -r`.
# We count non-empty lines whose final path segment is not empty and which
# aren't obviously directories (the CLI marks dirs with type=DIRECTORY in
# --output json, which is what we use here for accuracy).
count_files_recursive() {
  local path="$1"
  databricks fs ls -r "${path}" --profile "${PROFILE}" --output json 2>/dev/null \
    | jq '[.[] | select(.is_dir == false or .type == "FILE")] | length' 2>/dev/null \
    || echo "0"
}

for SUB in "${SUBDIRS[@]}"; do
  PATH_="${LANDING_ROOT}/${SUB}"
  # Check existence first -- `fs ls -r` on a missing path errors out.
  if ! databricks fs ls "${PATH_}" --profile "${PROFILE}" >/dev/null 2>&1; then
    echo "     MISSING: ${PATH_} (directory does not exist)"
    FAIL=1
    continue
  fi
  COUNT="$(count_files_recursive "${PATH_}")"
  if [[ -z "${COUNT}" || "${COUNT}" == "0" ]]; then
    echo "     EMPTY  : ${PATH_} (0 files)"
    FAIL=1
  else
    printf "     OK     : %-40s %s files\n" "${PATH_}" "${COUNT}"
  fi
done
echo

# ---------------------------------------------------------------------------
# Check 2: catalog + schema exist
# ---------------------------------------------------------------------------
echo "[2/3] Checking catalog and schema exist..."

if databricks catalogs get "${CATALOG}" --profile "${PROFILE}" >/dev/null 2>&1; then
  echo "     OK     : catalog ${CATALOG}"
else
  echo "     MISSING: catalog ${CATALOG}"
  FAIL=1
fi

if databricks schemas get "${CATALOG}.${SCHEMA}" --profile "${PROFILE}" >/dev/null 2>&1; then
  echo "     OK     : schema  ${CATALOG}.${SCHEMA}"
else
  echo "     MISSING: schema  ${CATALOG}.${SCHEMA}"
  FAIL=1
fi
echo

# ---------------------------------------------------------------------------
# Check 3: notebooks uploaded to workspace
# ---------------------------------------------------------------------------
echo "[3/3] Checking notebooks uploaded to workspace..."
for NB in "${NOTEBOOKS[@]}"; do
  WS_PATH="${WORKSPACE_BASE}/${NB}"
  if databricks workspace get-status "${WS_PATH}" --profile "${PROFILE}" >/dev/null 2>&1; then
    echo "     OK     : ${WS_PATH}"
  else
    echo "     MISSING: ${WS_PATH}"
    FAIL=1
  fi
done
echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=============================================="
if [[ "${FAIL}" -eq 0 ]]; then
  echo "Preflight PASSED. Safe to run ./run_pipeline.sh"
  exit 0
else
  echo "Preflight FAILED. Address the items marked MISSING/EMPTY above."
  exit 1
fi
