"""
Juniper Square Demo: Synthetic Data Generator

Generates realistic real estate investment management data:
  - 50 funds
  - 500 investors
  - 200 properties
  - 2,000,000 GL transactions (split into monthly JSONL files)

Usage:
    python generate_data.py [--output-dir ../output/raw]

Dependencies:
    pip install polars mimesis numpy
"""

import json
import random
import time
from pathlib import Path
from argparse import ArgumentParser

import numpy as np
import polars as pl
from mimesis import Person, Address
from mimesis.locales import Locale

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
person = Person(Locale.EN, seed=SEED)
address = Address(Locale.EN, seed=SEED)

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------
parser = ArgumentParser(description="Generate Juniper Square demo data")
parser.add_argument("--output-dir", type=str, default="../output/raw")
args = parser.parse_args()

OUTPUT_DIR = Path(args.output_dir)

# ---------------------------------------------------------------------------
# GL Chart of Accounts
# ---------------------------------------------------------------------------
GL_ACCOUNTS = {
    "4100": ("Rental Revenue", "revenue"),
    "4200": ("Parking Revenue", "revenue"),
    "4300": ("Other Income", "revenue"),
    "5100": ("Property Management Fee", "opex"),
    "5200": ("Maintenance & Repairs", "opex"),
    "5300": ("Utilities", "opex"),
    "5400": ("Insurance", "opex"),
    "5500": ("Property Tax", "opex"),
    "6100": ("Capital Improvements", "capex"),
    "6200": ("Tenant Improvements", "capex"),
    "7100": ("Mortgage Interest", "debt_service"),
    "7200": ("Loan Principal", "debt_service"),
}

# Category weights map to account codes
CATEGORY_ACCOUNTS = {
    "revenue": ["4100", "4200", "4300"],
    "opex": ["5100", "5200", "5300", "5400", "5500"],
    "capex": ["6100", "6200"],
    "debt_service": ["7100", "7200"],
}

CATEGORY_WEIGHTS = [0.35, 0.30, 0.20, 0.15]  # revenue, opex, capex, debt_service
CATEGORIES = ["revenue", "opex", "capex", "debt_service"]

# ---------------------------------------------------------------------------
# Property name building blocks
# ---------------------------------------------------------------------------
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

PROPERTY_TYPE_TEMPLATES = {
    "multifamily": [
        "The {prefix} at {nbhd}",
        "The Residences at {nbhd}",
        "{prefix} Apartments",
        "{nbhd} Living",
    ],
    "office": [
        "{prefix} Office Tower",
        "{nbhd} Corporate Center",
        "{prefix} Business Park",
    ],
    "industrial": [
        "{nbhd} Industrial Park",
        "{prefix} Logistics Center",
        "{prefix} Distribution Hub",
    ],
    "retail": [
        "The Shops at {nbhd}",
        "{nbhd} Town Center",
        "{prefix} Market",
    ],
    "mixed_use": [
        "{prefix} at {nbhd}",
        "The {prefix}",
        "{nbhd} Mixed-Use",
    ],
}


def generate_property_name(prop_type: str) -> str:
    template = random.choice(PROPERTY_TYPE_TEMPLATES[prop_type])
    return template.format(
        prefix=random.choice(BUILDING_PREFIXES),
        nbhd=random.choice(NEIGHBORHOODS),
    )


# ---------------------------------------------------------------------------
# Fund name generator
# ---------------------------------------------------------------------------
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

ROMAN_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
                  "XI", "XII", "XIII", "XIV", "XV"]


def generate_fund_name() -> str:
    sponsor = random.choice(FUND_SPONSORS)
    suffix = random.choice(FUND_SUFFIXES)
    numeral = random.choice(ROMAN_NUMERALS)
    return f"{sponsor} {suffix} {numeral}"


# ---------------------------------------------------------------------------
# Strategy parameters
# ---------------------------------------------------------------------------
STRATEGIES = ["core", "value_add", "opportunistic", "debt"]
STRATEGY_WEIGHTS = [0.30, 0.30, 0.25, 0.15]
STRATEGY_RETURN_RANGES = {
    "core": (6, 10),
    "value_add": (10, 16),
    "opportunistic": (16, 25),
    "debt": (6, 12),
}

