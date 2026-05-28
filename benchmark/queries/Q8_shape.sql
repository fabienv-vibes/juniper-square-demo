-- =============================================================================
-- Q8b-shape: Fund Performance Roll-up (silver-scan monster)
-- =============================================================================
-- Replicates Juniper's described shape: 500 lines, 50-table touch count.
-- Real-world: Fund-level IRR/MOIC/TVPI/DPI over time, by property, with
-- peer-vintage benchmarking. Quarterly board pack. Used by Insights AI for
-- "show me how Fund VII is performing vs vintage peers."
--
-- Parameters:
--   :fund_id      '{fund_id}' (or NULL = all funds in arena)
--   :as_of_date   '2024-12-31'
-- =============================================================================

-- ---- DBSQL (Spark SQL) ----

WITH
fund_metadata AS (
  SELECT
    f.fund_id,
    f.arena_id,
    f.fund_name,
    f.fund_type_code,
    ft.fund_type_name,
    ft.is_closed_end,
    f.vintage_year,
    vy.market_cycle_phase,
    vy.peer_cohort_size,
    f.holding_strategy_code,
    hs.strategy_name AS holding_strategy_name,
    hs.risk_profile,
    f.invest_strategy_code,
    invst.invest_strategy_name,
    invst.typical_hold_years,
    f.carry_code,
    cs.pref_return_pct,
    cs.gp_carry_pct,
    cs.catchup_pct,
    cs.waterfall_type,
    f.mgmt_fee_code,
    mfs.rate_pct AS mgmt_fee_rate,
    mfs.rate_basis AS mgmt_fee_basis,
    f.base_currency,
    cur.currency_name,
    f.committed_capital_usd,
    f.inception_date,
    a.arena_name,
    a.region AS arena_region
  FROM juniper_square_demo_catalog.pipeline.dim_fund f
  JOIN juniper_square_demo_catalog.pipeline.dim_fund_type ft ON f.fund_type_code = ft.fund_type_code
  JOIN juniper_square_demo_catalog.pipeline.dim_vintage_year vy ON f.vintage_year = vy.vintage_year
  JOIN juniper_square_demo_catalog.pipeline.dim_arena a ON f.arena_id = a.arena_id
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_holding_strategy hs ON f.holding_strategy_code = hs.strategy_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_investment_strategy invst ON f.invest_strategy_code = invst.invest_strategy_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_carry_structure cs ON f.carry_code = cs.carry_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_mgmt_fee_schedule mfs ON f.mgmt_fee_code = mfs.schedule_code
  JOIN juniper_square_demo_catalog.pipeline.dim_currency cur ON f.base_currency = cur.currency_code
  WHERE f.fund_id = '{fund_id}'
    AND f.is_current = TRUE
),

property_universe AS (
  SELECT
    p.property_id,
    p.arena_id,
    p.property_name,
    p.property_type_code,
    pt.property_type_name,
    p.property_subtype_code,
    pst.property_subtype_name,
    p.property_class_code,
    pcl.class_name AS property_class_name,
    pcl.quality_rank,
    p.country_code,
    geo_c.country_name AS property_country_name,
    geo_c.region AS property_region,
    p.state_code,
    geo_s.state_name,
    p.msa_code,
    msa.msa_name,
    msa.population AS msa_population,
    p.acquisition_date,
    p.acquisition_price_usd,
    p.square_feet,
    p.units
  FROM juniper_square_demo_catalog.pipeline.dim_property p
  JOIN juniper_square_demo_catalog.pipeline.dim_property_type pt ON p.property_type_code = pt.property_type_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_property_subtype pst ON p.property_subtype_code = pst.property_subtype_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_property_class pcl ON p.property_class_code = pcl.class_code
  JOIN juniper_square_demo_catalog.pipeline.dim_geography_country geo_c ON p.country_code = geo_c.country_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_geography_state geo_s ON p.state_code = geo_s.state_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_geography_msa msa ON p.msa_code = msa.msa_code
),

