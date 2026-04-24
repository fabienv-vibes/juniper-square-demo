-- Q4: top10_properties_by_revenue
-- Category: reporting
-- Top 10 properties by trailing-12-month revenue for one arena; ranked window function; LIMIT 10.

-- ---- DBSQL (Spark SQL) ----
WITH ttm AS (
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
LIMIT 10;

-- ---- Lakebase (Postgres) ----
WITH ttm AS (
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
LIMIT 10;
