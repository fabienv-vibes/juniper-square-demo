# Databricks notebook source
# MAGIC %md
# MAGIC # Unity Catalog Setup: Juniper Square Demo
# MAGIC
# MAGIC This notebook creates the catalog, schemas, and volumes needed for the Juniper Square
# MAGIC real estate investment management demo on the e2-demo-field-eng workspace.
# MAGIC
# MAGIC **Objects created:**
# MAGIC - Catalog: `juniper_square_demo`
# MAGIC - Schemas: `bronze`, `silver`, `gold`
# MAGIC - Volume: `juniper_square_demo.bronze.landing`

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Catalog Setup

# COMMAND ----------

# On e2-demo-field-eng, catalogs are typically pre-provisioned by workspace admins.
# USE CATALOG is the primary path; CREATE CATALOG IF NOT EXISTS is a fallback
# in case you have catalog-creation privileges.
spark.sql("CREATE CATALOG IF NOT EXISTS juniper_square_demo")
spark.sql("USE CATALOG juniper_square_demo")
print("Using catalog: juniper_square_demo")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Schema Setup

# COMMAND ----------

spark.sql("CREATE SCHEMA IF NOT EXISTS bronze COMMENT 'Raw ingested data from Juniper Square systems'")
spark.sql("CREATE SCHEMA IF NOT EXISTS silver COMMENT 'Cleaned and conformed data'")
spark.sql("CREATE SCHEMA IF NOT EXISTS gold COMMENT 'Business-level aggregates and metrics'")
print("Schemas created: bronze, silver, gold")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Volume Setup

# COMMAND ----------

spark.sql("""
    CREATE VOLUME IF NOT EXISTS juniper_square_demo.bronze.landing
    COMMENT 'Landing zone for raw JSON files from Juniper Square data sources'
""")
print("Volume created: juniper_square_demo.bronze.landing")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Verification

# COMMAND ----------

print("=" * 60)
print("VERIFICATION: Juniper Square Demo Catalog Objects")
print("=" * 60)

# Check catalog
catalogs = spark.sql("SHOW CATALOGS LIKE 'juniper_square_demo'").collect()
assert len(catalogs) == 1, "Catalog juniper_square_demo not found!"
print(f"  Catalog: juniper_square_demo")

# Check schemas
spark.sql("USE CATALOG juniper_square_demo")
schemas = {row.databaseName for row in spark.sql("SHOW SCHEMAS").collect()}
expected_schemas = {"bronze", "silver", "gold"}
for s in sorted(expected_schemas):
    status = "OK" if s in schemas else "MISSING"
    print(f"  Schema:  {s:10s} [{status}]")
assert expected_schemas.issubset(schemas), f"Missing schemas: {expected_schemas - schemas}"

# Check volume
volumes = spark.sql("SHOW VOLUMES IN bronze").collect()
volume_names = {row.volume_name for row in volumes}
vol_status = "OK" if "landing" in volume_names else "MISSING"
print(f"  Volume:  bronze.landing [{vol_status}]")
assert "landing" in volume_names, "Volume bronze.landing not found!"

print("=" * 60)
print("All objects verified successfully.")