cash_flow_history AS (
  SELECT fund_id,
    CONCAT('FY', YEAR(call_date), '_Q', QUARTER(call_date)) AS period_id,
    'CONTRIBUTION' AS flow_type,
    SUM(call_amount_usd) AS flow_usd
    FROM juniper_square_demo_catalog.pipeline.fact_capital_call
    WHERE fund_id = '{fund_id}' AND call_date <= DATE('2024-12-31')
    GROUP BY fund_id, CONCAT('FY', YEAR(call_date), '_Q', QUARTER(call_date))
  UNION ALL
  SELECT fund_id,
    CONCAT('FY', YEAR(distribution_date), '_Q', QUARTER(distribution_date)) AS period_id,
    CONCAT('DISTRIBUTION_', waterfall_tier_id) AS flow_type,
    SUM(amount_usd) AS flow_usd
    FROM juniper_square_demo_catalog.pipeline.fact_distribution
    WHERE fund_id = '{fund_id}' AND distribution_date <= DATE('2024-12-31')
    GROUP BY fund_id, period_id, waterfall_tier_id
  UNION ALL
  SELECT fund_id, period_id, 'FEE' AS flow_type, SUM(amount_usd) AS flow_usd
    FROM juniper_square_demo_catalog.pipeline.fact_fee_charge
    WHERE fund_id = '{fund_id}' AND charge_date <= DATE('2024-12-31')
    GROUP BY fund_id, period_id
),

valuation_history AS (
  SELECT
    v.fund_id,
    v.property_id,
    v.period_id,
    AVG(v.value_usd) AS period_value_usd,
    MAX(v.valuation_date) AS latest_val_date,
    v.valuation_method,
    LAG(AVG(v.value_usd)) OVER (PARTITION BY v.property_id ORDER BY v.period_id) AS prior_value_usd
  FROM juniper_square_demo_catalog.pipeline.fact_valuation v
  WHERE v.fund_id = '{fund_id}' AND v.valuation_date <= DATE('2024-12-31')
  GROUP BY v.fund_id, v.property_id, v.period_id, v.valuation_method
),

nav_time_series AS (
  SELECT
    fund_id,
    period_id,
    AVG(gross_nav_usd) AS gross_nav,
    AVG(net_nav_usd) AS net_nav,
    AVG(nav_per_unit) AS nav_per_unit,
    SUM(total_units) AS total_units,
    LAG(AVG(net_nav_usd)) OVER (PARTITION BY fund_id ORDER BY period_id) AS prior_net_nav
  FROM juniper_square_demo_catalog.pipeline.fact_nav_snapshot
  WHERE fund_id = '{fund_id}' AND snapshot_date <= DATE('2024-12-31')
  GROUP BY fund_id, period_id
),

fx_normalized AS (
  SELECT
    fx.base_currency,
    fx.quote_currency,
    rcp.is_active,
    AVG(fx.rate) AS avg_rate,
    STDDEV(fx.rate) AS rate_stddev
  FROM juniper_square_demo_catalog.pipeline.fact_fx_rate fx
  JOIN juniper_square_demo_catalog.pipeline.ref_currency_pair rcp ON fx.pair_code = rcp.pair_code
  WHERE fx.rate_date <= DATE('2024-12-31')
  GROUP BY fx.base_currency, fx.quote_currency, rcp.is_active
),

cumulative_cash_flows AS (
  SELECT
    fund_id,
    period_id,
    SUM(CASE WHEN flow_type = 'CONTRIBUTION' THEN flow_usd ELSE 0 END) AS contrib_usd,
    SUM(CASE WHEN flow_type LIKE 'DISTRIBUTION%' THEN flow_usd ELSE 0 END) AS dist_usd,
    SUM(CASE WHEN flow_type = 'FEE' THEN flow_usd ELSE 0 END) AS fee_usd,
    SUM(SUM(CASE WHEN flow_type LIKE 'DISTRIBUTION%' THEN flow_usd ELSE 0 END)) OVER (
      PARTITION BY fund_id ORDER BY period_id
    ) AS cumulative_dist_usd,
    SUM(SUM(CASE WHEN flow_type = 'CONTRIBUTION' THEN flow_usd ELSE 0 END)) OVER (
      PARTITION BY fund_id ORDER BY period_id
    ) AS cumulative_contrib_usd
  FROM cash_flow_history
  GROUP BY fund_id, period_id
),

