"""Static catalog of the six benchmark queries shown on the Data Latency page.

Kept in sync with `juniper-concurrency-demo/benchmark/config.yaml`. The app reads
these at render time so a presenter can walk through the exact SQL that produced
the measured latencies, with both the Spark SQL (DBSQL) and Postgres (Lakebase)
variants visible side by side.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BenchmarkQuery:
    name: str                 # Internal name stored in benchmark_summary
    display_name: str         # What we show in pickers and headers
    category: str             # "Reporting" or "Ad-hoc"
    weight: int               # Mix weight in the round-robin
    summary: str              # One-line plain-English description
    sql_dbsql: str            # Spark SQL as sent to DBSQL Serverless
    sql_lakebase: str         # Postgres as sent to Lakebase


BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    BenchmarkQuery(
        name="fund_performance_arena",
        display_name="Q1 — Fund performance for an arena",
        category="Reporting",
        weight=2,
        summary="Single-arena fund summary card. One row per fund, sorted by AUM.",
        sql_dbsql="""SELECT *
FROM juniper_square_demo_catalog.pipeline.gold_fund_performance
WHERE arena_id = '{arena_id}'
ORDER BY total_aum DESC""",
        sql_lakebase="""SELECT *
FROM serving.gold_fund_performance
WHERE arena_id = '{arena_id}'
ORDER BY total_aum DESC""",
    ),
    BenchmarkQuery(
        name="gl_monthly_by_arena_year",
        display_name="Q2 — GL monthly rollup, last 12 months",
        category="Ad-hoc",
        weight=3,
        summary="Ad-hoc Looker-style slice: monthly totals by category for one arena over a rolling year.",
        sql_dbsql="""SELECT
  category,
  month,
  SUM(total_amount) AS total_amount,
  SUM(transaction_count) AS transaction_count,
  ROUND(AVG(avg_transaction_amount), 2) AS avg_transaction_amount
FROM juniper_square_demo_catalog.pipeline.gold_gl_monthly_summary
WHERE arena_id = '{arena_id}'
  AND month >= add_months(current_date(), -12)
GROUP BY category, month
ORDER BY month, category""",
        sql_lakebase="""SELECT
  category,
  month,
  SUM(total_amount) AS total_amount,
  SUM(transaction_count) AS transaction_count,
  ROUND(AVG(avg_transaction_amount)::numeric, 2) AS avg_transaction_amount
FROM serving.gold_gl_monthly_summary
WHERE arena_id = '{arena_id}'
  AND month >= (current_date - interval '12 months')
GROUP BY category, month
ORDER BY month, category""",
    ),
    BenchmarkQuery(
        name="property_financials_joined",
        display_name="Q3 — Property financials + arena join",
        category="Ad-hoc",
        weight=3,
        summary="Property P&L for the last 6 months, joined to the arena dimension. LIMIT 100.",
        sql_dbsql="""SELECT
  pf.property_id,
  pf.property_name,
  pf.property_type,
  a.arena_name,
  pf.fund_name,
  pf.month,
  pf.revenue,
  pf.expenses,
  pf.noi,
  pf.occupancy_rate
FROM juniper_square_demo_catalog.pipeline.gold_property_financials pf
JOIN juniper_square_demo_catalog.pipeline.silver_arenas a
  ON a.arena_id = pf.arena_id
WHERE pf.arena_id = '{arena_id}'
  AND pf.month >= add_months(current_date(), -6)
ORDER BY pf.month DESC, pf.noi DESC
LIMIT 100""",
        sql_lakebase="""SELECT
  pf.property_id,
  pf.property_name,
  pf.property_type,
  a.arena_name,
  pf.fund_name,
  pf.month,
  pf.revenue,
  pf.expenses,
  pf.noi,
  pf.occupancy_rate
FROM serving.gold_property_financials pf
JOIN serving.silver_arenas a
  ON a.arena_id = pf.arena_id
WHERE pf.arena_id = '{arena_id}'
  AND pf.month >= (current_date - interval '6 months')
