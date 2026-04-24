#!/usr/bin/env bash
# run_pipeline.sh
#
# Triggers an update on the juniper_benchmark_medallion SDP pipeline and polls
# until it reaches a terminal state. Prints state transitions as they occur.
#
# Usage:
#   ./run_pipeline.sh
#   PIPELINE_ID=<id> ./run_pipeline.sh
#
# Env:
#   DATABRICKS_CLI_PROFILE   defaults to juniper-square-demo
#   PIPELINE_ID              optional; if unset, looked up by name
#   POLL_INTERVAL_SECONDS    defaults to 30

set -euo pipefail

PROFILE="${DATABRICKS_CLI_PROFILE:-juniper-square-demo}"
PIPELINE_NAME="juniper_benchmark_medallion"
POLL_INTERVAL="${POLL_INTERVAL_SECONDS:-30}"

echo "=============================================="
echo "Juniper Square benchmark - SDP pipeline run"
echo "=============================================="
echo "CLI profile : ${PROFILE}"
echo "Poll every  : ${POLL_INTERVAL}s"
echo

# ---------------------------------------------------------------------------
# Step 1: resolve PIPELINE_ID
# ---------------------------------------------------------------------------
if [[ -z "${PIPELINE_ID:-}" ]]; then
  echo "[1/4] Looking up pipeline '${PIPELINE_NAME}'..."
  LIST_JSON="$(databricks pipelines list-pipelines \
    --filter "name LIKE '${PIPELINE_NAME}'" \
    --profile "${PROFILE}" \
    --output json 2>/dev/null || echo '[]')"
  PIPELINE_ID="$(printf '%s' "${LIST_JSON}" \
    | jq -r --arg n "${PIPELINE_NAME}" \
        '[.[] | select(.name == $n)] | .[0].pipeline_id // empty')"
  if [[ -z "${PIPELINE_ID}" ]]; then
    echo "ERROR: pipeline '${PIPELINE_NAME}' not found on profile '${PROFILE}'." >&2
    echo "       Run ./deploy_pipeline.sh first." >&2
    exit 1
  fi
fi
echo "     pipeline_id = ${PIPELINE_ID}"
echo

# ---------------------------------------------------------------------------
# Step 2: trigger an update
# ---------------------------------------------------------------------------
echo "[2/4] Starting a pipeline update..."
START_JSON="$(databricks pipelines start-update "${PIPELINE_ID}" \
  --profile "${PROFILE}" \
  --output json)"
UPDATE_ID="$(printf '%s' "${START_JSON}" | jq -r '.update_id')"
if [[ -z "${UPDATE_ID}" || "${UPDATE_ID}" == "null" ]]; then
  echo "ERROR: failed to start update. Raw response:" >&2
  printf '%s\n' "${START_JSON}" >&2
  exit 1
fi
echo "     update_id = ${UPDATE_ID}"
echo

# ---------------------------------------------------------------------------
# Step 3: poll until terminal state
# ---------------------------------------------------------------------------
echo "[3/4] Polling update state every ${POLL_INTERVAL}s..."
LAST_STATE=""
LAST_DETAIL=""

# Terminal states per Databricks REST API for pipeline updates
is_terminal() {
  case "$1" in
    COMPLETED|FAILED|CANCELED) return 0 ;;
    *) return 1 ;;
  esac
}

while true; do
  GET_JSON="$(databricks pipelines get-update "${PIPELINE_ID}" "${UPDATE_ID}" \
    --profile "${PROFILE}" \
    --output json 2>/dev/null || echo '{}')"

  STATE="$(printf '%s' "${GET_JSON}" | jq -r '.update.state // empty')"
  # A best-effort "current stage" hint: most recent event level=INFO message.
  # Not all SDP updates expose a structured stage field on the update object.
  DETAIL="$(printf '%s' "${GET_JSON}" | jq -r '.update.cause // empty')"

  if [[ -z "${STATE}" ]]; then
    echo "     (no state yet) $(date '+%H:%M:%S')"
  elif [[ "${STATE}" != "${LAST_STATE}" || "${DETAIL}" != "${LAST_DETAIL}" ]]; then
    TS="$(date '+%H:%M:%S')"
    if [[ -n "${DETAIL}" && "${DETAIL}" != "null" ]]; then
      echo "     [${TS}] state=${STATE} cause=${DETAIL}"
    else
      echo "     [${TS}] state=${STATE}"
    fi
    LAST_STATE="${STATE}"
    LAST_DETAIL="${DETAIL}"
  fi

  if [[ -n "${STATE}" ]] && is_terminal "${STATE}"; then
    break
  fi
  sleep "${POLL_INTERVAL}"
done
echo

# ---------------------------------------------------------------------------
# Step 4: report outcome
# ---------------------------------------------------------------------------
echo "[4/4] Final state: ${LAST_STATE}"

case "${LAST_STATE}" in
  COMPLETED)
    echo "Pipeline update COMPLETED successfully."
    exit 0
    ;;
  FAILED|CANCELED)
    echo "Pipeline update ${LAST_STATE}. Fetching first ERROR event..."
    EVENTS_JSON="$(databricks pipelines list-pipeline-events "${PIPELINE_ID}" \
      --filter "update_id = '${UPDATE_ID}' AND level = 'ERROR'" \
      --max-results 5 \
      --profile "${PROFILE}" \
      --output json 2>/dev/null || echo '{}')"

    # The CLI returns either an object with .events or a raw array depending on
    # version. Normalize both shapes with jq.
    printf '%s' "${EVENTS_JSON}" | jq -r '
      (.events // . // [])
      | if type == "array" then . else [] end
      | .[0:3]
      | .[]
      | "---\n[\(.timestamp // "?")] level=\(.level // "?") type=\(.event_type // "?")\n\(.message // "(no message)")\n"
    ' 2>/dev/null || printf '%s\n' "${EVENTS_JSON}"
    exit 1
    ;;
  *)
    echo "Unexpected terminal state: ${LAST_STATE}"
    exit 1
    ;;
esac
