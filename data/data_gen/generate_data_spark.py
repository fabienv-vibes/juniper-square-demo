# Databricks notebook source
# MAGIC %md
# MAGIC # Juniper Square Demo — Serverless Spark Data Generator
# MAGIC
# MAGIC **Purpose:** Generate realistic real-estate investment management data at scale for the
# MAGIC Juniper Square concurrency / lakehouse benchmark demo.
# MAGIC
# MAGIC **Runs on:** Serverless compute (no classic cluster dependencies).
# MAGIC
# MAGIC ## Target scale
# MAGIC - **10,000 arenas** (multi-tenant scope, "ARN-NNNNN")
# MAGIC - **5 funds / arena** → 50,000 funds
# MAGIC - **10 properties / arena** → 100,000 properties
# MAGIC - **50 investors / arena** → 500,000 investors (each commits to 1–3 funds in their arena)
# MAGIC - **GL transactions:** up to 20B rows, calibrated via `gl_rows_target` widget
# MAGIC   - Default calibration run: 2B rows
# MAGIC   - Full run: 20B rows, ~10 TB Delta on disk (scales linearly)
# MAGIC
# MAGIC ## Output layout (raw landing, multi-format intentional for Auto Loader demo)
# MAGIC - `/arenas/arenas.json` — single JSON file
# MAGIC - `/funds/funds.json` — single JSON file
# MAGIC - `/investors/investors.csv` — CSV with header
# MAGIC - `/properties/` — Parquet
# MAGIC - `/gl_transactions/year=YYYY/month=MM/` — partitioned Parquet
# MAGIC
# MAGIC ## Scale / design
# MAGIC - `arena_id` is a **global shard key**. Every downstream row carries a valid `arena_id`,
# MAGIC   and all referential integrity (property→fund, investor→fund, GL→property→fund) is
# MAGIC   resolved **within** an arena.
# MAGIC - GL rows are generated from `spark.range(gl_rows_target)` with heavy partitioning, then
# MAGIC   written partitioned by `(year, month)`. Each output partition is independently
# MAGIC   generable — no driver-side collection, no global shuffles required for keys.
# MAGIC - Dimension tables are small enough (arenas ≤ 10K, funds ≤ 50K, properties ≤ 100K,
# MAGIC   investors ≤ 500K) to broadcast cheaply into the fact-join stage.
# MAGIC
# MAGIC ## Expected wall-clock (to be calibrated empirically)
# MAGIC - Dimensions: O(minutes) combined
# MAGIC - 2B GL rows: order-of-magnitude tens of minutes on serverless (calibration run)
# MAGIC - 20B GL rows: scales ~linearly; measure after calibration run
# MAGIC
# MAGIC ## IMPORTANT
# MAGIC - This notebook is **idempotent per path**: writes use `mode("overwrite")` so repeated
# MAGIC   runs replace prior output at the same volume path.
# MAGIC - Seeded RNG (`rand(seed)`) is used where determinism matters.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Widgets

# COMMAND ----------

dbutils.widgets.text("num_arenas", "10000", "Number of arenas (tenants)")
dbutils.widgets.text("gl_rows_target", "2000000000", "GL transactions target row count")
dbutils.widgets.text(
    "output_volume_path",
    "/Volumes/juniper_square_demo_catalog/raw/landing",
    "Output volume root path",
)
dbutils.widgets.text("start_date", "2023-01-01", "GL start date (inclusive)")
dbutils.widgets.text("end_date", "2026-03-31", "GL end date (inclusive)")

NUM_ARENAS = int(dbutils.widgets.get("num_arenas"))
GL_ROWS_TARGET = int(dbutils.widgets.get("gl_rows_target"))
OUTPUT_ROOT = dbutils.widgets.get("output_volume_path").rstrip("/")
START_DATE = dbutils.widgets.get("start_date")
END_DATE = dbutils.widgets.get("end_date")

FUNDS_PER_ARENA = 5
PROPERTIES_PER_ARENA = 10
INVESTORS_PER_ARENA = 50

NUM_FUNDS = NUM_ARENAS * FUNDS_PER_ARENA
NUM_PROPERTIES = NUM_ARENAS * PROPERTIES_PER_ARENA
NUM_INVESTORS = NUM_ARENAS * INVESTORS_PER_ARENA

SEED = 42

