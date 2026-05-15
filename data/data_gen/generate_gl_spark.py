# Databricks notebook source
# MAGIC %md
# MAGIC # Juniper GL Transactions Generator (Spark) — v2 wider schema, redline scale
# MAGIC
# MAGIC Generates **5 B unique GL transaction rows** with a **wider schema** (11 added columns)
# MAGIC for the May 10 customer redline benchmark. Writes Parquet partitioned by year/month
# MAGIC to the existing landing volume so the SDP medallion pipeline picks it up unchanged.
# MAGIC
# MAGIC Wider schema simulates real GL + the upcoming PDF-Reporting use case (memo_text proxy).
# MAGIC
# MAGIC ## What this changes vs the prior generator
# MAGIC
# MAGIC - 5 B unique rows (was 2 M)
# MAGIC - 11 new columns: memo_text, reference_id, external_ref_id, currency, fx_rate_to_usd,
# MAGIC   approval_status, approver_id, approval_date, cost_center, department_code,
# MAGIC   counterparty_name, counterparty_id
# MAGIC - Pure-Spark generation (no pandas, no local Python) — runs in workspace serverless
# MAGIC - No deliberate transaction_id duplicates (silver MV dropDuplicates becomes a no-op,
# MAGIC   but keeps the pipeline structure intact)
# MAGIC
# MAGIC ## Iteration knobs
# MAGIC
# MAGIC - `N_TRANSACTIONS` — start at 5 B, bump if silver lands under 1.5 TB
# MAGIC - `MEMO_LONG_PROBABILITY` — set > 0 to mix in long PDF-extract-style memos if size still light

# COMMAND ----------

from pyspark.sql import functions as F

# COMMAND ----------

# ---------------------------------------------------------------------------
# Knobs
# ---------------------------------------------------------------------------
CATALOG = "juniper_square_demo_catalog"
SCHEMA = "raw"
VOLUME = "landing"
LANDING_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/gl_transactions"

N_TRANSACTIONS = 10_000_000_000  # 10 B unique GL rows (iter #2 — 5B was 0.4 TB silver, target band 1.5-3 TB)
N_ARENAS = 10_000
N_FUNDS = 250
N_PROPERTIES = 1_000
N_PARTITIONS = 8_000             # write parallelism scaled with row count

START_DATE = "2023-01-01"
DAY_RANGE = 1460                 # ~4 years through 2026-12-31

# memo_text length controls — start short, lengthen if silver < 1.5 TB band
MEMO_LONG_PROBABILITY = 0.5      # 50% PDF-extract-style mix; pushes silver toward target band

# ---------------------------------------------------------------------------
# Static enum data
# ---------------------------------------------------------------------------
CURRENCIES = ["USD"] * 19 + ["EUR", "GBP", "CAD"]                    # ~95% USD
APPROVAL_STATUSES = ["approved"] * 9 + ["pending", "rejected"]       # 90% approved
DEPT_CODES = ["OPS", "FIN", "IT", "ADMIN", "LEGAL", "HR", "MKTG", "FAC", "RE", "TAX"]
ACCOUNT_CODES_REVENUE = ["4100", "4200", "4300"]
ACCOUNT_CODES_OPEX = ["5100", "5200", "5300", "5400"]
ACCOUNT_CODES_CAPEX = ["6100", "6200"]
ACCOUNT_CODES_DEBT = ["7100", "7200"]

# Long memo template (~600 chars) — used probabilistically
LONG_MEMO_TEMPLATE = (
    "Detailed accounting entry processed during scheduled month-end close. "
    "Property valuation reviewed against current market comparables and prior-quarter "
    "benchmarks. Approval routed through standard four-eye review workflow with "
    "supporting documentation attached including invoice copies, contract references, "
    "and counterparty correspondence. Reference cross-checked with general ledger "
    "trial balance and reconciled to subsidiary investor reporting feed. Notes from "
    "the deal team reviewed and material exceptions escalated per policy. Entry "
    "subject to standard controls including SOC 2 audit trail and SOX segregation."
)

# COMMAND ----------

# ---------------------------------------------------------------------------
# Clear existing files in the landing volume (idempotent rerun)
# ---------------------------------------------------------------------------
print(f"Clearing landing volume: {LANDING_PATH}")
try:
    dbutils.fs.rm(LANDING_PATH, recurse=True)
except Exception as e:
    print(f"  (no existing files to clear: {e})")
