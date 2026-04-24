-- Q6: investor_commitment_summary
-- Category: adhoc
-- Per-investor commitment totals in 1 arena: join silver_investors + silver_funds + array_agg of fund names.

-- ---- DBSQL (Spark SQL) ----
SELECT
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
ORDER BY total_commitment DESC;

-- ---- Lakebase (Postgres) ----
SELECT
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
ORDER BY total_commitment DESC;