print(f"num_arenas          = {NUM_ARENAS:,}")
print(f"num_funds           = {NUM_FUNDS:,}")
print(f"num_properties      = {NUM_PROPERTIES:,}")
print(f"num_investors       = {NUM_INVESTORS:,}")
print(f"gl_rows_target      = {GL_ROWS_TARGET:,}")
print(f"output_volume_path  = {OUTPUT_ROOT}")
print(f"date range          = {START_DATE} .. {END_DATE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Imports and Spark config

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql import Window
from pyspark.sql.types import (
    StringType,
    DoubleType,
    IntegerType,
    BooleanType,
    DateType,
    TimestampType,
)

# Serverless exposes `spark` already.
# Serverless Spark Connect disallows many runtime conf sets (CONFIG_NOT_AVAILABLE).
# These are tuning hints only — skip them if blocked. Serverless auto-tunes shuffle partitions.
def _try_set(key, value):
    try:
        spark.conf.set(key, value)
    except Exception as _e:
        print(f"(skipped unsupported conf on serverless) {key}={value}: {type(_e).__name__}")

_try_set("spark.sql.sources.partitionOverwriteMode", "dynamic")
_try_set("spark.sql.shuffle.partitions", "800")
# Parquet options tuned for large partitioned writes
_try_set("spark.sql.parquet.compression.codec", "snappy")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Business-logic reference data
# MAGIC
# MAGIC Weights and value pools mirror the Priority 1 generator so downstream SDP/SQL assets
# MAGIC keep the same categorical signatures.

# COMMAND ----------

# Strategy values + weights (funds)
STRATEGIES = ["core", "value_add", "opportunistic", "debt"]
STRATEGY_WEIGHTS = [0.30, 0.30, 0.25, 0.15]
STRATEGY_RETURN_RANGES = {
    "core": (6.0, 10.0),
    "value_add": (10.0, 16.0),
    "opportunistic": (16.0, 25.0),
    "debt": (6.0, 12.0),
}

# Investor types
INVESTOR_TYPES = ["institutional", "family_office", "hnwi", "endowment", "pension"]
INVESTOR_TYPE_WEIGHTS = [0.25, 0.25, 0.30, 0.10, 0.10]
INVESTOR_COMMITMENT_RANGES = {
    "institutional": (5_000_000, 50_000_000),
    "family_office": (1_000_000, 20_000_000),
    "hnwi": (100_000, 5_000_000),
    "endowment": (2_000_000, 30_000_000),
    "pension": (10_000_000, 50_000_000),
}

# Property types
PROPERTY_TYPES = ["multifamily", "office", "industrial", "retail", "mixed_use"]
PROPERTY_TYPE_WEIGHTS = [0.35, 0.20, 0.20, 0.15, 0.10]
SQFT_RANGES = {
    "multifamily": (50_000, 400_000),
    "office": (30_000, 500_000),
    "industrial": (100_000, 500_000),
    "retail": (10_000, 150_000),
    "mixed_use": (50_000, 300_000),
}

# GL categories
CATEGORIES = ["revenue", "opex", "capex", "debt_service"]
CATEGORY_WEIGHTS = [0.35, 0.30, 0.20, 0.15]

# Chart of accounts: category -> [(code, name), ...]
GL_ACCOUNTS_BY_CATEGORY = {
    "revenue": [
        ("4100", "Rental Revenue"),
        ("4200", "Parking Revenue"),
        ("4300", "Other Income"),
    ],
    "opex": [
        ("5100", "Property Management Fee"),
        ("5200", "Maintenance & Repairs"),
        ("5300", "Utilities"),
        ("5400", "Insurance"),
        ("5500", "Property Tax"),
    ],
    "capex": [
        ("6100", "Capital Improvements"),
        ("6200", "Tenant Improvements"),
    ],
    "debt_service": [
        ("7100", "Mortgage Interest"),
        ("7200", "Loan Principal"),
    ],
}

# Description pools per category (joined later via lookup DataFrames)
DESCRIPTIONS_BY_CATEGORY = {
    "revenue": [
        "Monthly rent collection", "Lease payment received", "Parking fee",
        "Late fee income", "Common area maintenance recovery", "Utility reimbursement",
        "Storage rental income", "Amenity fee", "Application fee income",
    ],
    "opex": [
        "HVAC maintenance", "Plumbing repair", "Elevator service", "Janitorial services",
        "Landscaping", "Electric bill", "Water/sewer", "Gas bill",
        "Property insurance premium", "Property tax payment", "Security services",
        "Snow removal", "Pest control", "Fire alarm inspection",
    ],
    "capex": [
        "Roof replacement", "Lobby renovation", "Parking lot resurfacing",
        "Window replacement", "Elevator modernization", "Tenant build-out",
        "HVAC system replacement", "Facade restoration", "ADA compliance upgrades",
    ],
    "debt_service": [
        "Monthly mortgage payment", "Interest payment", "Loan principal payment",
        "Refinancing fee", "Line of credit draw", "Bridge loan interest",
    ],
}

# Property + fund naming pools
NEIGHBORHOODS = [
    "Buckhead", "Midtown", "River North", "SoHo", "Uptown", "Westside",
    "Harbor Point", "Park Avenue", "Lake Shore", "Beacon Hill", "Cherry Creek",
    "Brickell", "Ballston", "NoMa", "Fenway", "Arts District", "Old Town",
    "Seaport", "Union Station", "Pacific Heights", "The Loop", "Back Bay",
    "Wynwood", "Pearl District", "Capitol Hill", "Deep Ellum", "Greenpoint",
    "Mission Bay", "Belltown", "King West",
]

BUILDING_PREFIXES = [
    "Meridian", "Apex", "Summit", "Pinnacle", "Vantage", "Crestview",
    "Ironworks", "Foundry", "Centennial", "Heritage", "Gateway", "Keystone",
    "Ridgewood", "Maplewood", "Riverside", "Lakewood", "Stonegate", "Cedarbrook",
    "Hawthorne", "Ashford",
]

FUND_SPONSORS = [
    "Blackstone", "Greystar", "Starwood", "Brookfield", "Prologis",
    "Hines", "Tishman Speyer", "Related", "Rockpoint", "Ares",
    "KKR", "Cerberus", "Angelo Gordon", "Colony", "Lone Star",
    "Oaktree", "Carlyle", "Apollo", "Fortress", "Invesco",
    "PGIM", "MetLife", "Nuveen", "LaSalle", "CBRE Investment",
]
FUND_SUFFIXES = [
    "Real Estate Partners", "Growth Fund", "Value Fund", "Opportunity Fund",
    "Income Fund", "Credit Fund", "Capital Partners", "Property Trust",
    "Strategic Fund", "Core Fund",
]
ROMAN_NUMERALS = [
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV",
]

TIERS = ["platinum", "gold", "silver"]
TIER_WEIGHTS = [0.10, 0.30, 0.60]

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

CITIES = [
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix",
    "Philadelphia", "San Antonio", "San Diego", "Dallas", "Austin",
    "Jacksonville", "Fort Worth", "Columbus", "Charlotte", "Indianapolis",
    "San Francisco", "Seattle", "Denver", "Washington", "Boston",
    "Nashville", "Portland", "Miami", "Atlanta", "Las Vegas",
    "Minneapolis", "Tampa", "Orlando", "Pittsburgh", "Cincinnati",
]

STREET_NAMES = [
    "Main", "Oak", "Pine", "Maple", "Cedar", "Elm", "Washington", "Lake",
    "Hill", "Park", "Sunset", "Lincoln", "Church", "Market", "Broadway",
    "Chestnut", "Spring", "Walnut", "Highland", "Madison",
]

STREET_SUFFIXES = ["St", "Ave", "Blvd", "Rd", "Ln", "Dr", "Way", "Pl"]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helpers: weighted picker via seeded uniform

# COMMAND ----------

def weighted_pick(col, values, weights):
    """Return a Spark Column that picks from `values` using cumulative `weights`.

    Uses cumulative thresholds + chained `when`. `col` must be a uniform [0,1) Column.
    """
    assert abs(sum(weights) - 1.0) < 1e-6, "weights must sum to 1.0"
    cumulative = 0.0
    expr = None
    for v, w in zip(values[:-1], weights[:-1]):
        cumulative += w
        cond = col < F.lit(cumulative)
        expr = F.when(cond, F.lit(v)) if expr is None else expr.when(cond, F.lit(v))
    return expr.otherwise(F.lit(values[-1]))


def array_pick(col, values):
    """Pick an element from `values` using `col` (uniform int or float).

    Implemented via array + element_at for scale-friendliness.
    """
    arr = F.array(*[F.lit(v) for v in values])
    idx = (F.abs(col) % F.lit(len(values))).cast("int") + F.lit(1)
    return F.element_at(arr, idx)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Arenas (10K)

# COMMAND ----------

arenas_df = (
    spark.range(0, NUM_ARENAS, numPartitions=16)
    .withColumn("arena_id", F.concat(F.lit("ARN-"), F.lpad(F.col("id").cast("string"), 5, "0")))
    .withColumn("r_tier", F.rand(seed=SEED))
    .withColumn("tier", weighted_pick(F.col("r_tier"), TIERS, TIER_WEIGHTS))
    .withColumn(
        "arena_name",
        F.concat(
            array_pick(F.col("id") * F.lit(7919), FUND_SPONSORS),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(6997), ["Capital", "Ventures", "Holdings", "Group", "Partners"]),
        ),
    )
    .withColumn(
        "created_at",
        (F.lit("2020-01-01 00:00:00").cast(TimestampType()).cast("long")
         + (F.col("id") * F.lit(3600)).cast("long")).cast(TimestampType()),
    )
    .select(
        F.col("arena_id").cast(StringType()),
        F.col("arena_name").cast(StringType()),
        F.col("tier").cast(StringType()),
        F.col("created_at").cast(TimestampType()),
    )
)

