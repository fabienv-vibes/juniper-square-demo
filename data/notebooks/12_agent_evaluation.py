# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Agent Evaluation with MLflow
# MAGIC Systematically evaluate the Fund Analyst agent using MLflow GenAI evaluation.
# MAGIC This addresses Juniper Square's concern about inconsistent AI answers.

# COMMAND ----------

# MAGIC %pip install -U mlflow>=3.1 databricks-agents
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import mlflow

CATALOG = "juniper_square_demo"
SCHEMA = "pipeline"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.fund_analyst_agent"
ENDPOINT_NAME = "juniper-square-fund-analyst"

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/fabien.vaucheret@databricks.com/juniper-square-demo/agent-evaluation")

# COMMAND ----------

import pandas as pd

eval_data = pd.DataFrame([
    {
        "inputs": "What is the total AUM across all funds?",
        "expected_response": "The total AUM across all funds is approximately $34.2 billion."
    },
    {
        "inputs": "How many funds use the core strategy?",
        "expected_response": "There are 20 funds using the core strategy."
    },
    {
        "inputs": "What are the top 3 properties by revenue?",
        "expected_response": "The agent should query property financials, aggregate revenue, and return the top 3 properties with their total revenue figures."
    },
    {
        "inputs": "Compare opex vs capex spending across all funds in 2025",
        "expected_response": "The agent should query GL summary data filtered to 2025, group by category for opex and capex, and compare total amounts."
    },
    {
        "inputs": "Which fund strategy has the most investors?",
        "expected_response": "The agent should query fund performance data and compare investor counts across strategies (core, value_add, opportunistic, debt)."
    },
    {
        "inputs": "What is the average occupancy rate for multifamily properties?",
        "expected_response": "The agent should query property financials filtered to multifamily type and calculate the average occupancy rate."
    },
    {
        "inputs": "Show me the monthly revenue trend for 2025",
        "expected_response": "The agent should query property financials or GL summary for 2025, group by month, and show revenue figures for each month."
    },
    {
        "inputs": "Which properties in opportunistic funds have negative NOI?",
        "expected_response": "The agent should query property financials joined with fund data, filter to opportunistic strategy, and find properties where NOI is negative."
    },
    {
        "inputs": "What percentage of total spending goes to debt service?",
        "expected_response": "The agent should query GL summary, sum amounts by category, and calculate debt_service as a percentage of total."
    },
    {
        "inputs": "Give me a summary of the entire portfolio",
        "expected_response": "The agent should use multiple tools to provide a comprehensive overview: total AUM, fund count, property count, total NOI, and key metrics."
    },
])

print(f"Eval dataset: {len(eval_data)} questions")
eval_data

# COMMAND ----------

from mlflow.genai.scorers import Guidelines, Correctness

# Guidelines scorer: checks if the agent uses real data
grounding_guidelines = Guidelines(
    guidelines=[
        "The response must cite specific numbers from the data (dollar amounts, counts, percentages).",
        "The response must not contain hallucinated or made-up values.",
        "The response must directly answer the question asked.",
        "If the question requires data from multiple sources, the response should reference data from each source.",
    ],
    name="data_grounding",
)

# COMMAND ----------

# Create a function that calls the deployed endpoint
import mlflow

def predict_fn(inputs):
    """Call the deployed agent endpoint."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()

    results = []
    for question in inputs["inputs"]:
        response = w.serving_endpoints.query(
            name=ENDPOINT_NAME,
            input={"input": [{"role": "user", "content": question}]}
        )
        # Extract text from the response
        output_text = ""
        for item in response.output:
            if hasattr(item, "content") and item.content:
                for content_item in item.content:
                    if hasattr(content_item, "text"):
                        output_text += content_item.text
        results.append(output_text)
    return results

# COMMAND ----------

eval_results = mlflow.genai.evaluate(
    data=eval_data,
    predict_fn=predict_fn,
    scorers=[grounding_guidelines],
)

print("Evaluation complete!")
print(f"Results: {eval_results.metrics}")

# COMMAND ----------

# Show per-question results
display(eval_results.tables["eval_results"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## View Traces
# MAGIC Navigate to the MLflow experiment to see detailed traces for each evaluation run:
# MAGIC - **Experiment:** `/Users/fabien.vaucheret@databricks.com/juniper-square-demo/agent-evaluation`
# MAGIC - Each trace shows: question → tool selection → SQL executed → results → final answer
# MAGIC - This is the monitoring and evaluation loop that Juniper Square's Insights AI is missing today
# MAGIC
# MAGIC ## Key Metrics
# MAGIC - **Data Grounding Score:** Measures whether answers cite real data vs hallucinating
# MAGIC - **Consistency:** Run the same eval twice to show reproducible results (unlike their current Insights AI)
