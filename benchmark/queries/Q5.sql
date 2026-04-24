-- Q5: pnl_rollup_multi_month
-- Category: reporting (heavy)
-- Full P&L rollup per fund per month for an arena; joins gold_gl_monthly_summary + silver_funds; 24 months.

-- ---- DBSQL (Spark SQL) ----
SELECT
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
ORDER BY f.fund_name, gl.month;

-- ---- Lakebase (Postgres) ----
SELECT
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
ORDER BY f.fund_name, gl.month;