arenas_path = f"{OUTPUT_ROOT}/arenas"
# Single JSON file: coalesce(1) is safe here — 10K rows is tiny.
(
    arenas_df.coalesce(1)
    .write.mode("overwrite")
    .option("ignoreNullFields", "false")
    .json(arenas_path)
)
print(f"Arenas written to {arenas_path}")

# Rename Spark's part-file to arenas.json for the Auto Loader demo layout.
try:
    files = [f for f in dbutils.fs.ls(arenas_path) if f.name.startswith("part-") and f.name.endswith(".json")]
    if len(files) == 1:
        dbutils.fs.mv(files[0].path, f"{arenas_path}/arenas.json")
except Exception as e:
    print(f"Arena part-file rename skipped: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Funds (5 per arena)

# COMMAND ----------

funds_df = (
    spark.range(0, NUM_FUNDS, numPartitions=32)
    .withColumn("fund_id", F.concat(F.lit("FND-"), F.lpad(F.col("id").cast("string"), 6, "0")))
    .withColumn("arena_idx", (F.col("id") / F.lit(FUNDS_PER_ARENA)).cast("long"))
    .withColumn(
        "arena_id",
        F.concat(F.lit("ARN-"), F.lpad(F.col("arena_idx").cast("string"), 5, "0")),
    )
    .withColumn("r_strategy", F.rand(seed=SEED + 1))
    .withColumn("strategy", weighted_pick(F.col("r_strategy"), STRATEGIES, STRATEGY_WEIGHTS))
    .withColumn("r_vintage", F.rand(seed=SEED + 2))
    .withColumn(
        "vintage_year",
        (F.lit(2015) + (F.col("r_vintage") * F.lit(11)).cast("int")).cast(IntegerType()),
    )
    # Strategy-dependent target return: use strategy ranges
    .withColumn("r_return", F.rand(seed=SEED + 3))
    .withColumn(
        "target_return_pct",
        F.when(F.col("strategy") == "core", F.lit(6.0) + F.col("r_return") * F.lit(4.0))
        .when(F.col("strategy") == "value_add", F.lit(10.0) + F.col("r_return") * F.lit(6.0))
        .when(F.col("strategy") == "opportunistic", F.lit(16.0) + F.col("r_return") * F.lit(9.0))
        .otherwise(F.lit(6.0) + F.col("r_return") * F.lit(6.0)),
    )
    # AUM lognormal approximation: exp(20 + 1.2 * std_normal)
    # Approximate std-normal with sum of 12 uniforms - 6 (CLT).
    .withColumn("u1", F.rand(seed=SEED + 4))
    .withColumn("u2", F.rand(seed=SEED + 5))
    .withColumn("u3", F.rand(seed=SEED + 6))
    .withColumn("u4", F.rand(seed=SEED + 7))
    .withColumn("u5", F.rand(seed=SEED + 8))
    .withColumn("u6", F.rand(seed=SEED + 9))
    .withColumn("u7", F.rand(seed=SEED + 10))
    .withColumn("u8", F.rand(seed=SEED + 11))
    .withColumn("u9", F.rand(seed=SEED + 12))
    .withColumn("u10", F.rand(seed=SEED + 13))
    .withColumn("u11", F.rand(seed=SEED + 14))
    .withColumn("u12", F.rand(seed=SEED + 15))
    .withColumn(
        "z_norm",
        F.col("u1") + F.col("u2") + F.col("u3") + F.col("u4") + F.col("u5") + F.col("u6")
        + F.col("u7") + F.col("u8") + F.col("u9") + F.col("u10") + F.col("u11") + F.col("u12")
        - F.lit(6.0),
    )
    .withColumn("raw_aum", F.exp(F.lit(20.0) + F.lit(1.2) * F.col("z_norm")))
    .withColumn(
        "aum",
        F.least(F.greatest(F.col("raw_aum"), F.lit(50_000_000.0)), F.lit(5_000_000_000.0)),
    )
    .withColumn("r_status", F.rand(seed=SEED + 16))
    .withColumn("status", F.when(F.col("r_status") < F.lit(0.90), F.lit("active")).otherwise(F.lit("closed")))
    .withColumn(
        "fund_name",
        F.concat(
            array_pick(F.col("id") * F.lit(3301), FUND_SPONSORS),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(2207), FUND_SUFFIXES),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(1409), ROMAN_NUMERALS),
        ),
    )
    .select(
        F.col("fund_id").cast(StringType()),
        F.col("arena_id").cast(StringType()),
        F.col("fund_name").cast(StringType()),
        F.col("vintage_year").cast(IntegerType()),
        F.col("strategy").cast(StringType()),
        F.round(F.col("target_return_pct"), 1).cast(DoubleType()).alias("target_return_pct"),
        F.col("aum").cast(DoubleType()),
        F.col("status").cast(StringType()),
    )
)