fund_metrics AS (
  SELECT
    ccf.fund_id,
    ccf.period_id,
    ccf.cumulative_contrib_usd,
    ccf.cumulative_dist_usd,
    nts.net_nav,
    ccf.cumulative_dist_usd / NULLIF(ccf.cumulative_contrib_usd, 0) AS dpi,
    (ccf.cumulative_dist_usd + nts.net_nav) / NULLIF(ccf.cumulative_contrib_usd, 0) AS tvpi,
    nts.net_nav / NULLIF(ccf.cumulative_contrib_usd, 0) AS rvpi,
    -- crude IRR proxy via linear approximation
    POWER((ccf.cumulative_dist_usd + nts.net_nav) / NULLIF(ccf.cumulative_contrib_usd, 1), 0.25) - 1 AS irr_proxy
  FROM cumulative_cash_flows ccf
  LEFT JOIN nav_time_series nts ON ccf.fund_id = nts.fund_id AND ccf.period_id = nts.period_id
),

property_attribution AS (
  SELECT
    a.fund_id,
    a.property_id,
    a.period_id,
    SUM(a.allocated_amount_usd) AS property_pl_usd,
    SUM(CASE WHEN a.allocation_type = 'REALIZED_GAIN' THEN a.allocated_amount_usd ELSE 0 END) AS realized_pl,
    SUM(CASE WHEN a.allocation_type = 'UNREALIZED_GAIN' THEN a.allocated_amount_usd ELSE 0 END) AS unrealized_pl,
    SUM(CASE WHEN a.allocation_type = 'INCOME' THEN a.allocated_amount_usd ELSE 0 END) AS income_pl,
    SUM(CASE WHEN a.allocation_type = 'EXPENSE' THEN a.allocated_amount_usd ELSE 0 END) AS expense_pl
  FROM juniper_square_demo_catalog.pipeline.fact_allocation a
  JOIN juniper_square_demo_catalog.pipeline.dim_account_chart ac ON a.account_id = ac.account_id
  JOIN juniper_square_demo_catalog.pipeline.ref_account_type rat ON ac.account_type_code = rat.account_type_code
  WHERE a.fund_id = '{fund_id}'
    AND a.computed_at <= TIMESTAMP('2024-12-31 23:59:59')
  GROUP BY a.fund_id, a.property_id, a.period_id
),

peer_quartile AS (
  SELECT
    vy.vintage_year,
    COUNT(DISTINCT f.fund_id) AS cohort_size,
    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY f.committed_capital_usd) AS p25_committed,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY f.committed_capital_usd) AS p50_committed,
    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY f.committed_capital_usd) AS p75_committed
  FROM juniper_square_demo_catalog.pipeline.dim_fund f
  JOIN juniper_square_demo_catalog.pipeline.dim_vintage_year vy ON f.vintage_year = vy.vintage_year
  WHERE f.is_current = TRUE
    AND f.vintage_year IN (SELECT vintage_year FROM fund_metadata)
  GROUP BY vy.vintage_year
),

