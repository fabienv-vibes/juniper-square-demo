# Databricks notebook source
"""
Gold Layer: Materialized Aggregations for Dashboard Consumption

Reads batch from silver tables and produces pre-aggregated materialized views
optimized for Looker/BI dashboard queries. All gold tables carry arena_id so
per-tenant dashboards can filter cleanly. Liquid clustering is declared on the
three big tables that drive concurrency benchmarks.

Key tables:
  - gold_fund_performance: fund-level portfolio summary (per arena)
  - gold_property_financials: per-property monthly P&L with NOI (clustered on arena_id, month)
  - gold_investor_portfolio: per-investor commitment rollup
  - gold_gl_monthly_summary: per-arena/fund/category/month GL aggregation (primary dashboard table, clustered on arena_id, month)
  - gold_arena_overview: per-arena KPI rollup for tenant-level overviews
"""

# COMMAND ----------

import dlt
from pyspark.sql.functions import (
    col,
    sum as spark_sum,
    count,
    countDistinct,
    avg,
    collect_set,
    concat_ws,
    date_trunc,
    add_months,
    current_date,
    when,
)


# COMMAND ----------

@dlt.table(
    name="gold_fund_performance",
    comment="Per-arena, per-fund portfolio summary: AUM, property count, total invested vs current value, unrealized gain/loss, and investor commitments.",
)
def gold_fund_performance():
    funds = dlt.read("silver_funds")
    properties = dlt.read("silver_properties")
    investors = dlt.read("silver_investors")

    prop_agg = (
        properties
        .groupBy("fund_id")
        .agg(
            count("property_id").alias("property_count"),
            spark_sum("acquisition_price").alias("total_invested"),
            spark_sum("current_valuation").alias("current_portfolio_value"),
            spark_sum("unrealized_gain").alias("unrealized_gain_loss"),
        )
    )

    inv_agg = (
        investors
        .groupBy("fund_id")
        .agg(
            count("investor_id").alias("investor_count"),
            spark_sum("commitment_amount").alias("total_commitments"),
        )
    )

    return (
        funds
        .join(prop_agg, "fund_id", "left")
        .join(inv_agg, "fund_id", "left")
        .select(
            col("arena_id"),
            col("fund_id"),
            col("fund_name"),
            col("strategy"),
            col("aum").alias("total_aum"),
            col("property_count"),
            col("total_invested"),
            col("current_portfolio_value"),
            col("unrealized_gain_loss"),
            col("investor_count"),
            col("total_commitments"),
        )
    )

# COMMAND ----------

@dlt.table(
    name="gold_property_financials",
    comment="Per-arena, per-property monthly P&L: revenue, expenses, and net operating income (NOI). Joins GL transactions to property and fund metadata.",
    cluster_by=["arena_id", "month"],
)
def gold_property_financials():
    properties = dlt.read("silver_properties")
    funds = dlt.read("silver_funds")
    gl = dlt.read("silver_gl_transactions")

    gl_monthly = (
        gl
        .withColumn("month", date_trunc("month", col("transaction_date")))
        .groupBy("arena_id", "property_id", "month")
        .agg(
            spark_sum(
                col("amount") * (col("category") == "revenue").cast("int")
            ).alias("revenue"),
            spark_sum(
                col("amount") * col("is_expense").cast("int")
            ).alias("expenses"),
        )
        .withColumn("noi", col("revenue") - col("expenses"))
    )

    return (
        gl_monthly
        .join(properties.drop("arena_id"), "property_id", "left")
        .join(funds.select("fund_id", "fund_name"), "fund_id", "left")
        .select(
            col("arena_id"),
            col("property_id"),
            col("property_name"),
            col("property_type"),
            col("fund_name"),
            col("month"),
            col("revenue"),
            col("expenses"),
            col("noi"),
            col("occupancy_rate"),
        )
    )

# COMMAND ----------

