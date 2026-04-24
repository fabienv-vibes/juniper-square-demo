# Databricks notebook source
# MAGIC %md
# MAGIC # Fund Analyst Agent: Initiative 2 Demo
# MAGIC A compound AI agent that answers natural language questions about RE investment data
# MAGIC by querying gold-layer tables via tool-calling.

# COMMAND ----------

# MAGIC %pip install -U databricks-langchain langgraph==0.3.4 databricks-agents pydantic
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import mlflow

CATALOG = "juniper_square_demo"
SCHEMA = "pipeline"
LLM_ENDPOINT = "databricks-claude-sonnet-4-6"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.fund_analyst_agent"
WAREHOUSE_ID = "fb0926b9e10676bf"

mlflow.set_registry_uri("databricks-uc")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create UC SQL Functions (Agent Tools)

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.query_fund_performance(
  strategy STRING DEFAULT NULL COMMENT 'Fund strategy filter: core, value_add, opportunistic, or debt',
  min_aum DOUBLE DEFAULT NULL COMMENT 'Minimum total AUM filter'
)
RETURNS TABLE(
  fund_name STRING,
  strategy STRING,
  total_aum DOUBLE,
  property_count LONG,
  total_invested DOUBLE,
  current_portfolio_value DOUBLE,
  unrealized_gain_loss DOUBLE,
  investor_count LONG
)
COMMENT 'Get fund performance metrics. Filter by strategy (core/value_add/opportunistic/debt) and/or minimum AUM.'
RETURN
  SELECT fund_name, strategy, total_aum, property_count, total_invested,
         current_portfolio_value, unrealized_gain_loss, investor_count
  FROM {CATALOG}.{SCHEMA}.gold_fund_performance
  WHERE (strategy = query_fund_performance.strategy OR query_fund_performance.strategy IS NULL)
    AND (total_aum >= min_aum OR min_aum IS NULL)
""")

print("Created query_fund_performance")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.query_property_financials(
  fund_name_filter STRING DEFAULT NULL COMMENT 'Filter by fund name (exact match)',
  property_type_filter STRING DEFAULT NULL COMMENT 'Filter by property type (e.g. office, retail, industrial, multifamily)',
  start_month STRING DEFAULT NULL COMMENT 'Only return data from this month onward (YYYY-MM-DD format)'
)
RETURNS TABLE(
  property_name STRING,
  property_type STRING,
  fund_name STRING,
  month DATE,
  revenue DOUBLE,
  expenses DOUBLE,
  noi DOUBLE,
  occupancy_rate DOUBLE
)
COMMENT 'Get monthly property financial data including revenue, expenses, and NOI. Filter by fund name, property type, and/or start month (YYYY-MM-DD format).'
RETURN
  SELECT property_name, property_type, fund_name, month, revenue, expenses, noi, occupancy_rate
  FROM {CATALOG}.{SCHEMA}.gold_property_financials
  WHERE (fund_name = fund_name_filter OR fund_name_filter IS NULL)
    AND (property_type = property_type_filter OR property_type_filter IS NULL)
    AND (month >= start_month OR start_month IS NULL)
""")

print("Created query_property_financials")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.query_gl_summary(
  fund_name_filter STRING DEFAULT NULL COMMENT 'Filter by fund name (exact match)',
  category_filter STRING DEFAULT NULL COMMENT 'Filter by GL category: revenue, opex, capex, or debt_service',
  start_month STRING DEFAULT NULL COMMENT 'Only return data from this month onward (YYYY-MM-DD format)'
)
RETURNS TABLE(
  fund_name STRING,
  category STRING,
  month DATE,
  total_amount DOUBLE,
  transaction_count LONG,
  avg_transaction_amount DOUBLE
)
COMMENT 'Get monthly general ledger summary by fund and category (revenue/opex/capex/debt_service). Filter by fund name, category, and/or start month.'
RETURN
  SELECT fund_name, category, month, total_amount, transaction_count, avg_transaction_amount
  FROM {CATALOG}.{SCHEMA}.gold_gl_monthly_summary
  WHERE (fund_name = fund_name_filter OR fund_name_filter IS NULL)
    AND (category = category_filter OR category_filter IS NULL)
    AND (month >= start_month OR start_month IS NULL)
""")

print("Created query_gl_summary")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write Agent Code

# COMMAND ----------

agent_code = '''
import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    output_to_responses_items_stream,
    to_chat_completions_input,
)
from databricks_langchain import ChatDatabricks, UCFunctionToolkit
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt.tool_node import ToolNode
from typing import Annotated, Generator, Sequence, TypedDict

LLM_ENDPOINT = "databricks-claude-sonnet-4-6"
SYSTEM_PROMPT = """You are a Fund Analyst for a real estate investment management platform.
You help users understand fund performance, property financials, and GL transaction data.

