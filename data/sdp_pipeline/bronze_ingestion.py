# Databricks notebook source
"""
Bronze Layer: Raw Data Ingestion via Auto Loader

Ingests raw data from a Unity Catalog Volume into bronze streaming tables
using Databricks Auto Loader (cloudFiles). Each table adds ingestion metadata
(_ingested_at, _source_file) and supports schema evolution for forward compatibility.

Source entities:
  - arenas        : tenant metadata (JSON)
  - funds         : fund master data (JSON)
  - investors     : LP commitments and contact details (CSV, header=true)
  - properties    : property data with valuations and occupancy (Parquet)
  - gl_transactions : high-volume GL (Parquet, partitioned by year=/month=)
"""

# COMMAND ----------

import dlt
from pyspark.sql.functions import current_timestamp, col


def _get_raw_data_path():
    return spark.conf.get("raw_data_path")

# COMMAND ----------

@dlt.table(
    name="bronze_arenas",
    comment="Raw arena (tenant) master data ingested from JSON. Arenas are the top-level scope for all other entities.",
)
def bronze_arenas():
    raw_path = _get_raw_data_path()
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{raw_path}/arenas/")
        .withColumn("_ingested_at", current_timestamp())
        .withColumn("_source_file", col("_metadata.file_path"))
    )

# COMMAND ----------

@dlt.table(
    name="bronze_funds",
    comment="Raw fund master data ingested from JSON.",
)
def bronze_funds():
    raw_path = _get_raw_data_path()
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{raw_path}/funds/")
        .withColumn("_ingested_at", current_timestamp())
        .withColumn("_source_file", col("_metadata.file_path"))
    )

# COMMAND ----------

# NOTE: investors land as CSV (header row present) — Auto Loader infers column types.
@dlt.table(
    name="bronze_investors",
    comment="Raw investor data ingested from CSV (header=true). Includes LP commitments and contact details.",
)
def bronze_investors():
    raw_path = _get_raw_data_path()
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("header", "true")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{raw_path}/investors/")
        .withColumn("_ingested_at", current_timestamp())
        .withColumn("_source_file", col("_metadata.file_path"))
    )

# COMMAND ----------

# NOTE: properties land as Parquet.
@dlt.table(
    name="bronze_properties",
    comment="Raw property data ingested from Parquet. Includes valuations, occupancy, and fund associations.",
)
def bronze_properties():
    raw_path = _get_raw_data_path()
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .load(f"{raw_path}/properties/")
        .withColumn("_ingested_at", current_timestamp())
        .withColumn("_source_file", col("_metadata.file_path"))
    )

# COMMAND ----------

# NOTE: GL transactions land as Parquet partitioned by year=/month=.
# cloudFiles.partitionColumns exposes those Hive-style partition columns to downstream layers.
@dlt.table(
    name="bronze_gl_transactions",
    comment="Raw general ledger transactions ingested from Parquet (partitioned by year/month). High-volume table (~20B rows at target scale) covering revenue, opex, capex, and debt service entries.",
)
def bronze_gl_transactions():
    raw_path = _get_raw_data_path()
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaEvolutionMode", "addNewColumns")
        .option("cloudFiles.partitionColumns", "year,month")
        .load(f"{raw_path}/gl_transactions/")
        .withColumn("_ingested_at", current_timestamp())
        .withColumn("_source_file", col("_metadata.file_path"))
    )
