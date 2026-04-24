# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Deploy Fund Analyst Agent
# MAGIC Register and deploy the agent to a serving endpoint for AI Playground testing.

# COMMAND ----------

CATALOG = "juniper_square_demo"
SCHEMA = "pipeline"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.fund_analyst_agent"
ENDPOINT_NAME = "juniper-square-fund-analyst"

# COMMAND ----------

from databricks import agents

deployment = agents.deploy(
    MODEL_NAME,
    model_version=1,
    endpoint_name=ENDPOINT_NAME,
    tags={"demo": "juniper-square", "initiative": "2"},
)
print(f"Endpoint: {deployment.endpoint_name}")
print(f"Query URL: {deployment.query_endpoint}")

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

print("Waiting for endpoint to be ready...")
while True:
    endpoint = w.serving_endpoints.get(ENDPOINT_NAME)
    state = endpoint.state.ready
    print(f"  State: {state}")
    if state == "READY":
        print("Endpoint is ready!")
        break
    time.sleep(30)

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

response = w.serving_endpoints.query(
    name=ENDPOINT_NAME,
    input={
        "input": [{"role": "user", "content": "What are the top 5 funds by AUM?"}]
    }
)
print(response.output)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test in AI Playground
# MAGIC Open the AI Playground to interactively test the agent:
# MAGIC - Navigate to **Machine Learning > Serving > juniper-square-fund-analyst > AI Playground**
# MAGIC - Try these questions:
# MAGIC   1. "What is the total AUM across all funds?"
# MAGIC   2. "Which properties have the highest NOI?"
# MAGIC   3. "Compare capex vs opex spending for opportunistic funds in 2025"
# MAGIC   4. "What is the average occupancy rate for multifamily vs industrial properties?"