funds_path = f"{OUTPUT_ROOT}/funds"
(
    funds_df.coalesce(1)
    .write.mode("overwrite")
    .option("ignoreNullFields", "false")
    .json(funds_path)
)
print(f"Funds written to {funds_path}")

try:
    files = [f for f in dbutils.fs.ls(funds_path) if f.name.startswith("part-") and f.name.endswith(".json")]
    if len(files) == 1:
        dbutils.fs.mv(files[0].path, f"{funds_path}/funds.json")
except Exception as e:
    print(f"Funds part-file rename skipped: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Investors (50 per arena, each committed to 1–3 funds within their arena)

# COMMAND ----------

# One investor row per (investor, fund commitment), 1–3 funds per investor within their arena.
# We materialize at the investor level first, then explode-join to funds in-arena.

investors_base = (
    spark.range(0, NUM_INVESTORS, numPartitions=64)
    .withColumn("investor_id", F.concat(F.lit("INV-"), F.lpad(F.col("id").cast("string"), 7, "0")))
    .withColumn("arena_idx", (F.col("id") / F.lit(INVESTORS_PER_ARENA)).cast("long"))
    .withColumn(
        "arena_id",
        F.concat(F.lit("ARN-"), F.lpad(F.col("arena_idx").cast("string"), 5, "0")),
    )
    .withColumn("r_type", F.rand(seed=SEED + 20))
    .withColumn("type", weighted_pick(F.col("r_type"), INVESTOR_TYPES, INVESTOR_TYPE_WEIGHTS))
    .withColumn("r_commit", F.rand(seed=SEED + 21))
    # Rough commitment sizing: scale by type
    .withColumn(
        "commitment_amount",
        F.when(F.col("type") == "institutional", F.lit(5_000_000.0) + F.col("r_commit") * F.lit(45_000_000.0))
        .when(F.col("type") == "family_office", F.lit(1_000_000.0) + F.col("r_commit") * F.lit(19_000_000.0))
        .when(F.col("type") == "hnwi", F.lit(100_000.0) + F.col("r_commit") * F.lit(4_900_000.0))
        .when(F.col("type") == "endowment", F.lit(2_000_000.0) + F.col("r_commit") * F.lit(28_000_000.0))
        .otherwise(F.lit(10_000_000.0) + F.col("r_commit") * F.lit(40_000_000.0)),
    )
    .withColumn("r_numfunds", F.rand(seed=SEED + 22))
    .withColumn(
        "num_funds",
        F.when(F.col("r_numfunds") < F.lit(0.50), F.lit(1))
        .when(F.col("r_numfunds") < F.lit(0.85), F.lit(2))
        .otherwise(F.lit(3)),
    )
    # Pick a fund-slot offset within the arena (0..4) for first fund; extra funds step +1
    .withColumn("r_fundslot", F.rand(seed=SEED + 23))
    .withColumn("fund_slot0", (F.col("r_fundslot") * F.lit(FUNDS_PER_ARENA)).cast("int"))
    .withColumn(
        "city",
        array_pick(F.col("id") * F.lit(5381), CITIES),
    )
    .withColumn(
        "state",
        array_pick(F.col("id") * F.lit(4723), US_STATES),
    )
    .withColumn(
        "investor_name",
        F.concat(
            array_pick(F.col("id") * F.lit(7879), [
                "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
                "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
                "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Nancy", "Daniel", "Lisa",
                "Matthew", "Margaret", "Anthony", "Betty", "Mark", "Sandra",
            ]),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(6151), [
                "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
                "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
                "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
                "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
            ]),
        ),
    )
)