-- ============== HEAVY SILVER SCAN: GL attribution ====================
gl_attribution AS (
  SELECT
    gl.fund_id,
    gl.account_id,
    ac.account_name,
    rat.account_type_name,
    rat.normal_balance,
    rtt.transaction_type_name,
    rtt.category,
    cur.currency_name AS txn_currency,
    rs.status_name AS txn_status,
    rac.approval_tier_name,
    SUM(gl.amount_usd) AS total_gl_usd,
    SUM(CASE WHEN rat.account_type_code = 'REVENUE' THEN gl.amount_usd ELSE 0 END) AS revenue_usd,
    SUM(CASE WHEN rat.account_type_code = 'EXPENSE' THEN gl.amount_usd ELSE 0 END) AS expense_usd,
    SUM(CASE WHEN rat.account_type_code IN ('REVENUE','EXPENSE') THEN gl.amount_usd ELSE 0 END) AS noi_usd,
    COUNT(*) AS txn_count
  FROM juniper_square_demo_catalog.pipeline.fact_gl_transaction gl
  JOIN juniper_square_demo_catalog.pipeline.dim_account_chart ac ON gl.account_id = ac.account_id
  JOIN juniper_square_demo_catalog.pipeline.ref_account_type rat ON ac.account_type_code = rat.account_type_code
  JOIN juniper_square_demo_catalog.pipeline.ref_transaction_type rtt ON gl.transaction_type_code = rtt.transaction_type_code
  JOIN juniper_square_demo_catalog.pipeline.dim_currency cur ON gl.currency_code = cur.currency_code
  JOIN juniper_square_demo_catalog.pipeline.ref_status_code rs ON gl.status_code = rs.status_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.ref_approval_chain rac ON gl.approval_chain_code = rac.approval_tier_code
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_counterparty cp ON gl.counterparty_id = cp.counterparty_id
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_cost_center cc ON gl.cost_center_code = cc.cost_center_code
  WHERE gl.fund_id = '{fund_id}'
    AND gl.transaction_date <= DATE('2024-12-31')
  GROUP BY gl.fund_id, gl.account_id, ac.account_name, rat.account_type_name,
           rat.normal_balance, rtt.transaction_type_name, rtt.category,
           cur.currency_name, rs.status_name, rac.approval_tier_name
),

-- ============== AUDIT TRAIL ====================
audit_summary AS (
  SELECT
    ae.entity_type,
    ae.event_type,
    er.role_name,
    COUNT(*) AS event_count,
    MIN(ae.event_timestamp) AS first_event,
    MAX(ae.event_timestamp) AS last_event
  FROM juniper_square_demo_catalog.pipeline.fact_audit_event ae
  LEFT JOIN juniper_square_demo_catalog.pipeline.dim_employee_role er ON ae.role_code = er.role_code
  WHERE ae.event_timestamp <= TIMESTAMP('2024-12-31 23:59:59')
    AND ae.entity_type IN ('fact_gl_transaction','fact_allocation','fact_valuation','fact_distribution','fact_capital_call','fact_fee_charge')
  GROUP BY ae.entity_type, ae.event_type, er.role_name
),

-- ============== DOCUMENT METADATA ====================
doc_summary AS (
  SELECT
    dm.fund_id,
    dt.doc_type_name,
    COUNT(*) AS doc_count,
    SUM(dm.file_size_bytes) / 1024.0 / 1024.0 AS total_mb,
    SUM(dm.page_count) AS total_pages
  FROM juniper_square_demo_catalog.pipeline.fact_document_metadata dm
  JOIN juniper_square_demo_catalog.pipeline.dim_document_type dt ON dm.doc_type_code = dt.doc_type_code
  WHERE dm.fund_id = '{fund_id}'
  GROUP BY dm.fund_id, dt.doc_type_name
)

