#!/bin/bash
# Post-deploy helper. Run after `databricks bundle deploy -t <target>` on a fresh workspace.
#
# What it does:
#   1. Looks up the new juniper-benchmark-warehouse ID
#   2. Rewrites app/app.yaml to point at it
#   3. Re-syncs the app + redeploys
#   4. Re-grants CAN_USE on the warehouse to the app's service principal
#
# Why: app.yaml's DATABRICKS_WAREHOUSE_ID env var + sql_warehouse resource binding
# are hardcoded to the original FEVM's warehouse ID. DAB's variable substitution
# doesn't templatize files inside the app source directory, so we patch post-deploy.

set -euo pipefail

PROFILE="${1:-juniper-square-fresh}"
APP_NAME="juniper-benchmark-viewer"
APP_YAML="$(dirname "$0")/../app/app.yaml"

echo ">>> Looking up new warehouse ID under profile=${PROFILE}"
WAREHOUSE_ID=$(
  databricks --profile "${PROFILE}" warehouses list --output json |
  python3 -c "import json,sys; ws=json.load(sys.stdin); print([w['id'] for w in ws if w['name']=='juniper-benchmark-warehouse'][0])"
)
echo "New warehouse_id: ${WAREHOUSE_ID}"

echo ">>> Patching ${APP_YAML}"
# Replace the legacy id wherever it appears in app.yaml
python3 - "$APP_YAML" "$WAREHOUSE_ID" <<'PY'
import sys, re, pathlib
path = pathlib.Path(sys.argv[1])
new_id = sys.argv[2]
text = path.read_text()
# Match any hex16 warehouse id in quotes (handles aae8e7baf626bd0d and any prior id)
patched = re.sub(r'"[a-f0-9]{16}"', f'"{new_id}"', text)
path.write_text(patched)
print("Patched app.yaml")
PY

cat "$APP_YAML"

echo ">>> Re-syncing + redeploying app"
cd "$(dirname "$0")/.."
databricks --profile "${PROFILE}" sync app /Workspace/Users/$(databricks --profile "${PROFILE}" current-user me --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['userName'])")/.bundle/juniper-square-demo/$(basename "$PROFILE" | sed 's/juniper-square-//')/files/app

echo ">>> Re-granting CAN_USE on warehouse to app SP"
SP_ID=$(
  databricks --profile "${PROFILE}" apps get "${APP_NAME}" --output json |
  python3 -c "import json,sys; print(json.load(sys.stdin)['service_principal_client_id'])"
)
databricks --profile "${PROFILE}" api patch "/api/2.0/permissions/warehouses/${WAREHOUSE_ID}" --json "{
  \"access_control_list\":[{
    \"service_principal_name\":\"${SP_ID}\",
    \"permission_level\":\"CAN_USE\"
  }]
}"

echo ">>> Done. Restart the app via: databricks apps stop ${APP_NAME} && databricks apps start ${APP_NAME}"
