-- Q3: property_financials_joined
-- Category: adhoc
-- Join gold_property_financials + silver_arenas + silver_funds, 1 arena, trailing 6 months, LIMIT 100.

-- ---- DBSQL (Spark SQL) ----
SELECT
  pf.property_id,
  pf.property_name,
  pf.property_type,
  a.arena_name,
  a.tier,
  f.fund_name,
  f.strategy,
  pf.month,
  pf.revenue,
  pf.expenses,
  pf.noi,
  pf.occupancy_rate
FROM juniper_square_demo_catalog.pipeline.gold_property_financials pf
JOIN juniper_square_demo_catalog.pipeline.silver_arenas a
  ON a.arena_id = pf.arena_id
JOIN juniper_square_demo_catalog.pipeline.silver_funds f
  ON f.fund_id = pf.fund_id AND f.arena_id = pf.arena_id
WHERE pf.arena_id = '{arena_id}'
  AND pf.month >= add_months(current_date(), -6)
ORDER BY pf.month DESC, pf.noi DESC
LIMIT 100;

-- ---- Lakebase (Postgres) ----
SELECT
  pf.property_id,
  pf.property_name,
  pf.property_type,
  a.arena_name,
  a.tier,
  f.fund_name,
  f.strategy,
  pf.month,
  pf.revenue,
  pf.expenses,
  pf.noi,
  pf.occupancy_rate
FROM serving.gold_property_financials pf
JOIN serving.silver_arenas a
  ON a.arena_id = pf.arena_id
JOIN serving.silver_funds f
  ON f.fund_id = pf.fund_id AND f.arena_id = pf.arena_id
WHERE pf.arena_id = '{arena_id}'
  AND pf.month >= (current_date - interval '6 months')
ORDER BY pf.month DESC, pf.noi DESC
LIMIT 100;
