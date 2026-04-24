-- Q1: fund_performance_arena
-- Category: reporting
-- Full fund overview for one arena. Small result, parallel-friendly.

-- ---- DBSQL (Spark SQL) ----
SELECT *
FROM juniper_square_demo_catalog.pipeline.gold_fund_performance
WHERE arena_id = '{arena_id}'
ORDER BY aum DESC;

-- ---- Lakebase (Postgres) ----
SELECT *
FROM serving.gold_fund_performance
WHERE arena_id = '{arena_id}'
ORDER BY aum DESC;