SELECT
  fm.fund_id,
  fm.fund_name,
  fm.fund_type_name,
  fm.is_closed_end,
  fm.vintage_year,
  fm.market_cycle_phase,
  fm.peer_cohort_size,
  fm.holding_strategy_name,
  fm.risk_profile,
  fm.invest_strategy_name,
  fm.typical_hold_years,
  fm.pref_return_pct,
  fm.gp_carry_pct,
  fm.waterfall_type,
  fm.mgmt_fee_rate,
  fm.mgmt_fee_basis,
  fm.committed_capital_usd,
  fm.inception_date,
  fm.arena_name,
  fm.arena_region,

  pu.property_id,
  pu.property_name,
  pu.property_type_name,
  pu.property_subtype_name,
  pu.property_class_name,
  pu.quality_rank,
  pu.property_country_name,
  pu.state_name,
  pu.msa_name,
  pu.msa_population,
  pu.acquisition_date,
  pu.acquisition_price_usd,
  pu.square_feet,
  pu.units,

  ccf.period_id,
  ccf.contrib_usd,
  ccf.dist_usd,
  ccf.fee_usd,
  ccf.cumulative_contrib_usd,
  ccf.cumulative_dist_usd,

  vh.period_value_usd,
  vh.prior_value_usd,
  vh.period_value_usd - vh.prior_value_usd AS value_change_usd,
  vh.valuation_method,

  nts.gross_nav,
  nts.net_nav,
  nts.nav_per_unit,
  nts.prior_net_nav,
  nts.total_units,

  fxn.avg_rate AS fx_avg_rate,
  fxn.rate_stddev AS fx_volatility,

  fmet.dpi,
  fmet.tvpi,
  fmet.rvpi,
  fmet.irr_proxy AS fund_irr,

  pa.property_pl_usd,
  pa.realized_pl,
  pa.unrealized_pl,
  pa.income_pl,
  pa.expense_pl,

  pq.cohort_size AS peer_cohort_size_actual,
  pq.p25_committed AS peer_p25_committed,
  pq.p50_committed AS peer_p50_committed,
  pq.p75_committed AS peer_p75_committed,
  CASE
    WHEN fm.committed_capital_usd <= pq.p25_committed THEN 'Q1'
    WHEN fm.committed_capital_usd <= pq.p50_committed THEN 'Q2'
    WHEN fm.committed_capital_usd <= pq.p75_committed THEN 'Q3'
    ELSE 'Q4'
  END AS peer_quartile,

  ga.account_name,
  ga.account_type_name,
  ga.transaction_type_name,
  ga.category AS gl_category,
  ga.txn_currency,
  ga.txn_status,
  ga.approval_tier_name AS gl_approval_tier,
  ga.total_gl_usd,
  ga.revenue_usd AS gl_revenue,
  ga.expense_usd AS gl_expense,
  ga.noi_usd AS gl_noi,
  ga.txn_count AS gl_txn_count,

  audit.entity_type AS audit_entity_type,
  audit.event_type AS audit_event_type,
  audit.role_name AS audit_user_role,
  audit.event_count AS audit_event_count,

  ds.doc_type_name,
  ds.doc_count,
  ds.total_mb AS doc_total_mb,
  ds.total_pages AS doc_total_pages

FROM fund_metadata fm
LEFT JOIN property_universe pu ON pu.arena_id = fm.arena_id
LEFT JOIN cumulative_cash_flows ccf ON ccf.fund_id = fm.fund_id
LEFT JOIN valuation_history vh ON vh.fund_id = fm.fund_id AND vh.property_id = pu.property_id
LEFT JOIN nav_time_series nts ON nts.fund_id = fm.fund_id AND nts.period_id = ccf.period_id
LEFT JOIN fx_normalized fxn ON fxn.base_currency = fm.base_currency
LEFT JOIN fund_metrics fmet ON fmet.fund_id = fm.fund_id AND fmet.period_id = ccf.period_id
LEFT JOIN property_attribution pa ON pa.fund_id = fm.fund_id AND pa.property_id = pu.property_id AND pa.period_id = ccf.period_id
LEFT JOIN peer_quartile pq ON pq.vintage_year = fm.vintage_year
LEFT JOIN gl_attribution ga ON ga.fund_id = fm.fund_id
LEFT JOIN (
  SELECT entity_type, event_type, role_name, event_count, first_event, last_event,
    ROW_NUMBER() OVER (ORDER BY event_count DESC) AS rn
  FROM audit_summary
) audit ON audit.rn = 1
LEFT JOIN doc_summary ds ON ds.fund_id = fm.fund_id
ORDER BY fm.fund_id, pu.property_id, ccf.period_id
LIMIT 100000;