# Explode each investor to num_funds rows with offsets 0..num_funds-1, clamp within arena
investors_exploded = (
    investors_base
    .withColumn("offset", F.explode(F.sequence(F.lit(0), F.col("num_funds") - F.lit(1))))
    .withColumn("fund_slot", (F.col("fund_slot0") + F.col("offset")) % F.lit(FUNDS_PER_ARENA))
    .withColumn("fund_global_idx", F.col("arena_idx") * F.lit(FUNDS_PER_ARENA) + F.col("fund_slot"))
    .withColumn(
        "fund_id",
        F.concat(F.lit("FND-"), F.lpad(F.col("fund_global_idx").cast("string"), 6, "0")),
    )
    .select(
        F.col("investor_id").cast(StringType()),
        F.col("arena_id").cast(StringType()),
        F.col("investor_name").cast(StringType()),
        F.col("type").cast(StringType()),
        F.round(F.col("commitment_amount"), 2).cast(DoubleType()).alias("commitment_amount"),
        F.col("fund_id").cast(StringType()),
        F.col("city").cast(StringType()),
        F.col("state").cast(StringType()),
    )
)

investors_path = f"{OUTPUT_ROOT}/investors"
(
    investors_exploded.coalesce(4)  # small number of CSV files
    .write.mode("overwrite")
    .option("header", "true")
    .option("escape", '"')
    .csv(investors_path)
)
print(f"Investors written to {investors_path}")