dbutils.fs.mkdirs(LANDING_PATH)
print("Cleared.")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
print(f"Generating {N_TRANSACTIONS:,} GL rows across {N_PARTITIONS} partitions ...")

base = spark.range(N_TRANSACTIONS, numPartitions=N_PARTITIONS).withColumnRenamed("id", "row_id")

# Independent random columns (separate seeds keep correlations clean)
df = (
    base
    .withColumn("rnd_cat", F.rand(seed=11))
    .withColumn("rnd_amt", F.rand(seed=12))
    .withColumn("rnd_date", F.rand(seed=13))
    .withColumn("rnd_posted", F.rand(seed=14))
    .withColumn("rnd_memo", F.rand(seed=15))
    .withColumn("rnd_appr_lag", F.rand(seed=16))
)

# Identifier columns — modular hashing into the cardinality knobs
df = (
    df
    .withColumn("transaction_id", F.concat(F.lit("TXN-"), F.lpad(F.col("row_id").cast("string"), 12, "0")))
    .withColumn("arena_id", F.concat(F.lit("ARN-"), F.lpad((F.col("row_id") % N_ARENAS).cast("string"), 5, "0")))
    .withColumn("fund_id", F.concat(F.lit("FND-"), F.lpad((F.col("row_id") % N_FUNDS).cast("string"), 5, "0")))
    .withColumn("property_id", F.concat(F.lit("PRP-"), F.lpad((F.col("row_id") % N_PROPERTIES).cast("string"), 6, "0")))
)

# Category split: 40% revenue / 35% opex / 15% capex / 10% debt_service
df = df.withColumn(
    "category",
    F.when(F.col("rnd_cat") < 0.40, F.lit("revenue"))
     .when(F.col("rnd_cat") < 0.75, F.lit("opex"))
     .when(F.col("rnd_cat") < 0.90, F.lit("capex"))
     .otherwise(F.lit("debt_service"))
)

# Account code per category (modular pick within the category's code list)
revenue_arr = F.array(*[F.lit(x) for x in ACCOUNT_CODES_REVENUE])
opex_arr = F.array(*[F.lit(x) for x in ACCOUNT_CODES_OPEX])
capex_arr = F.array(*[F.lit(x) for x in ACCOUNT_CODES_CAPEX])
debt_arr = F.array(*[F.lit(x) for x in ACCOUNT_CODES_DEBT])
df = df.withColumn(
    "account_code",
    F.when(F.col("category") == "revenue",
           F.element_at(revenue_arr, (F.col("row_id") % F.lit(len(ACCOUNT_CODES_REVENUE)) + 1).cast("int")))
     .when(F.col("category") == "opex",
           F.element_at(opex_arr, (F.col("row_id") % F.lit(len(ACCOUNT_CODES_OPEX)) + 1).cast("int")))
     .when(F.col("category") == "capex",
           F.element_at(capex_arr, (F.col("row_id") % F.lit(len(ACCOUNT_CODES_CAPEX)) + 1).cast("int")))
     .otherwise(F.element_at(debt_arr, (F.col("row_id") % F.lit(len(ACCOUNT_CODES_DEBT)) + 1).cast("int")))
)
df = df.withColumn("account_name", F.concat(F.lit("GL "), F.col("account_code")))

# Amounts (signed: + revenue, - everything else)
df = df.withColumn(
    "amount",
    F.when(F.col("category") == "revenue", F.col("rnd_amt") * 250000)
     .when(F.col("category") == "opex", -(F.col("rnd_amt") * 75000))
     .when(F.col("category") == "capex", -(F.col("rnd_amt") * 500000))
     .otherwise(-(F.col("rnd_amt") * 100000))
)

# Date (partition columns derived from this)
df = (
    df
    .withColumn("transaction_date",
                F.expr(f"date_add(to_date('{START_DATE}'), CAST(rnd_date * {DAY_RANGE} AS INT))"))
    .withColumn("year", F.year("transaction_date"))
    .withColumn("month", F.month("transaction_date"))
    .withColumn("posted", F.when(F.col("rnd_posted") < 0.98, F.lit(True)).otherwise(F.lit(False)))
)

# Description (kept similar to existing schema)
df = df.withColumn(
    "description",
    F.concat(F.lit("Transaction "), F.col("transaction_id"), F.lit(" — "), F.col("category"))
)

# ---------------------------------------------------------------------------
# NEW WIDER COLUMNS
# ---------------------------------------------------------------------------

