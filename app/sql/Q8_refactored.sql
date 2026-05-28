-- =============================================================================
-- Q8b-refactored: Fund performance via medallion (sub-second on gold)
-- =============================================================================
-- Same business answer as Q8b-shape, 25 lines, reads from pre-aggregated
-- gold_fund_attribution_period. All cash flows, valuations, NAV, IRR, MOIC,
-- TVPI, DPI, property attribution, peer benchmarks already precomputed.
-- =============================================================================

-- ---- DBSQL (Spark SQL) ----

SELECT
  f.fund_name,
  f.vintage_year,
  ft.fund_type_name,
  rhs.strategy_name AS holding_strategy_name,
  pu.property_name,
  pt.property_type_name,
  pu.msa_code,
  fap.period_label,
  fap.fund_irr_net,
  fap.fund_irr_trailing_12m,
  fap.moic_net,
  fap.tvpi_net,
  fap.dpi_net,
  fap.rvpi_net,
  fap.property_contribution_pct,
  fap.property_attribution_usd,
  fap.peer_quartile_irr,
  fap.peer_quartile_moic,
  fap.vintage_cohort_median_irr,
  fap.gross_appreciation_usd,
  fap.fx_drag_usd,
  fap.gross_nav,
  fap.net_nav
FROM juniper_square_demo_catalog.pipeline.gold_fund_attribution_period fap
JOIN juniper_square_demo_catalog.pipeline.dim_fund f ON fap.fund_id = f.fund_id AND f.is_current = TRUE
JOIN juniper_square_demo_catalog.pipeline.dim_fund_type ft ON f.fund_type_code = ft.fund_type_code
LEFT JOIN juniper_square_demo_catalog.pipeline.ref_holding_strategy rhs ON f.holding_strategy_code = rhs.strategy_code
JOIN juniper_square_demo_catalog.pipeline.dim_property pu ON fap.property_id = pu.property_id
JOIN juniper_square_demo_catalog.pipeline.dim_property_type pt ON pu.property_type_code = pt.property_type_code
WHERE fap.fund_id = 'FND-000042'
  AND fap.period_end <= DATE('2024-12-31')
ORDER BY fap.period_id, pu.property_id;