INVESTOR_TYPES = ["institutional", "family_office", "hnwi", "endowment", "pension"]
INVESTOR_TYPE_WEIGHTS = [0.25, 0.25, 0.30, 0.10, 0.10]
INVESTOR_COMMITMENT_RANGES = {
    "institutional": (5_000_000, 50_000_000),
    "family_office": (1_000_000, 20_000_000),
    "hnwi": (100_000, 5_000_000),
    "endowment": (2_000_000, 30_000_000),
    "pension": (10_000_000, 50_000_000),
}

PROPERTY_TYPES = ["multifamily", "office", "industrial", "retail", "mixed_use"]
PROPERTY_TYPE_WEIGHTS = [0.35, 0.20, 0.20, 0.15, 0.10]
SQFT_RANGES = {
    "multifamily": (50_000, 400_000),
    "office": (30_000, 500_000),
    "industrial": (100_000, 500_000),
    "retail": (10_000, 150_000),
    "mixed_use": (50_000, 300_000),
}

# ---------------------------------------------------------------------------
# Helper: write JSONL
# ---------------------------------------------------------------------------
def write_jsonl(df: pl.DataFrame, path: Path) -> int:
    """Write a Polars DataFrame to a JSONL file. Returns byte size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = df.to_dicts()
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
    return path.stat().st_size


# ---------------------------------------------------------------------------
# 1. Generate Funds
# ---------------------------------------------------------------------------
def generate_funds(n: int = 50) -> pl.DataFrame:
    print(f"Generating {n} funds...")
    strategies = random.choices(STRATEGIES, weights=STRATEGY_WEIGHTS, k=n)
    vintage_years = random.choices(range(2015, 2026), weights=range(1, 12), k=n)

    records = []
    used_names = set()
    for i in range(n):
        strat = strategies[i]
        lo, hi = STRATEGY_RETURN_RANGES[strat]
        target_return = round(random.uniform(lo, hi), 1)
        aum = round(np.random.lognormal(mean=20, sigma=1.2))
        aum = int(np.clip(aum, 50_000_000, 5_000_000_000))

        name = generate_fund_name()
        while name in used_names:
            name = generate_fund_name()
        used_names.add(name)

        records.append({
            "fund_id": f"FND-{i+1:03d}",
            "fund_name": name,
            "vintage_year": vintage_years[i],
            "strategy": strat,
            "target_return_pct": target_return,
            "aum": aum,
            "status": random.choices(["active", "closed"], weights=[0.90, 0.10])[0],
        })
    return pl.DataFrame(records)


# ---------------------------------------------------------------------------
# 2. Generate Investors
# ---------------------------------------------------------------------------
def generate_investors(n: int = 500, fund_ids: list[str] = None) -> pl.DataFrame:
    print(f"Generating {n} investors...")
    records = []
    for i in range(n):
        inv_type = random.choices(INVESTOR_TYPES, weights=INVESTOR_TYPE_WEIGHTS)[0]
        lo, hi = INVESTOR_COMMITMENT_RANGES[inv_type]
        commitment = int(np.random.lognormal(
            mean=np.log((lo + hi) / 3), sigma=0.6
        ))
        commitment = int(np.clip(commitment, lo, hi))

        num_funds = random.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0]
        investor_funds = random.sample(fund_ids, min(num_funds, len(fund_ids)))

        for fund_id in investor_funds:
            records.append({
                "investor_id": f"INV-{i+1:04d}",
                "investor_name": person.full_name(),
                "type": inv_type,
                "commitment_amount": commitment,
                "fund_id": fund_id,
                "city": address.city(),
                "state": address.state(abbr=True),
            })
    return pl.DataFrame(records)


# ---------------------------------------------------------------------------
# 3. Generate Properties
# ---------------------------------------------------------------------------
def generate_properties(n: int = 200, fund_ids: list[str] = None) -> pl.DataFrame:
    print(f"Generating {n} properties...")
    records = []
    used_names = set()

    for i in range(n):
        prop_type = random.choices(PROPERTY_TYPES, weights=PROPERTY_TYPE_WEIGHTS)[0]

        name = generate_property_name(prop_type)
        attempts = 0
        while name in used_names and attempts < 50:
            name = generate_property_name(prop_type)
            attempts += 1
        used_names.add(name)

        acq_year = random.randint(2016, 2025)
        acq_month = random.randint(1, 12)
        acq_day = random.randint(1, 28)
        acq_date = f"{acq_year}-{acq_month:02d}-{acq_day:02d}"

        acq_price = int(np.random.lognormal(mean=17, sigma=1.0))
        acq_price = int(np.clip(acq_price, 5_000_000, 500_000_000))
        multiplier = round(random.uniform(0.8, 1.6), 3)
        current_val = int(acq_price * multiplier)

        sqft_lo, sqft_hi = SQFT_RANGES[prop_type]
        sqft = random.randint(sqft_lo, sqft_hi)
        occupancy = round(random.uniform(0.70, 0.99), 2)

        records.append({
            "property_id": f"PROP-{i+1:03d}",
            "fund_id": random.choice(fund_ids),
            "property_name": name,
            "address": address.address(),
            "city": address.city(),
            "state": address.state(abbr=True),
            "property_type": prop_type,
            "acquisition_date": acq_date,
            "acquisition_price": acq_price,
            "current_valuation": current_val,
            "square_footage": sqft,
            "occupancy_rate": occupancy,
        })
    return pl.DataFrame(records)


# ---------------------------------------------------------------------------
# 4. Generate GL Transactions (vectorized, chunked)
# ---------------------------------------------------------------------------
def generate_gl_transactions(
    n: int = 2_000_000,
    property_ids: list[str] = None,
    property_fund_map: dict[str, str] = None,
) -> dict[str, pl.DataFrame]:
    """Generate GL transactions, returning a dict of month_key -> DataFrame."""
    print(f"Generating {n:,} GL transactions (vectorized)...")
    t0 = time.time()

    # Pre-compute property -> fund mapping arrays
    prop_arr = np.array(property_ids)

    # Vectorized generation
    prop_indices = np.random.randint(0, len(property_ids), size=n)
    chosen_props = prop_arr[prop_indices]
    chosen_funds = np.array([property_fund_map[p] for p in chosen_props])

    # Categories
    cat_indices = np.random.choice(len(CATEGORIES), size=n, p=CATEGORY_WEIGHTS)
    chosen_categories = np.array(CATEGORIES)[cat_indices]

    # Account codes per category
    account_codes = np.empty(n, dtype="U4")
    for cat_idx, cat in enumerate(CATEGORIES):
        mask = cat_indices == cat_idx
        codes = CATEGORY_ACCOUNTS[cat]
        account_codes[mask] = np.random.choice(codes, size=mask.sum())

    account_names = np.array([GL_ACCOUNTS[c][0] for c in account_codes])

    # Amounts: revenue positive, expenses negative
    amounts = np.zeros(n)
    revenue_mask = cat_indices == 0
    opex_mask = cat_indices == 1
    capex_mask = cat_indices == 2
    debt_mask = cat_indices == 3

    amounts[revenue_mask] = np.round(np.random.uniform(500, 150_000, size=revenue_mask.sum()), 2)
    amounts[opex_mask] = -np.round(np.random.uniform(100, 50_000, size=opex_mask.sum()), 2)
    amounts[capex_mask] = -np.round(np.random.uniform(1_000, 200_000, size=capex_mask.sum()), 2)
    amounts[debt_mask] = -np.round(np.random.uniform(5_000, 100_000, size=debt_mask.sum()), 2)

    # Transaction dates: 2023-01-01 to 2026-03-31 (1186 days)
    start_date = np.datetime64("2023-01-01")
    end_date = np.datetime64("2026-03-31")
    total_days = int((end_date - start_date) / np.timedelta64(1, "D")) + 1
    day_offsets = np.random.randint(0, total_days, size=n)
    dates = start_date + day_offsets.astype("timedelta64[D]")

    # Posted flag (98% true)
    posted = np.random.random(n) < 0.98

    # Description templates per category
    DESCRIPTIONS = {
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

    # Build description array
    descriptions = np.empty(n, dtype=object)
    for cat_idx, cat in enumerate(CATEGORIES):
        mask = cat_indices == cat_idx
        descs = DESCRIPTIONS[cat]
        descriptions[mask] = np.random.choice(descs, size=mask.sum())

    # Build the full DataFrame
    df = pl.DataFrame({
        "transaction_id": [f"TXN-{i+1:08d}" for i in range(n)],
        "property_id": chosen_props.tolist(),
        "fund_id": chosen_funds.tolist(),
        "account_code": account_codes.tolist(),
        "account_name": account_names.tolist(),
        "description": descriptions.tolist(),
        "amount": amounts.tolist(),
        "transaction_date": [str(d) for d in dates],
        "category": chosen_categories.tolist(),
        "posted": posted.tolist(),
    })

    # Split by month
    df = df.with_columns(
        pl.col("transaction_date").str.slice(0, 7).alias("month_key")
    )

    monthly = {}
    for month_key in sorted(df["month_key"].unique().to_list()):
        monthly[month_key] = df.filter(pl.col("month_key") == month_key).drop("month_key")

    elapsed = time.time() - t0
    print(f"  GL transactions generated in {elapsed:.1f}s")
    return monthly


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    total_start = time.time()
    total_bytes = 0

    # --- Funds ---
    funds_df = generate_funds(50)
    fund_ids = funds_df["fund_id"].to_list()
    path = OUTPUT_DIR / "funds" / "funds.jsonl"
    size = write_jsonl(funds_df, path)
    total_bytes += size
    print(f"  -> {path} ({size:,} bytes)")

    # --- Investors ---
    investors_df = generate_investors(500, fund_ids)
    path = OUTPUT_DIR / "investors" / "investors.jsonl"
    size = write_jsonl(investors_df, path)
    total_bytes += size
    print(f"  -> {path} ({size:,} bytes)")

    # --- Properties ---
    properties_df = generate_properties(200, fund_ids)
    property_ids = properties_df["property_id"].to_list()
    property_fund_map = dict(
        zip(
            properties_df["property_id"].to_list(),
            properties_df["fund_id"].to_list(),
        )
    )
    path = OUTPUT_DIR / "properties" / "properties.jsonl"
    size = write_jsonl(properties_df, path)
    total_bytes += size
    print(f"  -> {path} ({size:,} bytes)")

    # --- GL Transactions ---
    monthly_dfs = generate_gl_transactions(
        n=2_000_000,
        property_ids=property_ids,
        property_fund_map=property_fund_map,
    )
    gl_dir = OUTPUT_DIR / "gl_transactions"
    gl_file_count = 0
    gl_row_count = 0
    for month_key, df in monthly_dfs.items():
        path = gl_dir / f"gl_transactions_{month_key}.jsonl"
        size = write_jsonl(df, path)
        total_bytes += size
        gl_file_count += 1
        gl_row_count += len(df)

    print(f"  -> {gl_dir}/ ({gl_file_count} monthly files, {gl_row_count:,} rows)")

    # --- Summary ---
    elapsed = time.time() - total_start
    print()
    print("=" * 60)
    print("GENERATION SUMMARY")
    print("=" * 60)
    print(f"  Funds:           {len(funds_df):>10,} rows")
    print(f"  Investors:       {len(investors_df):>10,} rows  (one row per fund membership)")
    print(f"  Properties:      {len(properties_df):>10,} rows")
    print(f"  GL Transactions: {gl_row_count:>10,} rows  ({gl_file_count} monthly files)")
    print(f"  Total file size: {total_bytes / 1_048_576:>10.1f} MB")
    print(f"  Output dir:      {OUTPUT_DIR.resolve()}")
    print(f"  Elapsed time:    {elapsed:>10.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
