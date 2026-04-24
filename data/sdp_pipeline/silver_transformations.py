# Databricks notebook source
"""
Silver Layer: Validated and Enriched Data

Reads from bronze streaming tables, applies data quality expectations (dropping
invalid rows), casts columns to proper types, normalizes string fields, and
derives business columns. Every fat table carries arena_id so downstream gold
aggregations can be scoped per tenant.

Key derivations:
  - silver_properties: unrealized_gain = current_valuation - acquisition_price
  - silver_gl_transactions: fiscal_year, fiscal_quarter, is_expense flag
"""

# COMMAND ----------

import dlt
from pyspark.sql.functions import (
    col,
    lower,
    to_date,
    year,
    quarter,
)


# COMMAND ----------

@dlt.table(
    name="silver_arenas",
    comment="Validated arena (tenant) master with typed fields. Parent scope for all other silver tables.",
)
@dlt.expect_or_drop("valid_arena_id", "arena_id IS NOT NULL")
def silver_arenas():
    raw = dlt.read_stream("bronze_arenas")
    return (
        raw
        .withColumn("arena_id", col("arena_id").cast("string"))
        .withColumn("arena_name", col("arena_name").cast("string"))
    )

# COMMAND ----------

@dlt.table(
    name="silver_funds",
    comment="Validated fund data, scoped by arena_id, with normalized strategy and typed AUM.",
)
@dlt.expect_or_drop("valid_fund_id", "fund_id IS NOT NULL")
@dlt.expect_or_drop("valid_arena_id", "arena_id IS NOT NULL")
def silver_funds():
    raw = dlt.read_stream("bronze_funds")
    return (
        raw
        .withColumn("arena_id", col("arena_id").cast("string"))
        .withColumn("aum", col("aum").cast("double"))
        .withColumn("strategy", lower(col("strategy")))
    )

# COMMAND ----------

@dlt.table(
    name="silver_investors",
    comment="Validated investor data, scoped by arena_id, with typed commitment amounts and normalized investor type.",
)
@dlt.expect_or_drop("valid_investor_id", "investor_id IS NOT NULL")
@dlt.expect_or_drop("valid_arena_id", "arena_id IS NOT NULL")
def silver_investors():
    raw = dlt.read_stream("bronze_investors")
    return (
        raw
        .withColumn("arena_id", col("arena_id").cast("string"))
        .withColumn("commitment_amount", col("commitment_amount").cast("double"))
        .withColumn("type", lower(col("type")))
    )

# COMMAND ----------

@dlt.table(
    name="silver_properties",
    comment="Validated property data, scoped by arena_id, with typed financials and derived unrealized gain.",
)
@dlt.expect_or_drop("valid_property_id", "property_id IS NOT NULL")
@dlt.expect_or_drop("valid_arena_id", "arena_id IS NOT NULL")
def silver_properties():
    raw = dlt.read_stream("bronze_properties")
    return (
        raw
        .withColumn("arena_id", col("arena_id").cast("string"))
        .withColumn("acquisition_price", col("acquisition_price").cast("double"))
        .withColumn("current_valuation", col("current_valuation").cast("double"))
        .withColumn("square_footage", col("square_footage").cast("int"))
        .withColumn("occupancy_rate", col("occupancy_rate").cast("double"))
        .withColumn(
            "unrealized_gain",
            col("current_valuation").cast("double") - col("acquisition_price").cast("double"),
        )
    )

# COMMAND ----------

# Clustered by (arena_id, transaction_date) — the predicate pair that dominates
# per-tenant dashboard and analytical queries at 20B-row scale.
@dlt.table(
    name="silver_gl_transactions",
    comment="Validated GL transactions, scoped by arena_id, with typed amounts, parsed dates, and derived fiscal period and expense classification. Deduplicated on transaction_id.",
    cluster_by=["arena_id", "transaction_date"],
)
@dlt.expect_or_drop("valid_transaction_id", "transaction_id IS NOT NULL")
@dlt.expect_or_drop("valid_amount", "amount IS NOT NULL")
@dlt.expect_or_drop("valid_arena_id", "arena_id IS NOT NULL")
def silver_gl_transactions():
    # Batch read (not read_stream) so we can dropDuplicates on the full dataset — the
    # generator's retry left ~2.6x duplicates on transaction_id in bronze. Batch re-materialization
    # on each pipeline update is acceptable for the benchmark workload (full-refresh model).
    return (
        dlt.read("bronze_gl_transactions")
        .dropDuplicates(["transaction_id"])
        .withColumn("arena_id", col("arena_id").cast("string"))
        .withColumn("amount", col("amount").cast("double"))
        .withColumn("transaction_date", to_date(col("transaction_date")))
        .withColumn("fiscal_year", year(to_date(col("transaction_date"))))
        .withColumn("fiscal_quarter", quarter(to_date(col("transaction_date"))))
        .withColumn(
            "is_expense",
            col("category").isin("opex", "capex", "debt_service"),
        )
    )