# Consolidate to a single investors.csv if feasible (optional convenience).
# For large investor counts (>1M rows) keep multiple files.
if NUM_INVESTORS <= 1_000_000:
    try:
        csv_parts = [
            f for f in dbutils.fs.ls(investors_path)
            if f.name.startswith("part-") and f.name.endswith(".csv")
        ]
        if len(csv_parts) == 1:
            dbutils.fs.mv(csv_parts[0].path, f"{investors_path}/investors.csv")
    except Exception as e:
        print(f"Investors part-file rename skipped: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Properties (10 per arena)

# COMMAND ----------

properties_df = (
    spark.range(0, NUM_PROPERTIES, numPartitions=64)
    .withColumn("property_id", F.concat(F.lit("PRP-"), F.lpad(F.col("id").cast("string"), 6, "0")))
    .withColumn("arena_idx", (F.col("id") / F.lit(PROPERTIES_PER_ARENA)).cast("long"))
    .withColumn(
        "arena_id",
        F.concat(F.lit("ARN-"), F.lpad(F.col("arena_idx").cast("string"), 5, "0")),
    )
    # Each arena has FUNDS_PER_ARENA funds; assign property to one of them.
    # Use the SAME deterministic hash the GL generator uses (see section 5 below) so that
    # GL.fund_id == properties.fund_id for a given property_id — prevents orphaned fund joins in gold.
    .withColumn("fund_slot", (F.abs(F.hash(F.col("id"), F.lit("fundslot")).cast("long")) % F.lit(FUNDS_PER_ARENA)))
    .withColumn("fund_global_idx", F.col("arena_idx") * F.lit(FUNDS_PER_ARENA) + F.col("fund_slot"))
    .withColumn(
        "fund_id",
        F.concat(F.lit("FND-"), F.lpad(F.col("fund_global_idx").cast("string"), 6, "0")),
    )
    .withColumn("r_ptype", F.rand(seed=SEED + 31))
    .withColumn("property_type", weighted_pick(F.col("r_ptype"), PROPERTY_TYPES, PROPERTY_TYPE_WEIGHTS))
    .withColumn(
        "property_name",
        F.concat(
            F.lit("The "),
            array_pick(F.col("id") * F.lit(9973), BUILDING_PREFIXES),
            F.lit(" at "),
            array_pick(F.col("id") * F.lit(8831), NEIGHBORHOODS),
        ),
    )
    .withColumn(
        "address",
        F.concat(
            ((F.abs(F.hash(F.col("id")).cast("long")) % F.lit(9999)) + F.lit(1)).cast("string"),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(2179), STREET_NAMES),
            F.lit(" "),
            array_pick(F.col("id") * F.lit(1973), STREET_SUFFIXES),
        ),
    )
    .withColumn("city", array_pick(F.col("id") * F.lit(5381), CITIES))
    .withColumn("state", array_pick(F.col("id") * F.lit(4723), US_STATES))
    .withColumn("r_acq", F.rand(seed=SEED + 32))
    .withColumn(
        "acq_year",
        (F.lit(2016) + (F.col("r_acq") * F.lit(10)).cast("int")),
    )
    .withColumn("r_acq_day", F.rand(seed=SEED + 33))
    .withColumn(
        "acq_doy",
        (F.col("r_acq_day") * F.lit(365)).cast("int"),
    )
    .withColumn(
        "acquisition_date",
        F.expr("date_add(make_date(acq_year, 1, 1), acq_doy)").cast(DateType()),
    )
    # Lognormal approximation for acquisition_price ≈ exp(17 + 1.0 * std_normal)
    .withColumn("u1", F.rand(seed=SEED + 40))
    .withColumn("u2", F.rand(seed=SEED + 41))
    .withColumn("u3", F.rand(seed=SEED + 42))
    .withColumn("u4", F.rand(seed=SEED + 43))
    .withColumn("u5", F.rand(seed=SEED + 44))
    .withColumn("u6", F.rand(seed=SEED + 45))
    .withColumn("u7", F.rand(seed=SEED + 46))
    .withColumn("u8", F.rand(seed=SEED + 47))
    .withColumn("u9", F.rand(seed=SEED + 48))
    .withColumn("u10", F.rand(seed=SEED + 49))
    .withColumn("u11", F.rand(seed=SEED + 50))
    .withColumn("u12", F.rand(seed=SEED + 51))
    .withColumn(
        "z",
        F.col("u1") + F.col("u2") + F.col("u3") + F.col("u4") + F.col("u5") + F.col("u6")
        + F.col("u7") + F.col("u8") + F.col("u9") + F.col("u10") + F.col("u11") + F.col("u12")
        - F.lit(6.0),
    )
    .withColumn("raw_price", F.exp(F.lit(17.0) + F.lit(1.0) * F.col("z")))
    .withColumn(
        "acquisition_price",
        F.least(F.greatest(F.col("raw_price"), F.lit(5_000_000.0)), F.lit(500_000_000.0)),
    )
    .withColumn("r_mult", F.rand(seed=SEED + 55))
    .withColumn(
        "current_valuation",
        F.col("acquisition_price") * (F.lit(0.8) + F.col("r_mult") * F.lit(0.8)),
    )
    .withColumn("r_sqft", F.rand(seed=SEED + 56))
    .withColumn(
        "square_footage",
        F.when(
            F.col("property_type") == "multifamily",
            F.lit(50_000) + (F.col("r_sqft") * F.lit(350_000)).cast("int"),
        )
        .when(
            F.col("property_type") == "office",
            F.lit(30_000) + (F.col("r_sqft") * F.lit(470_000)).cast("int"),
        )
        .when(
            F.col("property_type") == "industrial",
            F.lit(100_000) + (F.col("r_sqft") * F.lit(400_000)).cast("int"),
        )
        .when(
            F.col("property_type") == "retail",
            F.lit(10_000) + (F.col("r_sqft") * F.lit(140_000)).cast("int"),
        )
        .otherwise(F.lit(50_000) + (F.col("r_sqft") * F.lit(250_000)).cast("int")),
    )
    .withColumn("r_occ", F.rand(seed=SEED + 57))
    .withColumn("occupancy_rate", F.lit(0.70) + F.col("r_occ") * F.lit(0.29))
    .select(
        F.col("property_id").cast(StringType()),
        F.col("arena_id").cast(StringType()),
        F.col("fund_id").cast(StringType()),
        F.col("property_name").cast(StringType()),
        F.col("address").cast(StringType()),
        F.col("city").cast(StringType()),
        F.col("state").cast(StringType()),
        F.col("property_type").cast(StringType()),
        F.col("acquisition_date").cast(DateType()),
        F.round(F.col("acquisition_price"), 2).cast(DoubleType()).alias("acquisition_price"),
        F.round(F.col("current_valuation"), 2).cast(DoubleType()).alias("current_valuation"),
        F.col("square_footage").cast(IntegerType()),
        F.round(F.col("occupancy_rate"), 2).cast(DoubleType()).alias("occupancy_rate"),
    )
)