# memo_text — short by default (~150 chars), optional long mix
short_memo = F.concat_ws(
    " ",
    F.lit("Q"),
    F.quarter("transaction_date").cast("string"),
    F.col("category"),
    F.lit("entry for property"), F.col("property_id"),
    F.lit("fund"), F.col("fund_id"),
    F.lit("approved by USR-"), F.lpad((F.col("row_id") % F.lit(500)).cast("string"), 4, "0"),
    F.lit("ref REF-"), F.lpad((F.col("row_id") % F.lit(99999)).cast("string"), 5, "0"),
    F.lit("amount $"), F.format_number("amount", 2),
)
df = df.withColumn(
    "memo_text",
    F.when(F.col("rnd_memo") < F.lit(MEMO_LONG_PROBABILITY), F.concat(short_memo, F.lit(". "), F.lit(LONG_MEMO_TEMPLATE)))
     .otherwise(short_memo)
)

# Reference IDs
df = df.withColumn("reference_id",
                   F.concat(F.lit("REF-"), F.lpad((F.col("row_id") % F.lit(99999)).cast("string"), 5, "0")))
df = df.withColumn("external_ref_id",
                   F.concat(F.lit("EXT-"), F.lpad((F.col("row_id") % F.lit(999999)).cast("string"), 6, "0")))

# Currency (95% USD by enum array distribution)
currency_arr = F.array(*[F.lit(c) for c in CURRENCIES])
df = df.withColumn("currency",
                   F.element_at(currency_arr, (F.col("row_id") % F.lit(len(CURRENCIES)) + 1).cast("int")))
df = df.withColumn(
    "fx_rate_to_usd",
    F.when(F.col("currency") == "USD", F.lit(1.0))
     .when(F.col("currency") == "EUR", F.lit(1.08))
     .when(F.col("currency") == "GBP", F.lit(1.27))
     .otherwise(F.lit(0.74))
)

# Approval status (90% approved)
appr_arr = F.array(*[F.lit(s) for s in APPROVAL_STATUSES])
df = df.withColumn("approval_status",
                   F.element_at(appr_arr, (F.col("row_id") % F.lit(len(APPROVAL_STATUSES)) + 1).cast("int")))
df = df.withColumn("approver_id",
                   F.concat(F.lit("USR-"), F.lpad((F.col("row_id") % F.lit(500)).cast("string"), 4, "0")))
df = df.withColumn(
    "approval_date",
    F.expr("date_add(transaction_date, CAST(rnd_appr_lag * 7 AS INT))")
)

# Cost center / department
df = df.withColumn("cost_center",
                   F.concat(F.lit("CC-"), F.lpad((F.col("row_id") % F.lit(50)).cast("string"), 3, "0")))
dept_arr = F.array(*[F.lit(d) for d in DEPT_CODES])
df = df.withColumn("department_code",
                   F.element_at(dept_arr, (F.col("row_id") % F.lit(len(DEPT_CODES)) + 1).cast("int")))

# Counterparty
df = df.withColumn("counterparty_name",
                   F.concat(F.lit("Counterparty "), F.lpad((F.col("row_id") % F.lit(500)).cast("string"), 4, "0"), F.lit(" LLC")))
df = df.withColumn("counterparty_id",
                   F.concat(F.lit("CP-"), F.lpad((F.col("row_id") % F.lit(99999)).cast("string"), 5, "0")))

# Drop helper columns
df = df.drop("row_id", "rnd_cat", "rnd_amt", "rnd_date", "rnd_posted", "rnd_memo", "rnd_appr_lag")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Write Parquet partitioned by (year, month)
# ---------------------------------------------------------------------------
print(f"Writing Parquet to {LANDING_PATH} ...")
(
    df
    .write
    .partitionBy("year", "month")
    .mode("overwrite")
    .parquet(LANDING_PATH)
)
print(f"DONE — wrote {N_TRANSACTIONS:,} rows.")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Quick verification — landed file count + sample
# ---------------------------------------------------------------------------
files = dbutils.fs.ls(LANDING_PATH)
print(f"Top-level entries in landing path: {len(files)}")
for f in files[:5]:
    print(f"  {f.name}")

sample = (
    spark.read.parquet(LANDING_PATH)
    .select("transaction_id", "arena_id", "category", "amount", "currency", "approval_status", "memo_text")
    .limit(5)
)
sample.show(truncate=False)
print("Generator complete.")
