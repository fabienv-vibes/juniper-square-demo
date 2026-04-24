-- Q2: gl_monthly_by_arena_year
-- Category: adhoc
-- Monthly GL spend by category for one arena, last 12 months.

-- ---- DBSQL (Spark SQL) ----
SELECT
  category,
  month,
  SUM(total_amount) AS total_amount,
  SUM(transaction_count) AS transaction_count,
  ROUND(AVG(avg_transaction_amount), 2) AS avg_transaction_amount
FROM juniper_square_demo_catalog.pipeline.gold_gl_monthly_summary
WHERE arena_id = '{arena_id}'
  AND month >= add_months(current_date(), -12)
GROUP BY category, month
ORDER BY month, category;

-- ---- Lakebase (Postgres) ----
SELECT
  category,
  month,
  SUM(total_amount) AS total_amount,
  SUM(transaction_count) AS transaction_count,
  ROUND(AVG(avg_transaction_amount)::numeric, 2) AS avg_transaction_amount
FROM serving.gold_gl_monthly_summary
WHERE arena_id = '{arena_id}'
  AND month >= (current_date - interval '12 months')
GROUP BY category, month
ORDER BY month, category;