properties_path = f"{OUTPUT_ROOT}/properties"
(
    properties_df
    .write.mode("overwrite")
    .parquet(properties_path)
)
print(f"Properties written to {properties_path}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. GL transactions (partitioned by year/month)
# MAGIC
# MAGIC **Partitioning strategy for scale:**
# MAGIC - Source: `spark.range(GL_ROWS_TARGET)` split across many Spark partitions (scaled
# MAGIC   with target row count).
# MAGIC - Every row is assigned deterministically to (year, month) via the start/end date
# MAGIC   window. Writing with `partitionBy("year","month")` produces ~38 output folders,
# MAGIC   each independently consumable. Dynamic partition overwrite keeps re-runs clean.
# MAGIC - Referential integrity: each row derives `(arena_idx, property_slot, fund_slot)`
# MAGIC   from deterministic hashes so `arena_id` on the GL row always matches the
# MAGIC   `arena_id` of the chosen property and fund.
# MAGIC - No `.collect()`, no driver-side pools, no cache/persist. All string pools are
# MAGIC   materialized as array columns on the fly and indexed with `element_at`.

# COMMAND ----------

from datetime import date

_start = date.fromisoformat(START_DATE)
_end = date.fromisoformat(END_DATE)
TOTAL_DAYS = (_end - _start).days + 1
print(f"GL date span: {_start} .. {_end} ({TOTAL_DAYS} days)")

# Scale partitions with target rows: aim ~8M rows per Spark partition.
GL_PARTITIONS = max(256, int(GL_ROWS_TARGET / 8_000_000))
print(f"GL spark partitions: {GL_PARTITIONS}")

# COMMAND ----------

# Build category → account lookup and category → description lookup as in-query arrays.
revenue_codes = [c for c, _ in GL_ACCOUNTS_BY_CATEGORY["revenue"]]
opex_codes = [c for c, _ in GL_ACCOUNTS_BY_CATEGORY["opex"]]
capex_codes = [c for c, _ in GL_ACCOUNTS_BY_CATEGORY["capex"]]
debt_codes = [c for c, _ in GL_ACCOUNTS_BY_CATEGORY["debt_service"]]

revenue_names = [n for _, n in GL_ACCOUNTS_BY_CATEGORY["revenue"]]
opex_names = [n for _, n in GL_ACCOUNTS_BY_CATEGORY["opex"]]
capex_names = [n for _, n in GL_ACCOUNTS_BY_CATEGORY["capex"]]
debt_names = [n for _, n in GL_ACCOUNTS_BY_CATEGORY["debt_service"]]

revenue_descs = DESCRIPTIONS_BY_CATEGORY["revenue"]
opex_descs = DESCRIPTIONS_BY_CATEGORY["opex"]
capex_descs = DESCRIPTIONS_BY_CATEGORY["capex"]
debt_descs = DESCRIPTIONS_BY_CATEGORY["debt_service"]


def pick_from_array(values, idx_col):
    """Pick element from `values` using positive-int idx column."""
    arr = F.array(*[F.lit(v) for v in values])
    idx = (F.abs(idx_col) % F.lit(len(values))).cast("int") + F.lit(1)
    return F.element_at(arr, idx)


gl_raw = (
    spark.range(0, GL_ROWS_TARGET, numPartitions=GL_PARTITIONS)
    # Deterministic hashes derived from id + salt
    .withColumn("h_arena", F.abs(F.hash(F.col("id"), F.lit("arena")).cast("long")))
    .withColumn("h_prop", F.abs(F.hash(F.col("id"), F.lit("prop")).cast("long")))
    .withColumn("h_date", F.abs(F.hash(F.col("id"), F.lit("date")).cast("long")))
    .withColumn("h_cat", F.rand(seed=SEED + 100))
    .withColumn("h_subacct", F.abs(F.hash(F.col("id"), F.lit("subacct")).cast("long")))
    .withColumn("h_desc", F.abs(F.hash(F.col("id"), F.lit("desc")).cast("long")))
    .withColumn("h_amt", F.rand(seed=SEED + 101))
    .withColumn("h_posted", F.rand(seed=SEED + 102))
    # Arena and in-arena property slot
    .withColumn("arena_idx", (F.col("h_arena") % F.lit(NUM_ARENAS)))
    .withColumn("prop_slot", (F.col("h_prop") % F.lit(PROPERTIES_PER_ARENA)))
    .withColumn("property_global_idx", F.col("arena_idx") * F.lit(PROPERTIES_PER_ARENA) + F.col("prop_slot"))
    # Rather than joining 20B rows vs 100K properties, we regenerate the same deterministic
    # fund_slot the properties table used. Properties table also uses hash(id, "fundslot")
    # % FUNDS_PER_ARENA (see section 4) — property_global_idx here == properties.id, so the
    # fund_id we derive for each GL row matches properties.fund_id for the same property_id.
    .withColumn(
        "fund_slot",
        (F.abs(F.hash(F.col("property_global_idx"), F.lit("fundslot")).cast("long")) % F.lit(FUNDS_PER_ARENA)),
    )
    .withColumn("fund_global_idx", F.col("arena_idx") * F.lit(FUNDS_PER_ARENA) + F.col("fund_slot"))
    # Ids
    .withColumn(
        "arena_id",
        F.concat(F.lit("ARN-"), F.lpad(F.col("arena_idx").cast("string"), 5, "0")),
    )
    .withColumn(
        "property_id",
        F.concat(F.lit("PRP-"), F.lpad(F.col("property_global_idx").cast("string"), 6, "0")),
    )
    .withColumn(
        "fund_id",
        F.concat(F.lit("FND-"), F.lpad(F.col("fund_global_idx").cast("string"), 6, "0")),
    )
    .withColumn(
        "transaction_id",
        F.concat(F.lit("TXN-"), F.lpad(F.col("id").cast("string"), 12, "0")),
    )
    # Category
    .withColumn("category", weighted_pick(F.col("h_cat"), CATEGORIES, CATEGORY_WEIGHTS))
    # Account code + name per category (use array_pick on h_subacct)
    .withColumn(
        "account_code",
        F.when(F.col("category") == "revenue", pick_from_array(revenue_codes, F.col("h_subacct")))
        .when(F.col("category") == "opex", pick_from_array(opex_codes, F.col("h_subacct")))
        .when(F.col("category") == "capex", pick_from_array(capex_codes, F.col("h_subacct")))
        .otherwise(pick_from_array(debt_codes, F.col("h_subacct"))),
    )
    .withColumn(
        "account_name",
        F.when(F.col("category") == "revenue", pick_from_array(revenue_names, F.col("h_subacct")))
        .when(F.col("category") == "opex", pick_from_array(opex_names, F.col("h_subacct")))
        .when(F.col("category") == "capex", pick_from_array(capex_names, F.col("h_subacct")))
        .otherwise(pick_from_array(debt_names, F.col("h_subacct"))),
    )
    .withColumn(
        "description",
        F.when(F.col("category") == "revenue", pick_from_array(revenue_descs, F.col("h_desc")))
        .when(F.col("category") == "opex", pick_from_array(opex_descs, F.col("h_desc")))
        .when(F.col("category") == "capex", pick_from_array(capex_descs, F.col("h_desc")))
        .otherwise(pick_from_array(debt_descs, F.col("h_desc"))),
    )
    # Amount (revenue positive, expenses negative; uniform within category range)
    .withColumn(
        "amount",
        F.round(
            F.when(
                F.col("category") == "revenue",
                F.lit(500.0) + F.col("h_amt") * F.lit(149_500.0),
            )
            .when(
                F.col("category") == "opex",
                -(F.lit(100.0) + F.col("h_amt") * F.lit(49_900.0)),
            )
            .when(
                F.col("category") == "capex",
                -(F.lit(1_000.0) + F.col("h_amt") * F.lit(199_000.0)),
            )
            .otherwise(-(F.lit(5_000.0) + F.col("h_amt") * F.lit(95_000.0))),
            2,
        ),
    )
    # Transaction date within [START_DATE, END_DATE]
    .withColumn("day_offset", (F.col("h_date") % F.lit(TOTAL_DAYS)).cast("int"))
    .withColumn(
        "transaction_date",
        F.expr(f"date_add(to_date('{START_DATE}'), day_offset)").cast(DateType()),
    )
    # Posted flag ~98% true
    .withColumn("posted", F.col("h_posted") < F.lit(0.98))
    # Partition columns
    .withColumn("year", F.year(F.col("transaction_date")).cast(IntegerType()))
    .withColumn("month", F.month(F.col("transaction_date")).cast(IntegerType()))
    .select(
        F.col("transaction_id").cast(StringType()),
        F.col("arena_id").cast(StringType()),
        F.col("property_id").cast(StringType()),
        F.col("fund_id").cast(StringType()),
        F.col("account_code").cast(StringType()),
        F.col("account_name").cast(StringType()),
        F.col("description").cast(StringType()),
        F.col("amount").cast(DoubleType()),
        F.col("transaction_date").cast(DateType()),
        F.col("category").cast(StringType()),
        F.col("posted").cast(BooleanType()),
        F.col("year"),
        F.col("month"),
    )
)

gl_path = f"{OUTPUT_ROOT}/gl_transactions"

(
    gl_raw
    .write
    .mode("overwrite")
    .partitionBy("year", "month")
    .parquet(gl_path)
)
print(f"GL transactions written to {gl_path}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verification: row counts and volume listings

# COMMAND ----------

# Spark's default directory read picks up `_committed_*` / `_started_*` DBIO commit
# markers alongside real data files, which trips JSON schema inference. Constrain to
# the actual data-file glob for each format.
print("-- Arenas --")
arenas_count = spark.read.option("pathGlobFilter", "*.json").json(arenas_path).count()
print(f"arenas rows: {arenas_count:,} (expected {NUM_ARENAS:,})")

print("-- Funds --")
funds_count = spark.read.option("pathGlobFilter", "*.json").json(funds_path).count()
print(f"funds rows:  {funds_count:,} (expected {NUM_FUNDS:,})")

print("-- Investors --")
investors_count = (
    spark.read.option("header", "true").option("pathGlobFilter", "*.csv").csv(investors_path).count()
)
print(f"investors rows: {investors_count:,}  (expected between {NUM_INVESTORS:,} and {NUM_INVESTORS*3:,})")

print("-- Properties --")
properties_count = spark.read.option("pathGlobFilter", "*.parquet").parquet(properties_path).count()
print(f"properties rows: {properties_count:,} (expected {NUM_PROPERTIES:,})")

print("-- GL transactions --")
gl_count = spark.read.option("pathGlobFilter", "*.parquet").parquet(gl_path).count()
print(f"gl rows: {gl_count:,} (target {GL_ROWS_TARGET:,})")

# COMMAND ----------

# Volume listings
for sub in ["arenas", "funds", "investors", "properties", "gl_transactions"]:
    path = f"{OUTPUT_ROOT}/{sub}"
    try:
        entries = dbutils.fs.ls(path)
        print(f"\n== {path} ({len(entries)} entries) ==")
        for e in entries[:15]:
            print(f"  {e.name}\t{e.size}")
        if len(entries) > 15:
            print(f"  ... and {len(entries) - 15} more")
    except Exception as e:
        print(f"Could not list {path}: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Next step: hand this output off to the SDP pipeline in `juniper_square_demo_catalog.pipeline`
# MAGIC for bronze/silver/gold curation, then exercise serving-layer concurrency queries.