ORDER BY pf.month DESC, pf.noi DESC
LIMIT 100""",
    ),
    BenchmarkQuery(
        name="top10_properties_by_revenue",
        display_name="Q4 — Top-10 properties by trailing revenue",
        category="Reporting",
        weight=2,
        summary="Rank-aggregation: 12-month trailing revenue per property, return top 10 by rank.",
        sql_dbsql="""WITH ttm AS (
  SELECT
    property_id,
    property_name,
    property_type,
    fund_name,
    SUM(revenue) AS revenue_ttm,
    SUM(noi) AS noi_ttm,
    AVG(occupancy_rate) AS avg_occupancy
  FROM juniper_square_demo_catalog.pipeline.gold_property_financials
  WHERE arena_id = '{arena_id}'
    AND month >= add_months(current_date(), -12)
  GROUP BY property_id, property_name, property_type, fund_name
),
ranked AS (
  SELECT
    ttm.*,
    RANK() OVER (ORDER BY revenue_ttm DESC) AS revenue_rank
  FROM ttm
)
SELECT *
FROM ranked
WHERE revenue_rank <= 10
ORDER BY revenue_rank
LIMIT 10""",
        sql_lakebase="""WITH ttm AS (
  SELECT
    property_id,
    property_name,
    property_type,
    fund_name,
    SUM(revenue) AS revenue_ttm,
    SUM(noi) AS noi_ttm,
    AVG(occupancy_rate) AS avg_occupancy
  FROM serving.gold_property_financials
  WHERE arena_id = '{arena_id}'
    AND month >= (current_date - interval '12 months')
  GROUP BY property_id, property_name, property_type, fund_name
),
ranked AS (
  SELECT
    ttm.*,
    RANK() OVER (ORDER BY revenue_ttm DESC) AS revenue_rank
  FROM ttm
)
SELECT *
FROM ranked
WHERE revenue_rank <= 10
ORDER BY revenue_rank
LIMIT 10""",
    ),
    BenchmarkQuery(
        name="pnl_rollup_multi_month",
        display_name="Q5 — Multi-month P&L rollup (heaviest)",
        category="Reporting (heavy)",
        weight=2,
        summary="24-month P&L by fund with revenue/expense pivot. The worst-case query in the mix.",
        sql_dbsql="""SELECT
  f.fund_id,
  f.fund_name,
  f.strategy,
  f.vintage_year,
  gl.month,
  SUM(CASE WHEN gl.category = 'revenue' THEN gl.total_amount ELSE 0 END) AS revenue,
  SUM(CASE WHEN gl.category = 'expense' THEN gl.total_amount ELSE 0 END) AS expenses,
  SUM(CASE WHEN gl.category = 'revenue' THEN gl.total_amount ELSE 0 END)
    - SUM(CASE WHEN gl.category = 'expense' THEN gl.total_amount ELSE 0 END) AS net_income,
  SUM(gl.transaction_count) AS transaction_count,
  ROUND(AVG(gl.avg_transaction_amount), 2) AS avg_txn_amount
FROM juniper_square_demo_catalog.pipeline.gold_gl_monthly_summary gl
JOIN juniper_square_demo_catalog.pipeline.silver_funds f
  ON f.fund_id = gl.fund_id AND f.arena_id = gl.arena_id
WHERE gl.arena_id = '{arena_id}'
  AND gl.month >= add_months(current_date(), -24)
GROUP BY f.fund_id, f.fund_name, f.strategy, f.vintage_year, gl.month
ORDER BY f.fund_name, gl.month""",
        sql_lakebase="""SELECT
  f.fund_id,
  f.fund_name,
  f.strategy,
  f.vintage_year,
  gl.month,
  SUM(CASE WHEN gl.category = 'revenue' THEN gl.total_amount ELSE 0 END) AS revenue,
  SUM(CASE WHEN gl.category = 'expense' THEN gl.total_amount ELSE 0 END) AS expenses,
  SUM(CASE WHEN gl.category = 'revenue' THEN gl.total_amount ELSE 0 END)
    - SUM(CASE WHEN gl.category = 'expense' THEN gl.total_amount ELSE 0 END) AS net_income,
  SUM(gl.transaction_count) AS transaction_count,
  ROUND(AVG(gl.avg_transaction_amount)::numeric, 2) AS avg_txn_amount
FROM serving.gold_gl_monthly_summary gl
JOIN serving.silver_funds f
  ON f.fund_id = gl.fund_id AND f.arena_id = gl.arena_id
WHERE gl.arena_id = '{arena_id}'
  AND gl.month >= (current_date - interval '24 months')
GROUP BY f.fund_id, f.fund_name, f.strategy, f.vintage_year, gl.month
ORDER BY f.fund_name, gl.month""",
    ),
    BenchmarkQuery(
        name="investor_commitment_summary",
        display_name="Q6 — Investor commitment summary",
        category="Ad-hoc",
        weight=3,
        summary="Per-investor rollup across funds: total commitment + distinct fund count.",
        sql_dbsql="""SELECT
  i.investor_id,
  i.investor_name,
  i.type,
  i.city,
  i.state,
  SUM(i.commitment_amount) AS total_commitment,
  COUNT(DISTINCT i.fund_id) AS fund_count,
  array_agg(DISTINCT f.fund_name) AS funds
FROM juniper_square_demo_catalog.pipeline.silver_investors i
JOIN juniper_square_demo_catalog.pipeline.silver_funds f
  ON f.fund_id = i.fund_id AND f.arena_id = i.arena_id
WHERE i.arena_id = '{arena_id}'
GROUP BY i.investor_id, i.investor_name, i.type, i.city, i.state
ORDER BY total_commitment DESC""",
        sql_lakebase="""SELECT
  i.investor_id,
  i.investor_name,
  i.type,
  i.city,
  i.state,
  SUM(i.commitment_amount) AS total_commitment,
  COUNT(DISTINCT i.fund_id) AS fund_count,
  array_agg(DISTINCT f.fund_name) AS funds
FROM serving.silver_investors i
JOIN serving.silver_funds f
  ON f.fund_id = i.fund_id AND f.arena_id = i.arena_id
WHERE i.arena_id = '{arena_id}'
GROUP BY i.investor_id, i.investor_name, i.type, i.city, i.state
ORDER BY total_commitment DESC""",
    ),
]


def by_name(name: str) -> BenchmarkQuery | None:
    for q in BENCHMARK_QUERIES:
        if q.name == name:
            return q
    return None
