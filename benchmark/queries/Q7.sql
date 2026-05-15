-- Q7: worst_case_yoy_growth
-- Category: worst-case / redline
-- DBSQL ONLY. Deliberately defeats Liquid Clustering pruning, gold pre-aggregation,
-- and arena-id filtering. Full-silver scan on the wider GL schema (memo_text,
-- currency, approval_status, counterparty_id, etc), 3-join, 2 CTE window functions,
-- cross-arena top-100 properties by 3-year YoY revenue growth.
--
-- NOT run against Lakebase: silver_gl_transactions is not synced (only gold is).
-- See config.yaml `targets: [dbsql]` for this query.

-- ---- DBSQL (Spark SQL) ----
WITH annual_revenue AS (
  SELECT
    gl.property_id,
    gl.fiscal_year,
    SUM(CASE WHEN gl.category = 'revenue' THEN gl.amount ELSE 0 END) AS rev,
    COUNT(DISTINCT gl.counterparty_id) AS counterparty_count
  FROM juniper_square_demo_catalog.pipeline.silver_gl_transactions gl
  JOIN juniper_square_demo_catalog.pipeline.silver_properties p
    ON p.property_id = gl.property_id
  JOIN juniper_square_demo_catalog.pipeline.silver_funds f
    ON f.fund_id = gl.fund_id
  WHERE gl.fiscal_year BETWEEN year(current_date()) - 3 AND year(current_date())
    AND gl.approval_status = 'approved'
    AND gl.currency = 'USD'
  GROUP BY gl.property_id, gl.fiscal_year
),
growth AS (
  SELECT
    property_id,
    fiscal_year,
    rev,
    counterparty_count,
    LAG(rev) OVER (PARTITION BY property_id ORDER BY fiscal_year) AS prev_rev,
    (rev - LAG(rev) OVER (PARTITION BY property_id ORDER BY fiscal_year))
      / NULLIF(LAG(rev) OVER (PARTITION BY property_id ORDER BY fiscal_year), 0) AS yoy_growth
  FROM annual_revenue
),
ranked AS (
  SELECT
    property_id,
    fiscal_year,
    rev,
    prev_rev,
    yoy_growth,
    counterparty_count,
    RANK() OVER (PARTITION BY fiscal_year ORDER BY yoy_growth DESC) AS rnk
  FROM growth
  WHERE yoy_growth IS NOT NULL
)
SELECT
  property_id,
  fiscal_year,
  rev,
  prev_rev,
  yoy_growth,
  counterparty_count,
  rnk
FROM ranked
WHERE rnk <= 100
ORDER BY fiscal_year DESC, rnk
LIMIT 300;