When answering questions:
- Always query the data using your tools rather than guessing
- Cite specific numbers from the query results
- If a question spans multiple data domains, use multiple tools
- Format currency values with $ and commas
- Be concise but thorough"""


class AgentState(TypedDict):
    messages: Annotated[Sequence, add_messages]


class FundAnalystAgent(ResponsesAgent):
    def __init__(self):
        self.llm = ChatDatabricks(endpoint=LLM_ENDPOINT, temperature=0.1)
        uc_toolkit = UCFunctionToolkit(
            function_names=[
                "juniper_square_demo.pipeline.query_fund_performance",
                "juniper_square_demo.pipeline.query_property_financials",
                "juniper_square_demo.pipeline.query_gl_summary",
            ]
        )
        self.tools = uc_toolkit.tools
        self.llm_with_tools = self.llm.bind_tools(self.tools)

    def _build_graph(self):
        def should_continue(state):
            last = state["messages"][-1]
            if isinstance(last, AIMessage) and last.tool_calls:
                return "tools"
            return "end"

        def call_model(state):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + state["messages"]
            response = self.llm_with_tools.invoke(messages)
            return {"messages": [response]}

        graph = StateGraph(AgentState)
        graph.add_node("agent", RunnableLambda(call_model))
        graph.add_node("tools", ToolNode(self.tools))
        graph.add_conditional_edges(
            "agent", should_continue, {"tools": "tools", "end": END}
        )
        graph.add_edge("tools", "agent")
        graph.set_entry_point("agent")
        return graph.compile()

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        outputs = [
            event.item
            for event in self.predict_stream(request)
            if event.type == "response.output_item.done"
        ]
        return ResponsesAgentResponse(output=outputs)

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        messages = to_chat_completions_input(
            [m.model_dump() for m in request.input]
        )
        graph = self._build_graph()
        for event in graph.stream({"messages": messages}, stream_mode=["updates"]):
            if event[0] == "updates":
                for node_data in event[1].values():
                    if node_data.get("messages"):
                        yield from output_to_responses_items_stream(
                            node_data["messages"]
                        )


mlflow.langchain.autolog()
AGENT = FundAnalystAgent()
mlflow.models.set_model(AGENT)
'''

import os

agent_dir = "/Workspace/Users/fabien.vaucheret@databricks.com/juniper-square-demo/agent"
os.makedirs(agent_dir, exist_ok=True)
with open(f"{agent_dir}/agent.py", "w") as f:
    f.write(agent_code)
print(f"Agent written to {agent_dir}/agent.py")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test the Agent Locally

# COMMAND ----------

import sys
sys.path.insert(0, agent_dir)
from agent import AGENT
from mlflow.types.responses import ResponsesAgentRequest

# Test: total AUM across all funds
request = ResponsesAgentRequest(
    input=[{"role": "user", "content": "What is the total AUM across all funds?"}]
)
result = AGENT.predict(request)
print(result.model_dump(exclude_none=True))

# COMMAND ----------

# Test: cross-table question
request = ResponsesAgentRequest(
    input=[
        {
            "role": "user",
            "content": "Which fund strategy has the highest NOI margin? Compare revenue vs expenses.",
        }
    ]
)
result = AGENT.predict(request)
print(result.model_dump(exclude_none=True))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log Model to MLflow & Register in Unity Catalog

# COMMAND ----------

from mlflow.models.resources import DatabricksServingEndpoint, DatabricksFunction

resources = [
    DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.query_fund_performance"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.query_property_financials"),
    DatabricksFunction(function_name=f"{CATALOG}.{SCHEMA}.query_gl_summary"),
]

with mlflow.start_run():
    model_info = mlflow.pyfunc.log_model(
        name="agent",
        python_model=f"{agent_dir}/agent.py",
        resources=resources,
        pip_requirements=[
            "mlflow>=3.1",
            "databricks-langchain",
            "langgraph==0.3.4",
            "databricks-agents",
        ],
        input_example={
            "input": [
                {"role": "user", "content": "What is the total AUM across all funds?"}
            ]
        },
        registered_model_name=MODEL_NAME,
    )
    print(f"Model logged: {model_info.model_uri}")
    print(f"Registered as: {MODEL_NAME}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next Steps
# MAGIC - Run notebook `11_deploy_agent` to deploy to a serving endpoint
# MAGIC - Test in AI Playground
# MAGIC - Run notebook `12_agent_evaluation` for systematic evaluation