@dlt.table(
    name="gold_investor_portfolio",
    comment="Per-investor portfolio summary: commitment count, total committed capital, and list of fund names.",
)
def gold_investor_portfolio():
    investors = dlt.read("silver_investors")
    funds = dlt.read("silver_funds")

    inv_funds = (
        investors
        .join(funds.drop("arena_id"), "fund_id", "left")
        .groupBy(
            investors["arena_id"],
            investors["investor_id"],
            investors["investor_name"],
            investors["type"],
            investors["city"],
            investors["state"],
        )
        .agg(
            count("fund_id").alias("fund_commitment_count"),
            spark_sum("commitment_amount").alias("total_committed"),
            collect_set("fund_name").alias("fund_names_set"),
        )
        .withColumn("fund_names", concat_ws(", ", col("fund_names_set")))
        .drop("fund_names_set")
    )

    return inv_funds

# COMMAND ----------

@dlt.table(
    name="gold_gl_monthly_summary",
    comment="Per-arena, per-fund, per-category, per-month GL aggregation. Primary table for concurrency benchmarking against Redshift.",
    cluster_by=["arena_id", "month"],
)
def gold_gl_monthly_summary():
    gl = dlt.read("silver_gl_transactions")
    funds = dlt.read("silver_funds")

    monthly = (
        gl
        .withColumn("month", date_trunc("month", col("transaction_date")))
        .groupBy("arena_id", "fund_id", "category", "month")
        .agg(
            spark_sum("amount").alias("total_amount"),
            count("transaction_id").alias("transaction_count"),
            avg("amount").alias("avg_transaction_amount"),
        )
    )

    return (
        monthly
        .join(funds.select("fund_id", "fund_name"), "fund_id", "left")
        .select(
            col("arena_id"),
            col("fund_id"),
            col("fund_name"),
            col("category"),
            col("month"),
            col("total_amount"),
            col("transaction_count"),
            col("avg_transaction_amount"),
        )
    )

# COMMAND ----------

@dlt.table(
    name="gold_arena_overview",
    comment="Per-arena (tenant) KPI rollup: AUM, property count, investor count, total commitments, and trailing-12-month revenue and NOI.",
)
def gold_arena_overview():
    arenas = dlt.read("silver_arenas")
    funds = dlt.read("silver_funds")
    properties = dlt.read("silver_properties")
    investors = dlt.read("silver_investors")
    gl = dlt.read("silver_gl_transactions")

    fund_agg = (
        funds
        .groupBy("arena_id")
        .agg(spark_sum("aum").alias("total_aum"))
    )

    prop_agg = (
        properties
        .groupBy("arena_id")
        .agg(countDistinct("property_id").alias("total_properties"))
    )

    inv_agg = (
        investors
        .groupBy("arena_id")
        .agg(
            countDistinct("investor_id").alias("total_investors"),
            spark_sum("commitment_amount").alias("total_commitments"),
        )
    )

    # Trailing 12 months of revenue and NOI (expenses defined by is_expense flag)
    cutoff = add_months(current_date(), -12)
    gl_ttm = (
        gl
        .filter(col("transaction_date") >= cutoff)
        .groupBy("arena_id")
        .agg(
            spark_sum(
                when(col("category") == "revenue", col("amount")).otherwise(0.0)
            ).alias("total_revenue"),
            spark_sum(
                when(col("category") == "revenue", col("amount")).otherwise(0.0)
            ).alias("_rev_tmp"),
            spark_sum(
                when(col("is_expense"), col("amount")).otherwise(0.0)
            ).alias("_exp_tmp"),
        )
        .withColumn("total_noi", col("_rev_tmp") - col("_exp_tmp"))
        .drop("_rev_tmp", "_exp_tmp")
    )

    return (
        arenas
        .join(fund_agg, "arena_id", "left")
        .join(prop_agg, "arena_id", "left")
        .join(inv_agg, "arena_id", "left")
        .join(gl_ttm, "arena_id", "left")
        .select(
            col("arena_id"),
            col("arena_name"),
            col("total_aum"),
            col("total_properties"),
            col("total_investors"),
            col("total_commitments"),
            col("total_revenue"),
            col("total_noi"),
        )
    )
