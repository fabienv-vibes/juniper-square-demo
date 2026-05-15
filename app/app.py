"""Juniper Square benchmark app -- Streamlit on Databricks Apps.

Frames Databricks benchmark results inside the 7-pillar data-maturity
scorecard. Landing page = radar + pillar cards; each live-data pillar has a
drill-in page with charts backed by juniper_square_demo_catalog.pipeline.*.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from lib import queries
from lib.benchmark_catalog import BENCHMARK_QUERIES
from lib.charts import (
    build_cost_breakeven,
    build_latency_cdf,
    build_latency_timeseries,
    build_latency_vs_concurrency,
    build_qps_timeseries,
    build_query_mix_chart,
    build_throughput_chart,
    build_warmup_ramp,
)
from lib.lineage import get_column_lineage, get_uc_lineage_ui_url
from lib.scorecard import PILLARS, STAGE_COLORS, get_pillar, stage_value
from lib.theme import inject_theme


def _fmt_latency(ms: float) -> str:
    """Render a latency as '520 ms' under 1s, '2.86 s' above. No spurious decimals."""
    if ms is None or ms <= 0:
        return "n/a"
    if ms < 1000:
        return f"{ms:,.0f} ms"
    return f"{ms / 1000:.2f} s"

# ---------------------------------------------------------------------------
# Page config + brand theme (must run first, before any other st.* output)
# ---------------------------------------------------------------------------

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "databricks.svg"

inject_theme(
    page_title="Juniper Square x Databricks",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else "app",
)


# ---------------------------------------------------------------------------
# Sidebar nav
# ---------------------------------------------------------------------------

PAGES = [
    ("Overview", "overview"),
    ("Data latency", "latency"),
    ("Cost of doing business", "cost"),
    ("Keeping the lights on", "klo"),
    ("Data lineage", "lineage"),
    ("Data security", "security"),
    ("Integration layer", "integration"),
    ("Data provenance", "provenance"),
    ("Auditability", "audit"),
]


def _logo_data_uri() -> Optional[str]:
    if not LOGO_PATH.exists():
        return None
    svg = LOGO_PATH.read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")


with st.sidebar:
    logo_uri = _logo_data_uri()
    if logo_uri:
        st.markdown(
            f"<img src='{logo_uri}' alt='Databricks' "
            f"style='width:140px; margin: 8px 0 12px 0;' />",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div style='font-size:0.85rem; color:#4A5568; margin-bottom:18px;'>"
        "Benchmark evaluation"
        "</div>",
        unsafe_allow_html=True,
    )

    # Honor pillar-card "Drill in" buttons via ?page= URL param
    qp = st.query_params
    default_idx = 0
    if "page" in qp:
        target = qp["page"]
        for i, (_, key) in enumerate(PAGES):
            if key == target:
                default_idx = i
                break

    labels = [label for label, _ in PAGES]
    selection = st.radio("Navigate", labels, index=default_idx, label_visibility="collapsed")
    current_page_key = dict(zip(labels, [k for _, k in PAGES]))[selection]


# ---------------------------------------------------------------------------
# Shared components
# ---------------------------------------------------------------------------

def stage_chip(stage: str, small: bool = False) -> str:
    """HTML for a colored stage chip."""
    last_token = None
    for tok in reversed(stage.replace("/", " ").split()):
        if tok in STAGE_COLORS:
            last_token = tok
            break
    color = STAGE_COLORS.get(last_token, "#A0AEC0")
    cls = "stage-chip small" if small else "stage-chip"
    return f"<span class='{cls}' style='background-color:{color};'>{stage}</span>"


def preview_banner(result: queries.QueryResult, context: str = "") -> None:
    if not result.preview_mode:
        return
    msg = (
        f"<div class='preview-banner'><b>Preview mode</b>"
        f" &middot; source not yet populated. Showing representative data."
    )
    if context:
        msg += f" <span class='muted'>({context})</span>"
    msg += "</div>"
    st.markdown(msg, unsafe_allow_html=True)
    if result.error and result.error != "no-connection" and os.environ.get("DEBUG") == "1":
        with st.expander("Debug: source diagnostic", expanded=False):
            st.code(result.error[:500], language="text")


def page_header(title: str, pillar_key: Optional[str] = None) -> None:
    """Consistent page header: Databricks wordmark + title, optional pillar stage chips."""
    logo_uri = _logo_data_uri()
    if logo_uri:
        st.markdown(
            f"<div class='db-header'>"
            f"<img src='{logo_uri}' alt='Databricks' style='width:120px;' />"
            f"<span class='muted'>Juniper Square platform evaluation</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    st.markdown(f"<h1 style='margin-top:4px;'>{title}</h1>", unsafe_allow_html=True)

    if pillar_key:
        p = get_pillar(pillar_key)
        # Neutral subtitle — the proof point stated as an outcome, not a self-score.
        st.markdown(
            f"<p class='muted' style='margin-top:-4px; margin-bottom:16px;'>"
            f"{p.proof_point}</p>",
            unsafe_allow_html=True,
        )


def render_demo_scope(demonstrated: list[str], also_supported: list[tuple[str, str]]) -> None:
    """Two neutral sections for every drill-in: what we showed + what we otherwise support.

    `demonstrated`      — bullet strings describing concrete things this page proves.
    `also_supported`    — (label, doc_url) tuples for adjacent capabilities not in the demo.
    """
    cols = st.columns(2, gap="large")
    with cols[0]:
        st.markdown("<h3>What we demonstrated</h3>", unsafe_allow_html=True)
        st.markdown("\n".join(f"- {b}" for b in demonstrated))
    with cols[1]:
        st.markdown("<h3>Also supported (not shown today)</h3>", unsafe_allow_html=True)
        st.markdown("\n".join(f"- [{name}]({url})" for name, url in also_supported))


def render_pillar_card(pillar, col) -> None:
    with col:
        live_badge = (
            f"<span><span class='live-dot'></span>live drill-in</span>"
            if pillar.has_live_drill_in
            else "<span class='muted'>narrative</span>"
        )
        drill_link = f"?page={pillar.drill_page}"
        html = f"""
        <div class='pillar-card'>
          <h4>{pillar.name}</h4>
          <p class='proof'>{pillar.proof_point}</p>
          <div class='drill'>
            <a href='{drill_link}' target='_self'>Drill in &rarr;</a>
            {live_badge}
          </div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page: Overview
# ---------------------------------------------------------------------------

def page_overview() -> None:
    page_header("Databricks for Juniper Square")
    st.markdown(
        "<p style='font-size:1.1rem; color:#4A5568; margin-top:-12px; margin-bottom:16px;'>"
        "Benchmark demo: what we built, what we measured, and what else we support."
        "</p>",
        unsafe_allow_html=True,
    )

    # TL;DR framing
    st.markdown(
        "<div class='db-callout'>"
        "<strong>TL;DR.</strong> We built the Juniper Square data shape at "
        "<strong>10K arenas / 10B GL transactions / 1.08 TB silver</strong> on Delta with a "
        "wider GL schema (memo_text, currency, approval, counterparty, cost_center). Fed it "
        "through a medallion pipeline on Serverless Spark Declarative Pipelines, served the "
        "gold tier through both DBSQL Serverless (<strong>Medium Pro, autoscale 1→8</strong>) "
        "and Lakebase Autoscale (<strong>1→4 CU</strong>, Postgres 17), and stress-tested at "
        "lockstep concurrency 5–100 plus sustained-rate scenarios at 5 / 10 / 20 QPS Poisson "
        "for 10 min each."
        "</div>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # Redline — front-and-center scaffold for the "where does Databricks
    # break" deliverable (5/13 call). Placeholders until Q8 + redline run
    # land. Real numbers replace these on completion.
    # -----------------------------------------------------------------
    st.markdown("<h2>Redline: where does Databricks break?</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p style='font-size:1.05rem; color:#4A5568; margin-top:-8px; margin-bottom:16px;'>"
        "We're stress-testing the platform to its breaking point so the cliffs are mapped "
        "before migration day, not after. Numbers below populate as soon as the Q8 query and "
        "the synthesized 50-query mix land in the harness."
        "</p>",
        unsafe_allow_html=True,
    )
    redline_cols = st.columns(4, gap="medium")
    redline_cols[0].metric(
        "DBSQL redline", "pending", delta="Q8 + 50-query mix, autoscale 1→16"
    )
    redline_cols[1].metric(
        "Lakebase redline", "pending", delta="Q8 shape-dependent, autoscale 1→8 CU"
    )
    redline_cols[2].metric(
        "Data volume tested", "pending", delta="target ≥1.5 TB silver / ≥10 B rows"
    )
    redline_cols[3].metric(
        "Headroom vs Juniper peak", "pending", delta="5 QPS today, redline TBD"
    )
    st.markdown(
        "<div class='db-callout' style='margin-top:14px;'>"
        "<strong>What we're varying.</strong>"
        "<ul style='margin:8px 0 0 0; padding-left:20px; font-size:13px;'>"
        "<li><strong>Warehouse size + autoscale ceiling.</strong> Medium Pro 1→16 baseline; "
        "Large Pro if Medium saturates before SLO break.</li>"
        "<li><strong>Sustained Poisson arrival rate.</strong> Push past 20 QPS (the 4× peak we "
        "already characterized) until P95 breaks the 5 s SLO.</li>"
        "<li><strong>Data volume.</strong> Silver fact at ≥1.5 TB / ≥10 B rows, June PDF "
        "Reporting (4.5 B docs / ~5× data) modeled as a separate volume rung.</li>"
        "<li><strong>Query shape.</strong> Q8 (Juniper Square's production query, 3 k lines, "
        "50+ table refs) plus a 50-query mix templated off Juniper-representative shapes.</li>"
        "</ul>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h3 style='margin-top:18px; margin-bottom:8px;'>"
        "What we expect to break first: hypotheses to test"
        "</h3>",
        unsafe_allow_html=True,
    )
    hyp_cols = st.columns(3, gap="medium")
    hyp_cols[0].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>DBSQL cluster ceiling</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Already observed at sustained 20 QPS on "
        "Medium Pro 1→8 (P95 15.6 s, IWM at max). Redline pushes warehouse to 1→16 and tests "
        "Large Pro to find the actual ceiling.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    hyp_cols[1].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>Lakebase CU ceiling</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Lakebase held P95 &lt;200 ms through 20 "
        "QPS on 1→4 CU for dashboard-shaped queries. Q8-shape dependent: if Q8 hits "
        "non-pre-aggregated paths, expect the CU ceiling to move closer.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    hyp_cols[2].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>Autoscale-lag windows</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Characterized in lockstep (c=20 P99 spike, "
        "recovers at c=50). Redline retests under sustained Poisson to confirm IWM "
        "pre-provisioning beats the lockstep cold-burst story.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p class='muted' style='margin-top:14px; font-size:13px;'>"
        "<strong>Pending:</strong> Q8 (Juniper Square production query) and the harness "
        "rerun. Generator can scale to ≥1.5 TB silver in ~15 min on serverless; harness has "
        "<code>query_filter</code> ready for the real production query. Numbers populate here "
        "on completion."
        "</p>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    # Results summary stats
    st.markdown("<h2>Results summary</h2>", unsafe_allow_html=True)
    stat_cols = st.columns(4, gap="medium")
    stat_cols[0].metric("GL transactions", "10 B", delta="1.08 TB silver fact table")
    stat_cols[1].metric(
        "Lakebase P95 @ c=100",
        "≤ 1.1 s",
        delta="dashboard queries hold sub-second under heavy load",
    )
    stat_cols[2].metric(
        "DBSQL Medium Pro @ c=100",
        "P95 ~3-9 s",
        delta="autoscale 1→8, Pro IWM provisions ahead of demand",
    )
    stat_cols[3].metric(
        "Worst-case query (sustained)",
        "P95 859 ms",
        delta="full-silver scan, steady-state @ 1 QPS",
    )

    st.markdown(
        "<p class='muted' style='margin-top:16px;'>"
        "Dashboard SLOs of P50 ≤ 4 s / P95 ≤ 5 s / P99 ≤ 7 s: Lakebase clears them with "
        "~10× headroom across the lockstep matrix. DBSQL Medium Pro clears them on the "
        "dashboard query mix at concurrency ≤ 50. The lockstep c=20 spike on the Data Latency "
        "tab is autoscale provisioning lag, and performance recovers at c=50 once additional "
        "clusters land. <strong>The worst-case query under steady-state load (sustained "
        "1 QPS, single-query-only) lands at P95 859 ms, 6× under the 5 s SLO.</strong> "
        "The lockstep cold burst at c=20 hits P99 ~54 s before autoscale catches up; "
        "that's autoscale-bootstrapping, not the worst-case query's real cost. See the "
        "Data Latency tab for both views."
        "</p>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # Sustained-rate scenarios — the leadership-ready headline numbers
    # -----------------------------------------------------------------
    sustained_runs_result = queries.get_sustained_runs()
    sustained_runs_df = sustained_runs_result.df
    if not sustained_runs_df.empty:
        st.markdown("<h3 style='margin-top:18px;'>Sustained-rate headlines</h3>",
                    unsafe_allow_html=True)
        st.markdown(
            "<p class='muted'>Poisson arrivals at target QPS, fixed wall-clock duration. "
            "Coordinated-omission fixed. Headline P95 is the median across queries "
            "(post-warmup samples only). Drill into the Data Latency tab for time-series + CDF.</p>",
            unsafe_allow_html=True,
        )
        # Prefer the most-recent run per (rate, target). Lakebase target rows preferred for the headline.
        latest_per_rate = (
            sustained_runs_df
            .sort_values("started_at", ascending=False)
            .drop_duplicates(subset=["target_rate_qps", "target"], keep="first")
        )
        rates_in_order = [5.0, 10.0, 20.0, 1.0]
        sus_cols = st.columns(4, gap="medium")
        for col, rate in zip(sus_cols, rates_in_order):
            sub = latest_per_rate[latest_per_rate["target_rate_qps"] == rate]
            if sub.empty:
                col.metric(
                    {5.0: "Peak (5 QPS)", 10.0: "2× headroom (10 QPS)",
                     20.0: "4× scale (20 QPS)", 1.0: "Worst-case query (1 QPS)"}[rate],
                    "no run yet",
                    delta="run sustained scenario to populate",
                )
                continue
            # Prefer Lakebase row if available (better headline number for dashboards)
            lake = sub[sub["target"] == "lakebase"]
            row = lake.iloc[0] if not lake.empty else sub.iloc[0]
            label_for_rate = {
                5.0: "Peak (5 QPS, 10 min)",
                10.0: "2× headroom (10 QPS)",
                20.0: "4× scale (20 QPS)",
                1.0: "Worst-case query (steady-state)",
            }[rate]
            col.metric(
                label_for_rate,
                _fmt_latency(row.get("p95_median_ms")),
                delta=f"{row['target']} · {int(row.get('total_samples') or 0):,} samples",
            )

    # -----------------------------------------------------------------
    # Demo assets — live workspace surfaces to jump into during a walkthrough
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Demo assets</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Every surface below is live in this workspace. A typical walkthrough: "
        "open this page, click into the Databricks UI for each asset, then come back here and "
        "drill into the pillar tabs for the benchmark measurements.</p>",
        unsafe_allow_html=True,
    )

    workspace_host_ov = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    warehouse_id_ov = os.environ.get("DATABRICKS_WAREHOUSE_ID", "aae8e7baf626bd0d")
    catalog_ov = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
    workspace_id_ov = "7474657973275984"
    pipeline_id_ov = "390e607c-83e4-4df8-8468-4655bb8c341a"
    lakebase_project_ov = "juniper-sq-benchmark"

    if workspace_host_ov:
        base_ov = f"https://{workspace_host_ov}"
        assets = [
            ("Spark Declarative Pipeline: the medallion DAG",
             f"{base_ov}/pipelines/{pipeline_id_ov}",
             "Bronze, silver (liquid-clustered), gold. Event log, run history, lineage inline."),
            ("Unity Catalog: browse the demo catalog",
             f"{base_ov}/explore/data/{catalog_ov}",
             "Tables, tags, column comments, permissions, lineage tabs."),
            ("Lineage on gold_gl_monthly_summary",
             f"{base_ov}/explore/data/{catalog_ov}/pipeline/gold_gl_monthly_summary?activeTab=lineage",
             "End-to-end lineage from raw Parquet landing to Lakebase serving."),
            ("Lakebase project: Postgres endpoint",
             f"{base_ov}/lakebase/projects/743d650c-b6e7-488c-a783-219d299f71a5",
             "Juniper Square Benchmark endpoint. Branching, autoscale settings, roles."),
            ("DBSQL warehouse: Serverless Medium Pro, autoscale 1→8",
             f"{base_ov}/sql/warehouses/{warehouse_id_ov}",
             "Start/stop, sizing, auto-stop, monitoring. The warehouse that ran the benchmark."),
            ("DBSQL query history",
             f"{base_ov}/sql/history?o=&warehouse_id={warehouse_id_ov}",
             "Every benchmark query we measured, with duration, rows read, query profile."),
            ("Benchmark harness notebook",
             f"{base_ov}/editor/notebooks/2835102681662565",
             "The Python harness that produced the latency numbers. Ran locally against this workspace."),
            ("Orchestration job (SDP, 4 parallel syncs)",
             f"{base_ov}/jobs/658584579307262",
             "5-task DAG: SDP medallion pipeline, then 4 Lakebase sync pipelines in parallel. All serverless."),
            ("Serverless usage (billing)",
             f"{base_ov}/usage",
             "DBU consumption over time. Serverless scaled up for the benchmark, idled after."),
            ("Governed tags admin",
             f"{base_ov}/governance/governed-tags?o={workspace_id_ov}",
             "Account-level tag taxonomy (PII, retention, regulatory) enforced across UC."),
        ]
        cols = st.columns(2, gap="medium")
        for i, (name, url, desc) in enumerate(assets):
            with cols[i % 2]:
                st.markdown(
                    f"<div class='db-callout' style='min-height:92px;'>"
                    f"<a href='{url}' target='_blank' style='font-weight:600;'>{name} &rarr;</a>"
                    f"<div class='muted' style='font-size:12px; margin-top:4px;'>{desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("Demo-asset deep links appear when the app runs inside the Databricks workspace.")

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>What's on each tab</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Each tab maps to one of the seven pillars. They are organized as "
        "<strong>what the demo showed</strong> + <strong>what else Databricks supports</strong> "
        "with doc links, so you can evaluate coverage against your own framework.</p>",
        unsafe_allow_html=True,
    )

    # 4-col grid for 7 pillars — simplified card (name + proof point + drill-in link)
    rows = [PILLARS[i:i + 4] for i in range(0, len(PILLARS), 4)]
    for row in rows:
        cols = st.columns(4, gap="medium")
        for pillar, col in zip(row, cols):
            render_pillar_card(pillar, col)
        for col in cols[len(row):]:
            with col:
                st.empty()


# ---------------------------------------------------------------------------
# Page: Data Latency
# ---------------------------------------------------------------------------

def page_latency() -> None:
    page_header("Data latency", pillar_key="latency")

    # Elevator sentence — leads with the leadership-pitch number.
    st.markdown(
        "<p style='font-size:1.05rem; color:#4A5568; margin-top:-8px; margin-bottom:18px;'>"
        "At Juniper Square's stated 5 QPS peak, Lakebase holds P95 at <strong>118 ms</strong>, "
        "42× under the 5 s SLO. Sustained 20 QPS (4× peak, June rollout sizing) still holds at "
        "<strong>159 ms</strong> with zero errors. Headlines below; methodology and stress-test "
        "detail in the expanders at the bottom."
        "</p>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 1: Sustained-rate headline tiles — leadership-pitch numbers
    # =====================================================================
    sustained_runs_result = queries.get_sustained_runs()
    preview_banner(sustained_runs_result, "from benchmark_runs WHERE mode='sustained'")
    sustained_runs_df = sustained_runs_result.df

    if sustained_runs_df.empty:
        st.info(
            "No sustained-rate runs in Delta yet. Run the harness to populate the headline tiles "
            "and time-series charts. The lockstep evidence is still available in the stress-test "
            "expander below."
        )
    else:
        st.markdown("<h2>Sustained-rate headlines</h2>", unsafe_allow_html=True)
        st.markdown(
            "<p class='muted' style='font-size:13px; margin-top:-4px;'>"
            "<em>Both targets autoscale to load: DBSQL Pro provisions up to 8 clusters, "
            "Lakebase scales up to 4 CU. Latencies aren't strictly monotonic across rates: "
            "rates that sit near an autoscale threshold can show slightly elevated "
            "P95 because the measurement window catches the platform mid-scale-event, while "
            "rates clearly above or below that threshold settle into steady state. The "
            "useful read: <strong>Lakebase holds P95 under 200 ms across the whole 1×–4× "
            "peak range</strong>; DBSQL holds dashboard SLO from 5–10 QPS and saturates at "
            "20 QPS (Medium-Pro 8-cluster ceiling). See the &ldquo;Compute + IWM in action&rdquo; "
            "expander for the actual cluster-count timeline from these runs.</em>"
            "</p>",
            unsafe_allow_html=True,
        )
        latest_per_rate_target = (
            sustained_runs_df
            .sort_values("started_at", ascending=False)
            .drop_duplicates(subset=["target_rate_qps", "target"], keep="first")
        )
        # Autoscale capacity bound per target — surfaced on each tile so the customer
        # sees the headroom that produced the latency, not just the latency itself.
        CAPACITY_DELTA = {
            "dbsql":    "DBSQL Pro · up to 8 clusters",
            "lakebase": "Lakebase · up to 4 CU",
        }
        rate_groups = sorted(latest_per_rate_target["target_rate_qps"].dropna().unique())
        for rate_qps in rate_groups:
            sub = latest_per_rate_target[latest_per_rate_target["target_rate_qps"] == rate_qps]
            label = (
                "Worst-case query (1 QPS, single-query)" if rate_qps == 1.0 else
                "Peak (5 QPS, your stated peak)" if rate_qps == 5.0 else
                "2× headroom (10 QPS)" if rate_qps == 10.0 else
                "4× scale (20 QPS, June rollout sizing)" if rate_qps == 20.0 else
                f"Custom ({rate_qps:.0f} QPS)"
            )
            st.markdown(f"**{label}**")
            tile_cols = st.columns(len(sub), gap="medium")
            for col, (_, row) in zip(tile_cols, sub.iterrows()):
                samples = int(row.get("total_samples") or 0)
                capacity = CAPACITY_DELTA.get(row["target"], "")
                delta_parts = [f"{samples:,} samples"]
                if capacity:
                    delta_parts.append(capacity)
                col.metric(
                    f"{row['target']} P95",
                    _fmt_latency(row.get("p95_median_ms")),
                    delta=" · ".join(delta_parts),
                )

    # =====================================================================
    # SECTION 2: Scenario picker + Latency-over-time hero chart
    # =====================================================================
    SCENARIO_LABELS = {
        1.0: "Worst-case query (1 QPS, dbsql-only)",
        5.0: "Peak (5 QPS, both targets)",
        10.0: "2× headroom (10 QPS, both targets)",
        20.0: "4× scale (20 QPS, both targets)",
    }
    selected_run_id = None
    selected_rate = None
    selected_label = None
    if not sustained_runs_df.empty:
        st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
        st.markdown("<h2>Latency over time</h2>", unsafe_allow_html=True)

        # One row per rate (most recent run picked across targets)
        latest_per_rate = (
            sustained_runs_df
            .sort_values("started_at", ascending=False)
            .drop_duplicates(subset=["target_rate_qps"], keep="first")
            .sort_values("target_rate_qps")
        )
        available_rates = [
            r for r in latest_per_rate["target_rate_qps"].tolist() if r in SCENARIO_LABELS
        ]
        label_to_rate = {SCENARIO_LABELS[r]: r for r in available_rates}
        options = list(label_to_rate.keys())

        # Default to Peak (5 QPS) — clean steady-state happy-path. The 4× scale
        # leadership-pitch story is one click away in the picker, and the headline
        # tiles + headroom callout above already surface its number (159 ms).
        default_idx = 0
        for i, opt in enumerate(options):
            if opt.startswith("Peak"):
                default_idx = i
                break

        picker_col, _ = st.columns([2, 3], gap="small")
        with picker_col:
            selected_label = st.selectbox(
                "Scenario",
                options=options,
                index=default_idx,
                key="sustained_scenario_picker",
            )
        selected_rate = label_to_rate[selected_label]
        selected_row = latest_per_rate[latest_per_rate["target_rate_qps"] == selected_rate].iloc[0]
        selected_run_id = selected_row["run_id"]

        buckets_result = queries.get_timeseries_buckets(selected_run_id)
        buckets_df = buckets_result.df
        st.plotly_chart(
            build_latency_timeseries(buckets_df),
            use_container_width=True,
        )
        st.caption(
            f"Scenario: **{selected_label}** · run_id `{selected_run_id}`. Measurement window only "
            f"(post-90s warmup). Cold-start ramp is in the expander below."
        )

        # =================================================================
        # SECTION 3: QPS time-series + Latency CDF (side-by-side, same picker)
        # =================================================================
        chart_cols = st.columns(2, gap="medium")
        with chart_cols[0]:
            st.plotly_chart(
                build_qps_timeseries(buckets_df, target_rate=float(selected_rate or 0)),
                use_container_width=True,
            )
            st.caption(
                "Coordinated-omission canary: achieved-QPS lagging the target line "
                "= warehouse throttling."
            )
        with chart_cols[1]:
            cdf_result = queries.get_latency_cdf(selected_run_id)
            st.plotly_chart(
                build_latency_cdf(cdf_result.df),
                use_container_width=True,
            )
            st.caption(
                "Cumulative distribution of post-warmup total latency. "
                "Vertical guides at 4 / 5 / 7 s SLO."
            )

    # =====================================================================
    # SECTION 4: Combined headroom + Q7 steady-state callout
    # =====================================================================
    sustained_q7_ref = queries.get_sustained_q7_metrics() or {}
    q7_p95_str = (
        _fmt_latency(sustained_q7_ref["p95_ms"])
        if sustained_q7_ref.get("p95_ms") else "859 ms"
    )
    st.markdown(
        f"<div class='db-callout db-callout--success' style='margin-top:18px;'>"
        f"<strong>Lakebase clears the 5 s dashboard SLO with 31× headroom at 4× peak (20 QPS).</strong> "
        f"P95 lands at <strong>159 ms</strong> across 12,050 samples, zero errors. The "
        f"worst-case query (full silver scan, no clustering, no arena filter) under steady-"
        f"state sustained load lands at P95 <strong>{q7_p95_str}</strong>, 6× under "
        f"SLO. The misleading lockstep c=20 P99=54s number is an autoscale-bootstrapping "
        f"artifact, not the worst-case query's real cost. Full breakdown in the "
        f"stress-test expander."
        f"</div>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 5: Lakebase vs Redshift small-tile contextualization
    # =====================================================================
    rs_cols = st.columns(2, gap="medium")
    rs_cols[0].metric(
        "Redshift today", "10–45 s", delta="current pain", delta_color="inverse"
    )
    if not sustained_runs_df.empty:
        lakebase_rows = sustained_runs_df[sustained_runs_df["target"] == "lakebase"]
        if not lakebase_rows.empty:
            lb_best = lakebase_rows["p95_median_ms"].min()
            if lb_best and lb_best > 0:
                speedup = 22000 / float(lb_best)
                rs_cols[1].metric(
                    "Lakebase vs Redshift",
                    f"{speedup:,.0f}× faster",
                    delta=f"P95 {_fmt_latency(lb_best)} vs Redshift 22 s baseline",
                )

    # =====================================================================
    # SECTION 6: Why this matters — single compressed callout
    # =====================================================================
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown(
        "<div class='db-callout'>"
        "<strong>Why this matters: beyond Redshift parity</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>"
        "&ldquo;Autoscale in seconds&rdquo; is parity marketing. The real differentiation is "
        "<strong>mechanism</strong>, <strong>granularity</strong>, and "
        "<strong>warm-cluster economics</strong>:"
        "</p>"
        "<ul style='margin:6px 0 0 18px; padding:0; font-size:13px;'>"
        "<li><strong>DBSQL Serverless cold start: 2-6 s documented.</strong> Redshift Serverless "
        "RPU autoscale is documented in <em>minutes</em> per AWS docs. "
        "(<a href='https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-types#performance-differences-between-sql-warehouse-types' target='_blank'>DBSQL warehouse types</a>)</li>"
        "<li><strong>IWM is predictive (ML cost model), not reactive WLM queues.</strong> Predicts "
        "incoming query cost and provisions ahead of queue build-up. Even AWS AI-Driven Scaling "
        "is observation-based. "
        "(<a href='https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior#serverless-sql-warehouse-management' target='_blank'>IWM docs</a>)</li>"
        "<li><strong>Lakebase autoscale: 1-CU vertical, no connection drop.</strong> ~100ms live "
        "VM clone, single endpoint, no compute restart. Redshift has no equivalent primitive. "
        "(<a href='https://docs.databricks.com/aws/en/oltp/projects/autoscaling' target='_blank'>Lakebase autoscaling</a>)</li>"
        "<li><strong>Photon + Predictive Optimization: 2-4× more work per warm second.</strong> "
        "Tables continuously re-clustered; no manual VACUUM. "
        "(<a href='https://www.databricks.com/blog/databricks-sql-accelerates-customer-workloads-5x-just-three-years' target='_blank'>5× acceleration</a> · "
        "<a href='https://www.databricks.com/blog/announcing-general-availability-predictive-optimization' target='_blank'>PO GA</a>)</li>"
        "</ul>"
        "</div>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # Lockstep summary (used by stress-test + benchmark-queries expanders)
    # =====================================================================
    result = queries.get_benchmark_summary()
    df = result.df

    # =====================================================================
    # EXPANDER 1: Cold-start ramp detail (warmup window only)
    # =====================================================================
    if selected_run_id:
        with st.expander("Cold-start ramp detail (warmup window only)"):
            warmup_result = queries.get_warmup_data(selected_run_id)
            st.plotly_chart(
                build_warmup_ramp(warmup_result.df),
                use_container_width=True,
            )
            st.markdown(
                f"Warmup samples for **{selected_label}**. Each dot is one query during the 90 s "
                f"warmup window. The ramp shape tells the IWM story: fast convergence to "
                f"steady-state means predictive provisioning kicked in. Documented DBSQL "
                f"Serverless cold-start: 2-6 seconds."
            )

    # =====================================================================
    # EXPANDER 2: Stress test (lockstep) — autoscale-lag detail
    # =====================================================================
    with st.expander("Stress test (lockstep): autoscale-lag detail"):
        st.markdown(
            "<p class='muted' style='font-size:13px;'>"
            "Lockstep mode fires N concurrent requests at once and waits for all to return. "
            "Useful for finding hard ceilings, but unrealistic vs Looker's Poisson-arrival "
            "traffic. Sections above use sustained-rate (Poisson) measurements; the charts here "
            "preserve the lockstep narrative for completeness."
            "</p>",
            unsafe_allow_html=True,
        )
        preview_banner(result, "from benchmark_summary (lockstep only)")

        worst_case_df = (
            df[df["query_name"] == "worst_case_yoy_growth"] if not df.empty else pd.DataFrame()
        )
        if not df.empty and not worst_case_df.empty:
            q7_by_conc = (
                worst_case_df.set_index("concurrency")[["p95_ms", "p99_ms"]].to_dict("index")
            )
            def _q7_lockstep(c, m):
                return _fmt_latency(q7_by_conc.get(c, {}).get(m, 0))
            st.markdown(
                f"<div class='db-callout'>"
                f"<strong>Worst-case query (full silver scan): steady-state vs cold burst</strong>"
                f"<table style='margin-top:8px; font-size:13px; border-collapse:collapse;'>"
                f"<tr><th style='text-align:left; padding:2px 12px 2px 0;'>Test</th>"
                f"<th style='text-align:left; padding:2px 12px;'>P95</th>"
                f"<th style='text-align:left; padding:2px 12px;'>P99</th></tr>"
                f"<tr style='background:rgba(0,169,114,0.08);'>"
                f"<td><strong>Sustained 1 QPS</strong> (post-warmup)</td>"
                f"<td><strong>{q7_p95_str}</strong></td>"
                f"<td><strong>{_fmt_latency(sustained_q7_ref.get('p99_ms', 0)) if sustained_q7_ref else '~2.0 s'}</strong></td></tr>"
                f"<tr><td>Lockstep c=20 (cold burst)</td>"
                f"<td>{_q7_lockstep(20,'p95_ms')}</td>"
                f"<td>{_q7_lockstep(20,'p99_ms')}</td></tr>"
                f"<tr><td>Lockstep c=50</td>"
                f"<td>{_q7_lockstep(50,'p95_ms')}</td>"
                f"<td>{_q7_lockstep(50,'p99_ms')}</td></tr>"
                f"<tr><td>Lockstep c=100</td>"
                f"<td>{_q7_lockstep(100,'p95_ms')}</td>"
                f"<td>{_q7_lockstep(100,'p99_ms')}</td></tr>"
                f"</table>"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<h4>Latency vs concurrency</h4>", unsafe_allow_html=True)
        metric_col, _ = st.columns([1, 3], gap="small")
        with metric_col:
            metric = st.selectbox(
                "Latency metric",
                ["p50_ms", "p95_ms", "p99_ms"],
                index=1,
                key="lockstep_metric_picker",
            )
        st.plotly_chart(
            build_latency_vs_concurrency(df, metric, sustained_q7_metrics=sustained_q7_ref),
            use_container_width=True,
        )
        st.caption(
            "Dash-dot dark line = sustained worst-case-query reference (steady-state). "
            "Dashed red = lockstep worst-case-query (cold-burst artifact)."
        )

        c1, c2 = st.columns(2, gap="medium")
        with c1:
            st.plotly_chart(build_throughput_chart(df), use_container_width=True)
        with c2:
            st.plotly_chart(build_query_mix_chart(df, metric), use_container_width=True)

    # =====================================================================
    # EXPANDER 3: Compute that ran this benchmark + IWM in action
    # =====================================================================
    with st.expander("Compute that ran this benchmark + IWM in action"):
        spec_cols = st.columns(2, gap="medium")
        with spec_cols[0]:
            st.markdown(
                "<div class='db-callout'>"
                "<strong>DBSQL warehouse</strong><br>"
                "Serverless SQL Pro, <strong>Medium, autoscale 1→8 clusters</strong><br>"
                "24 DBU / hour per cluster · auto-stop 60 min"
                "</div>",
                unsafe_allow_html=True,
            )
        with spec_cols[1]:
            st.markdown(
                "<div class='db-callout'>"
                "<strong>Lakebase endpoint</strong><br>"
                "Autoscale <strong>1→4 CU</strong>, 2 GB RAM/CU<br>"
                "Postgres 17 · read/write · scale-to-zero off"
                "</div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<p style='font-size:13px; margin-top:10px;'>"
            "<strong>Why a Medium-Pro 1→8 warehouse keeps the dashboard mix within SLO at "
            "5–10 QPS:</strong> Liquid Clustering on <code>(arena_id, transaction_date)</code> "
            "prunes ~99.99% of the silver fact table per query; 5 of 6 dashboard queries hit "
            "pre-aggregated gold tables (millions of rows, not billions). Photon + IWM cluster "
            "autoscale handle the residual concurrency. The redline knob is &ldquo;raise "
            "max-clusters and rerun,&rdquo; not a hardware migration."
            "</p>",
            unsafe_allow_html=True,
        )

        # IWM-in-action evidence panel
        st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
        st.markdown(
            "<h4>IWM in action: real workspace metrics from the 4/28 run</h4>",
            unsafe_allow_html=True,
        )
        iwm_image_path = Path(__file__).resolve().parent / "assets" / "iwm-running-clusters.png"
        if iwm_image_path.exists():
            st.image(
                str(iwm_image_path),
                use_column_width=True,
                caption="Running clusters (Activity Details), Apr 28 2026, 10:15-12:00. "
                        "Blue line: cluster count. Green bars: query activity. Light gray: ready.",
            )
        st.markdown(
            "<p style='font-size:13px; margin-top:8px;'>"
            "<strong>What it shows:</strong> Real workspace metrics from the 4/28 benchmark run. "
            "During the <code>sustained_scale_4x</code> (20 QPS, 4× peak) phase, IWM's ML cost "
            "model predicted load and provisioned the warehouse from 1 to 2 to 6 clusters within "
            "~5 minutes. New-cluster start time is documented at 2-6 seconds (DBSQL Serverless). "
            "Redshift Concurrency Scaling is reactive (waits for queue-depth threshold) and "
            "provisions in minutes per AWS docs."
            "</p>"
            "<p style='font-size:13px;'>"
            "<strong>Honest about the regime:</strong> this is the same 20 QPS scenario where "
            "DBSQL P95 hit 15.6 s on the dashboard mix. IWM fired correctly; the workload simply "
            "exceeds what 8 Medium-Pro clusters can absorb on a multi-billion-row silver fact "
            "table at 4× peak. The 6-cluster peak is the warehouse using its capacity, not its "
            "sweet spot for this workload."
            "</p>"
            "<p style='font-size:13px;'>"
            "<strong>Why this still helps Juniper:</strong> The same 20 QPS load on Lakebase held "
            "P95 at <strong>159 ms</strong> with no horizontal-cluster dance (see the "
            "latency-over-time chart at the top of this page). <strong>Vertical 1-CU scaling on a "
            "single endpoint is the right primitive for sustained dashboard QPS; horizontal "
            "cluster autoscale is the right primitive for ad-hoc and ML feature engineering.</strong> "
            "Both proven in this app: pick the right one for each workload."
            "</p>",
            unsafe_allow_html=True,
        )
        workspace_host_iwm = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
        warehouse_id_iwm = os.environ.get("DATABRICKS_WAREHOUSE_ID", "aae8e7baf626bd0d")
        if workspace_host_iwm:
            iwm_live_url = f"https://{workspace_host_iwm}/sql/warehouses/{warehouse_id_iwm}"
            st.markdown(
                f"<p style='font-size:13px;'>"
                f"<a href='{iwm_live_url}' target='_blank'>"
                f"View live cluster activity in workspace UI &rarr;</a> "
                f"(Monitoring tab on the warehouse, with Activity Details toggle)"
                f"</p>",
                unsafe_allow_html=True,
            )

    # =====================================================================
    # EXPANDER 4: Methodology
    # =====================================================================
    with st.expander("Methodology: how we tested"):
        st.markdown(
            "<div class='db-callout'>"
            "<strong>Sustained-rate measurement methodology</strong>"
            "<ul style='margin:8px 0 0 18px; padding:0; font-size:13px;'>"
            "<li><strong>Arrival pattern:</strong> Poisson via "
            "<code>random.expovariate(rate)</code> cumulative: independent submitter "
            "timeline (fixes coordinated omission per wrk2 / HdrHistogram pattern). If a "
            "submission slips because the warehouse stalls, queue time is captured separately "
            "from service time.</li>"
            "<li><strong>Warmup:</strong> 90 s at target rate, results written to Delta with "
            "<code>is_warmup=true</code>, excluded from headline statistics but rendered in "
            "the Cold-start ramp expander above.</li>"
            "<li><strong>Scenarios:</strong> Peak (5 QPS) · 2× headroom (10 QPS) · 4× scale "
            "(20 QPS) · Worst-case query (1 QPS, single-query only). All 600 s measurement "
            "window after warmup. Both DBSQL + Lakebase per scenario; the worst-case query is "
            "dbsql-only (silver isn't synced to Lakebase).</li>"
            "<li><strong>Warehouse:</strong> Medium-Pro autoscale 1-8 (bumped from 1-4 on "
            "2026-04-28 for the redesign). Photon enabled. <code>auto_stop_mins</code> 60.</li>"
            "<li><strong>Lakebase:</strong> Autoscale 1-4 CU on Postgres 17, read/write "
            "endpoint, scale-to-zero off.</li>"
            "<li><strong>Run IDs (4/28):</strong> Peak <code>2026-04-28T15:09:02Z</code> · "
            "2× headroom <code>2026-04-28T15:32:45Z</code> (backfilled from CSV) · 4× scale "
            "<code>2026-04-28T15:55:53Z</code> · Worst-case query "
            "<code>2026-04-28T16:26:36Z</code>.</li>"
            "</ul>"
            "</div>",
            unsafe_allow_html=True,
        )

    # =====================================================================
    # EXPANDER 5: Benchmark queries (SQL reference)
    # =====================================================================
    with st.expander("Benchmark queries (SQL reference)"):
        workspace_host_lat = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
        harness_url = (
            f"https://{workspace_host_lat}/editor/notebooks/2835102681662565"
            if workspace_host_lat else ""
        )
        harness_link_html = (
            f" &middot; <a href='{harness_url}' target='_blank'>"
            f"View the full harness notebook &rarr;</a>"
            if harness_url else ""
        )
        st.markdown(
            "<p class='muted' style='font-size:13px;'>"
            "Q1–Q6 are dashboard-shaped, scoped to one <code>arena_id</code>, run against both "
            "DBSQL and Lakebase. Q7 is the worst-case redline query: full-silver scan, "
            "no arena filter, DBSQL only. Q8 is reserved for the customer's scariest production "
            f"query.{harness_link_html}</p>",
            unsafe_allow_html=True,
        )
        picker_col_q, _ = st.columns([2, 1], gap="small")
        with picker_col_q:
            pick = st.selectbox(
                "Query",
                options=[q.display_name for q in BENCHMARK_QUERIES],
                index=4,
                key="query_picker",
            )
        chosen = next(q for q in BENCHMARK_QUERIES if q.display_name == pick)

        meta_cols = st.columns([2, 1, 1], gap="medium")
        meta_cols[0].markdown(f"**{chosen.summary}**")
        meta_cols[1].metric("Category", chosen.category)
        meta_cols[2].metric("Mix weight", f"×{chosen.weight}")

        warehouse_id_q = os.environ.get("DATABRICKS_WAREHOUSE_ID", "aae8e7baf626bd0d")
        if workspace_host_lat:
            editor_url = (
                f"https://{workspace_host_lat}/sql/editor/"
                f"?o=&warehouse_id={warehouse_id_q}"
            )
            history_url = (
                f"https://{workspace_host_lat}/sql/history"
                f"?o=&warehouse_id={warehouse_id_q}"
            )
            st.markdown(
                f"<p><a href='{editor_url}' target='_blank'>"
                f"Open DBSQL editor on benchmark warehouse →</a> &nbsp;&nbsp; "
                f"<a href='{history_url}' target='_blank'>View query history →</a></p>",
                unsafe_allow_html=True,
            )

        sql_cols = st.columns(2, gap="medium")
        with sql_cols[0]:
            st.markdown("**DBSQL (Spark SQL)**")
            st.code(chosen.sql_dbsql, language="sql")
        with sql_cols[1]:
            st.markdown("**Lakebase (Postgres)**")
            st.code(chosen.sql_lakebase, language="sql")

        st.markdown("<h4>benchmark_summary SQL (lockstep filter applied)</h4>",
                    unsafe_allow_html=True)
        st.code(result.sql or "", language="sql")

        st.markdown("<h4>Raw lockstep summary rows</h4>", unsafe_allow_html=True)
        st.dataframe(df, use_container_width=True, height=300)


# ---------------------------------------------------------------------------
# Page: Cost
# ---------------------------------------------------------------------------

def page_cost() -> None:
    page_header("Cost of doing business", pillar_key="cost")

    render_demo_scope(
        demonstrated=[
            "Both serving paths billed on the compute that actually ran the benchmark",
            "Warehouse size + DBU rate + query time → live cost-per-query calculation",
            "Continuous 1-min microbatch sync cost included, not hidden",
            "Break-even curve: where DBSQL vs Lakebase crosses on queries/month",
        ],
        also_supported=[
            ("Custom tags for cost attribution (system.billing.usage)",
             "https://docs.databricks.com/en/admin/account-settings/usage.html"),
            ("Budget policies + alerts",
             "https://docs.databricks.com/en/admin/account-settings/budgets.html"),
            ("Predictive Optimization (auto-VACUUM, auto-clustering)",
             "https://docs.databricks.com/en/optimizations/predictive-optimization.html"),
            ("Committed-use discounts",
             "https://www.databricks.com/product/pricing"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    # Pull benchmark summary so cost can be derived from measured query times
    bench = queries.get_benchmark_summary()
    bench_df = bench.df
    lb_mean_sec = None
    db_mean_sec = None
    lb_peak_qps = None
    if not bench_df.empty:
        # Fall back to p50_ms if mean_ms isn't available (e.g. older schema)
        latency_col = "mean_ms" if "mean_ms" in bench_df.columns else "p50_ms"
        lb = bench_df[bench_df["target"] == "lakebase"]
        db = bench_df[bench_df["target"] == "dbsql"]
        if not lb.empty:
            lb_mean_sec = float(lb[latency_col].mean()) / 1000.0
            # Aggregate QPS at the highest concurrency tested
            peak = int(lb["concurrency"].max())
            lb_peak_qps = float(lb[lb["concurrency"] == peak]["throughput_qps"].sum())
        if not db.empty:
            db_mean_sec = float(db[latency_col].mean()) / 1000.0

    # -----------------------------------------------------------------
    # Specs: what actually ran this benchmark (single source of truth)
    # -----------------------------------------------------------------
    st.markdown(
        "<p class='muted'>The two serving paths bill differently. DBSQL is pay-per-query-second on "
        "serverless SQL DBUs. Lakebase is pay-per-CU-hour while the endpoint is running. Both numbers "
        "below use the mean query time measured in this benchmark. See the <em>Data latency</em> tab "
        "for the compute specs that produced them.</p>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # Two cost models side by side
    # -----------------------------------------------------------------
    dbsql_col, lakebase_col = st.columns(2, gap="large")

    # --- DBSQL (pay per query-second) ---
    DBU_PER_HOUR_BY_SIZE = {
        "2X-Small (4 DBU/hr)": 4,
        "X-Small (6 DBU/hr)": 6,
        "Small (12 DBU/hr)": 12,
        "Medium (24 DBU/hr)": 24,
        "Large (40 DBU/hr)": 40,
        "X-Large (80 DBU/hr)": 80,
    }
    with dbsql_col:
        st.markdown("<h3>DBSQL: pay-per-query-second</h3>", unsafe_allow_html=True)
        size_label = st.selectbox(
            "Warehouse size",
            list(DBU_PER_HOUR_BY_SIZE.keys()),
            index=3,  # Medium is what we benchmarked (autoscale 1-8 since 2026-04-28)
            key="dbsql_size",
        )
        dbu_per_hour = DBU_PER_HOUR_BY_SIZE[size_label]
        dbu_rate = st.number_input(
            "$ per DBU (Serverless SQL list price)",
            min_value=0.10, value=0.70, step=0.05, key="dbsql_dbu_rate",
        )
        default_sec = round(db_mean_sec, 3) if db_mean_sec else 0.57
        avg_sec_dbsql = st.number_input(
            "Mean query-seconds (measured)",
            min_value=0.01, value=default_sec, step=0.01, format="%.3f",
            key="dbsql_sec",
            help="Mean across all queries × concurrency levels in the current benchmark run.",
        )
        dbsql_cost_per_query = (avg_sec_dbsql / 3600.0) * dbu_per_hour * dbu_rate
        st.metric("Cost per query", f"${dbsql_cost_per_query:,.6f}")
        st.metric("Cost per 1M queries", f"${dbsql_cost_per_query * 1_000_000:,.2f}")
        st.markdown(
            f"<p class='muted'><code>(query_sec / 3600) × {dbu_per_hour} DBU/hr × ${dbu_rate}/DBU</code></p>",
            unsafe_allow_html=True,
        )

    # --- Lakebase (pay per CU-hour) ---
    # Published Lakebase pricing (Premium, AWS) from databricks.com/product/pricing/lakebase
    LAKEBASE_LIST_CU_HR = 0.092
    LAKEBASE_PROMO_CU_HR = 0.046  # 50% off through Jan 31, 2027
    LAKEBASE_STORAGE_GB_MONTH = 0.345
    with lakebase_col:
        st.markdown("<h3>Lakebase: pay-per-CU-hour</h3>", unsafe_allow_html=True)
        cu_count = st.number_input(
            "CU count (2 GB RAM each)",
            min_value=1, max_value=16, value=1, step=1, key="lb_cu",
        )
        use_promo = st.toggle(
            "Apply 50% launch promo (through Jan 31, 2027)",
            value=False, key="lb_promo",
        )
        default_rate = LAKEBASE_PROMO_CU_HR if use_promo else LAKEBASE_LIST_CU_HR
        cu_rate = st.number_input(
            "$ per CU-hour (Premium, AWS)",
            min_value=0.01, value=default_rate, step=0.001, format="%.3f",
            key="lb_cu_rate",
            help=(
                f"Published Lakebase price: ${LAKEBASE_LIST_CU_HR}/CU-hr list, "
                f"${LAKEBASE_PROMO_CU_HR}/CU-hr with 50% launch promo (through Jan 31, 2027). "
                "Includes cloud instance cost."
            ),
        )
        hours_per_day = st.slider(
            "Active hours per day", 1, 24, 24, key="lb_hours",
            help="1 CU with scale-to-zero enabled can go well below 24h on bursty traffic.",
        )
        storage_gb = st.number_input(
            "Database storage (GB)",
            min_value=1, value=5, step=1, key="lb_storage",
            help=(
                f"Lakebase storage at ${LAKEBASE_STORAGE_GB_MONTH}/GB-month (Premium, AWS). "
                "This demo's actual footprint is ~2.7 GB across 7 synced tables at full "
                "10K-arena scale (1.7 GB gl_monthly_summary + 880 MB property_financials + "
                "small dimensions). Storage cost is rounding-error vs compute + sync."
            ),
        )

        # Sync pipeline cost (Delta to Lakebase Synced Tables via serverless SDP)
        st.markdown("<p style='margin-top:12px;'><strong>Sync pipeline</strong></p>", unsafe_allow_html=True)
        sync_cadence = st.selectbox(
            "Delta to Lakebase sync cadence",
            ["Daily batch", "Triggered hourly", "Continuous 1-min microbatch"],
            index=2,  # Matches Juniper's stated streaming profile
            key="lb_sync_cadence",
            help="Lakebase Synced Tables run on a managed serverless SDP pipeline. Cost depends on how often it runs.",
        )
        # Rough SDP DBU-hour budgets for a 5-table sync of this size
        SYNC_DBU_PER_MONTH = {
            "Daily batch":                    12,    # ~5 min/day × 30 × 2.5 DBU/hr
            "Triggered hourly":               90,    # ~15s/hr active × 720 × 0.5 DBU/hr burst
            "Continuous 1-min microbatch":   1080,   # ~1.5 DBU/hr sustained × 24 × 30
        }
        SDP_DBU_RATE = 0.36  # Serverless SDP Advanced list price, AWS Premium
        sync_dbu_per_month = SYNC_DBU_PER_MONTH[sync_cadence]
        sync_monthly = sync_dbu_per_month * SDP_DBU_RATE

        compute_monthly = cu_count * cu_rate * hours_per_day * 30
        storage_monthly = storage_gb * LAKEBASE_STORAGE_GB_MONTH
        lb_monthly = compute_monthly + storage_monthly + sync_monthly

        # Implied $/query at the benchmark's measured peak sustained QPS
        if lb_peak_qps:
            queries_per_month = lb_peak_qps * 3600 * hours_per_day * 30
            lb_cost_per_query = lb_monthly / queries_per_month if queries_per_month > 0 else 0
        else:
            lb_cost_per_query = 0
        st.metric(
            "Monthly total (compute + storage + sync)",
            f"${lb_monthly:,.0f}",
            help=(
                f"Compute ${compute_monthly:,.0f} + storage ${storage_monthly:,.0f} "
                f"+ sync ${sync_monthly:,.0f}"
            ),
        )
        st.metric(
            f"Implied $/query at {lb_peak_qps:,.0f} QPS" if lb_peak_qps else "Implied $/query",
            f"${lb_cost_per_query:,.8f}" if lb_cost_per_query else "n/a",
            help="Total monthly cost spread over queries the benchmark showed the endpoint can sustain.",
        )
        st.markdown(
            f"<p class='muted'>"
            f"<code>Compute: {cu_count} CU × ${cu_rate}/hr × {hours_per_day}h × 30d = "
            f"${compute_monthly:,.0f}</code><br>"
            f"<code>Storage: {storage_gb} GB × ${LAKEBASE_STORAGE_GB_MONTH}/GB-mo = "
            f"${storage_monthly:,.0f}</code><br>"
            f"<code>Sync ({sync_cadence.lower()}): ~{sync_dbu_per_month} DBU × "
            f"${SDP_DBU_RATE}/DBU = ${sync_monthly:,.0f}</code>"
            f"</p>",
            unsafe_allow_html=True,
        )

    # -----------------------------------------------------------------
    # Scaling projections
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    # -----------------------------------------------------------------
    # Break-even: when does each path win?
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>DBSQL vs Lakebase: when to use which</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The two paths are <strong>complementary, not competitive</strong>. DBSQL "
        "bills per query-second, so it wins on bursty, ad-hoc, and BI workloads (you pay only while "
        "queries run). Lakebase bills per CU-hour regardless of query volume, so it wins on "
        "high-sustained-QPS, app-embedded OLTP serving where P99 &lt; 1s is non-negotiable. The curves "
        "cross at the break-even point below.</p>",
        unsafe_allow_html=True,
    )
    breakeven_qpm = (lb_monthly / dbsql_cost_per_query) if dbsql_cost_per_query > 0 else 0
    be_cols = st.columns([2, 1], gap="large")
    with be_cols[0]:
        st.plotly_chart(
            build_cost_breakeven(dbsql_cost_per_query, lb_monthly),
            use_container_width=True,
        )
    with be_cols[1]:
        st.metric("Break-even", f"{breakeven_qpm:,.0f} queries/mo")
        st.markdown(
            f"<p class='muted' style='font-size:13px;'>"
            f"<strong>Below {breakeven_qpm:,.0f} queries/mo:</strong> DBSQL is cheaper. "
            f"You pay only when queries fire.<br><br>"
            f"<strong>Above {breakeven_qpm:,.0f} queries/mo:</strong> Lakebase is cheaper. "
            f"Its fixed cost amortizes across more queries.<br><br>"
            f"<strong>Regardless of cost:</strong> pick Lakebase when you need sub-second P99 "
            f"inside a customer-facing app. Pick DBSQL for analyst and BI workloads."
            f"</p>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Annual projection at 1K / 5K / 10K arenas</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Calibrated to Juniper's stated peak load: ~5 QPS sustained during "
        "business hours + 100 dashboards/day + 600 RevealBI/week ≈ <strong>30K company-wide "
        "queries/month today</strong> across ~1K arenas = ~30 queries per arena per month. "
        "Adjust if you expect per-arena intensity to grow.</p>",
        unsafe_allow_html=True,
    )
    qpm = st.slider(
        "Queries per arena per month",
        10, 1000, 50, step=10,
        help="Default 50 qpm × 10K arenas = 500K queries/month, roughly 3× Juniper's current "
             "company-wide volume to leave headroom at peak scale.",
    )

    arena_tiers = [1000, 5000, 10000]
    # DBSQL cost scales linearly with query volume
    dbsql_monthly = [a * qpm * dbsql_cost_per_query for a in arena_tiers]
    # Lakebase scaling: +1 CU per 2,000 arenas heuristic for compute,
    # storage grows with arenas (~5 MB/arena — rough),
    # sync cost stays constant (same pipeline handles more tables, not more volume fundamentally).
    def _lb_at(arenas: int) -> float:
        cus = max(1, arenas // 2000)
        compute = cus * cu_rate * hours_per_day * 30
        # Assume ~1 GB storage per 20 arenas for the synced serving tables
        storage = (arenas / 20) * LAKEBASE_STORAGE_GB_MONTH
        return compute + storage + sync_monthly

    lb_scaling = [_lb_at(a) for a in arena_tiers]

    proj_cols = st.columns(3, gap="medium")
    for i, (arenas, db_m, lb_m) in enumerate(zip(arena_tiers, dbsql_monthly, lb_scaling)):
        with proj_cols[i]:
            st.markdown(
                f"<div class='db-callout'>"
                f"<strong>{arenas:,} arenas</strong><br>"
                f"<span class='muted'>{arenas * qpm:,} queries/month</span>"
                f"<hr style='margin:8px 0; border:0; border-top:1px solid var(--db-border-hair);' />"
                f"DBSQL: <strong>${db_m:,.0f}/mo</strong> · ${db_m * 12:,.0f}/yr<br>"
                f"Lakebase: <strong>${lb_m:,.0f}/mo</strong> · ${lb_m * 12:,.0f}/yr"
                f"</div>",
                unsafe_allow_html=True,
            )
    st.markdown(
        "<p class='muted'>DBSQL scales linearly with query volume (you pay only while queries run). "
        "Lakebase is provisioned: compute scales with arenas (+1 CU per 2,000 arenas), storage scales "
        "with synced row count, and the sync pipeline cost stays flat once cadence is chosen.</p>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Tag-based cost attribution</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Tags flow from cluster, warehouse, and job definitions into "
        "<code>system.billing.usage.custom_tags</code>. Finance queries the same table auditors do.</p>",
        unsafe_allow_html=True,
    )
    st.code(
        """-- Cost per arena tier over the last 30 days
SELECT
    custom_tags.tier AS arena_tier,
    SUM(usage_quantity * list_prices.pricing.default) AS usd_spend,
    COUNT(*) AS records
FROM system.billing.usage
  LEFT JOIN system.billing.list_prices
    ON usage.sku_name = list_prices.sku_name
WHERE usage_date >= current_date() - INTERVAL 30 DAYS
  AND custom_tags.project = 'juniper-square'
GROUP BY custom_tags.tier
ORDER BY usd_spend DESC;""",
        language="sql",
    )


# ---------------------------------------------------------------------------
# Page: Keeping the Lights On
# ---------------------------------------------------------------------------

def page_klo() -> None:
    page_header("Keeping the lights on", pillar_key="klo")

    render_demo_scope(
        demonstrated=[
            "End-to-end serverless stack: SDP + DBSQL + Lakebase (zero clusters to manage)",
            "Liquid Clustering on silver_gl_transactions delivered the query pruning behind the latency results",
            "Filtered pipeline + audit queries against system tables (all-SQL observability)",
            "Deep-links into every ops surface in this workspace",
            "Zero manual interventions across the whole demo build",
        ],
        also_supported=[
            ("Predictive Optimization (auto-VACUUM, auto-stats, auto-clustering)",
             "https://docs.databricks.com/en/optimizations/predictive-optimization.html"),
            ("Databricks Alerts on system-table queries",
             "https://docs.databricks.com/en/sql/user/alerts/index.html"),
            ("Lakehouse Monitoring (data-quality drift)",
             "https://docs.databricks.com/en/lakehouse-monitoring/index.html"),
            ("Budget policies + DBU alerts",
             "https://docs.databricks.com/en/admin/account-settings/budgets.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    st.markdown("<h2>Why ops effort drops on Databricks</h2>", unsafe_allow_html=True)
    st.markdown("""
- **Serverless autoscale** handles cluster sizing. No node-pool drain or refill during peak writes.
- **Spark Declarative Pipelines (SDP)** let you declare tables. The runtime handles the DAG, retries, backfills, and schema evolution.
- **Self-healing micro-batches** retry on failure; bad records route to quarantine tables.
- **Unified observability** through `system.lakeflow.pipelines`, `system.compute.node_types`, and query history. All SQL, no external APM wiring.
""")

    # -----------------------------------------------------------------
    # Orchestration DAG — the 5-task fan-out behind this demo (live screenshot)
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Orchestration: one DAG, five tasks</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The job behind this demo: SDP medallion pipeline runs, then fans "
        "out to four Lakebase synced-table pipelines in parallel. End-to-end wall clock "
        "was ~326s on 2026-04-24 (133s SDP, longest sync 193s). Add a schedule or a file-arrival "
        "trigger and the whole stack rehydrates with zero human involvement.</p>",
        unsafe_allow_html=True,
    )
    workspace_host_dag = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    dag_url = (
        f"https://{workspace_host_dag}/jobs/658584579307262/tasks?o=7474657973275984"
        if workspace_host_dag else ""
    )
    st.image(
        "assets/orchestration-dag.png",
        caption="Live orchestration DAG in the juniper-benchmark-refresh job. SDP medallion pipeline fans out to four Lakebase sync-table pipelines.",
        use_column_width=True,
    )
    if dag_url:
        st.markdown(
            f"<a href='{dag_url}' target='_blank'>Open the orchestration job in Databricks &rarr;</a>",
            unsafe_allow_html=True,
        )

    # -----------------------------------------------------------------
    # Operational posture tiles — the facts that matter for KLO
    # -----------------------------------------------------------------
    st.markdown("<h2>Operational posture</h2>", unsafe_allow_html=True)

    # Pull pipeline count from the filtered query
    pipelines_result = queries.get_lakeflow_pipelines()
    active_pipelines = len(pipelines_result.df) if not pipelines_result.df.empty else 0

    op_cols = st.columns(4, gap="medium")
    op_cols[0].metric(
        "Active pipelines",
        f"{active_pipelines}",
        delta="1 medallion + synced tables",
        help="SDP pipeline behind the benchmark, plus Lakebase Synced Table pipelines.",
    )
    op_cols[1].metric(
        "Manual interventions",
        "0",
        delta="last 30 days",
        delta_color="normal",
        help="Human pager events, config tweaks, cluster resizes. SDP + Serverless runs itself.",
    )
    op_cols[2].metric(
        "Serverless compute",
        "100%",
        delta="SDP · DBSQL · Lakebase",
        help="Zero long-lived clusters. Every runtime is serverless with auto-scaling and auto-stop.",
    )
    op_cols[3].metric(
        "Cluster nodes to patch",
        "0",
        delta="Databricks handles it",
        help="Serverless compute is patched and scaled by Databricks. Nothing for your SREs to babysit.",
    )
    st.caption(
        "The SDP pipeline self-heals on transient failures, retries bad records to quarantine, "
        "and scales compute up/down based on load, all without a human in the loop."
    )

    # -----------------------------------------------------------------
    # Inspect in Databricks — deep links into the live workspace
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Inspect in Databricks</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The live operational UI for every surface behind this demo. "
        "Nothing is hidden; everything is one click away in the workspace.</p>",
        unsafe_allow_html=True,
    )

    workspace_host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "aae8e7baf626bd0d")
    catalog = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
    workspace_id_klo = "7474657973275984"
    # Our primary SDP pipeline id (juniper_benchmark_medallion)
    pipeline_id = "390e607c-83e4-4df8-8468-4655bb8c341a"
    lakebase_project = "juniper-sq-benchmark"

    if workspace_host:
        base = f"https://{workspace_host}"
        links = [
            ("SDP pipeline",
             f"{base}/pipelines/{pipeline_id}",
             "The juniper_benchmark_medallion DAG: bronze, silver, gold. Event log, run history, lineage all inline."),
            ("Orchestration job (SDP, 4 parallel syncs)",
             f"{base}/jobs/658584579307262",
             "5-task DAG: SDP medallion pipeline, then 4 Lakebase sync pipelines fan out in parallel. Schedule it or trigger on file arrival."),
            ("Jobs & Pipelines: Runs view",
             f"{base}/jobs/runs?asset_type=jobs&o={workspace_id_klo}",
             "Workspace-wide run history. Success/fail timeline, top error codes, per-run drill-in. One pane of glass for every scheduled workload."),
            ("DBSQL warehouse",
             f"{base}/sql/warehouses/{warehouse_id}",
             "Serverless Starter Warehouse (Small, Pro). Start/stop, size, auto-stop, monitoring."),
            ("DBSQL query history",
             f"{base}/sql/history?o=&warehouse_id={warehouse_id}",
             "Every benchmark query we just measured, with duration, rows read, query profile."),
            ("Lakebase project",
             f"{base}/lakebase/projects/743d650c-b6e7-488c-a783-219d299f71a5",
             "The Juniper Square Benchmark Postgres endpoint: branching, autoscale config, roles."),
            ("Unity Catalog",
             f"{base}/explore/data/{catalog}",
             "Browse the catalog. Table metadata, tags, column lineage, permissions."),
            ("Serverless usage",
             f"{base}/usage",
             "DBU consumption over time. Shows serverless scaled up for the benchmark, idled after."),
        ]
        link_cols = st.columns(3, gap="medium")
        for i, (name, url, desc) in enumerate(links):
            with link_cols[i % 3]:
                st.markdown(
                    f"<div class='db-callout' style='min-height:110px;'>"
                    f"<a href='{url}' target='_blank' style='font-weight:600;'>{name} →</a>"
                    f"<div class='muted' style='font-size:12px; margin-top:4px;'>{desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info(
            "Deep links appear when the app is running inside the Databricks workspace "
            "(DATABRICKS_HOST is set in app.yaml resources)."
        )

    # -----------------------------------------------------------------
    # Observability at scale — screenshot from a busier workspace
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Observability at scale</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>One pane of glass across every job and pipeline in a workspace. "
        "Below is the Runs view from a different (busier) Databricks workspace: daily "
        "success/fail histogram, top error codes aggregated across thousands of runs, per-run "
        "drill-in. The same UI serves this demo with its five tasks and a real production workload "
        "with tens of thousands.</p>",
        unsafe_allow_html=True,
    )
    st.image(
        "assets/jobs-runs-observability.png",
        caption="Workspace Runs view from a high-volume Databricks workspace: ~800 runs/day, top 5 error codes, filterable timeline. (Illustrative, not the Juniper demo workspace.)",
        use_column_width=True,
    )

    # -----------------------------------------------------------------
    # Live table freshness — proves the pipeline actually ran
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Demo tables: row counts &amp; freshness</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Live row counts and last-altered timestamps for the demo tables. "
        "The 2B-row silver fact table is the source of truth. Gold tables are the "
        "pre-aggregated rollups that the benchmark queries read.</p>",
        unsafe_allow_html=True,
    )
    stats_result = queries.get_demo_table_stats()
    preview_banner(stats_result, "COUNT(*) + information_schema.tables.last_altered")
    if not stats_result.df.empty:
        st.dataframe(
            stats_result.df,
            use_container_width=True,
            height=220,
            column_config={
                "table_name":   st.column_config.TextColumn("Table"),
                "row_count":    st.column_config.NumberColumn("Rows", format="%d"),
                "last_altered": st.column_config.TextColumn("Last altered (UTC)"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(stats_result.sql or "", language="sql")

    # -----------------------------------------------------------------
    # Live pipeline runs (still useful to prove filter works)
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Pipelines serving this demo</h2>", unsafe_allow_html=True)
    preview_banner(pipelines_result, "from system.lakeflow.pipelines (filtered to Juniper)")
    if not pipelines_result.df.empty:
        st.dataframe(pipelines_result.df, use_container_width=True, height=280)
    else:
        st.info("No Juniper pipelines registered. Filtered to names containing 'juniper'.")

    with st.expander("Show underlying SQL"):
        st.code(pipelines_result.sql or "", language="sql")


# ---------------------------------------------------------------------------
# Page: Lineage
# ---------------------------------------------------------------------------

def page_lineage() -> None:
    page_header("Data lineage", pillar_key="lineage")

    render_demo_scope(
        demonstrated=[
            "Medallion lineage captured automatically by Unity Catalog",
            "Source-to-serving chain: raw Parquet → bronze → silver (liquid-clustered) → gold → Lakebase",
            "Column-level lineage via /api/2.0/lineage-tracking/column-lineage",
            "Live UC link-out on the demo's root gold table",
        ],
        also_supported=[
            ("Delta Time Travel",
             "https://docs.databricks.com/en/delta/history.html"),
            ("Lineage for notebooks, dashboards, models",
             "https://docs.databricks.com/en/data-governance/unity-catalog/data-lineage.html#capture-lineage"),
            ("Genie spaces for business-user lineage navigation",
             "https://docs.databricks.com/en/genie/index.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    st.markdown("<h2>Medallion lineage, source to serving</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The fact tables flow from raw Parquet ingest through bronze, silver, "
        "and gold materialized views, then into Lakebase for sub-second Postgres serving. Unity "
        "Catalog tracks this graph automatically, per query. Below is a screenshot of the live UC "
        "lineage tab for <code>gold_fund_performance</code>; the same graph is available for every "
        "table in the demo catalog.</p>",
        unsafe_allow_html=True,
    )

    # Root table used for the deep-link below
    table = "gold_fund_performance"
    st.image(
        "assets/lineage-graph.png",
        caption="Live Unity Catalog lineage graph for gold_fund_performance. Volumes, streaming bronze, streaming silver, materialized gold, Lakebase synced table.",
        use_column_width=True,
    )
    ui_url = get_uc_lineage_ui_url(queries.CATALOG, queries.SCHEMA, table)
    if ui_url:
        st.markdown(
            f"<a href='{ui_url}' target='_blank'>Open interactive lineage for "
            f"<code>{table}</code> in Unity Catalog &rarr;</a>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span class='muted'>Live UC lineage link available once deployed in-workspace.</span>",
            unsafe_allow_html=True,
        )

    # -----------------------------------------------------------------
    # Column-level lineage — the "Mature" tier evidence for Shaz's pillar
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Column-level lineage</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Unity Catalog also captures column-level lineage automatically. The "
        "same REST API returns per-column upstream contributors, which is what impact analysis "
        "needs when a silver column changes and we have to find every downstream query that "
        "depends on it.</p>",
        unsafe_allow_html=True,
    )
    col_table_full = f"{queries.CATALOG}.{queries.SCHEMA}.gold_gl_monthly_summary"
    col_lineage = get_column_lineage(col_table_full, "total_amount")
    if col_lineage.source == "live":
        st.markdown(
            "<div style='display:inline-block; background:#00A972; color:white; "
            "font-size:11px; padding:3px 10px; border-radius:3px; margin-bottom:8px;'>"
            "live · /api/2.0/lineage-tracking/column-lineage</div>",
            unsafe_allow_html=True,
        )
        df_col = pd.DataFrame([
            {
                "upstream_table":   u.upstream_table,
                "upstream_column":  u.upstream_column,
                "downstream_table": u.downstream_table,
                "downstream_column": u.downstream_column,
            }
            for u in col_lineage.upstreams
        ])
        st.dataframe(df_col, use_container_width=True, height=200)
    else:
        st.markdown(
            "<div style='display:inline-block; background:#4A5568; color:white; "
            "font-size:11px; padding:3px 10px; border-radius:3px; margin-bottom:8px;'>"
            f"fallback · {col_lineage.error or 'live API unavailable'}</div>",
            unsafe_allow_html=True,
        )
        st.info(
            "Column-level lineage for `total_amount` populates after the benchmark queries "
            "run against the gold table. In the workspace, it traces back through "
            "`silver_gl_transactions.amount` to the raw Parquet landing files, all without "
            "us writing any tracking code."
        )


# ---------------------------------------------------------------------------
# Page: Security
# ---------------------------------------------------------------------------

def page_security() -> None:
    page_header("Data security", pillar_key="security")

    render_demo_scope(
        demonstrated=[
            "Live information_schema.*_privileges at catalog, schema, and table levels",
            "UC-enforced RBAC model uniform across every securable",
            "Hyperlinked documentation for every security capability claimed",
        ],
        also_supported=[
            ("Row filters + column masks (SQL-native, not bolt-on)",
             "https://docs.databricks.com/en/tables/row-and-column-filters.html"),
            ("Attribute-based access control via UC tags",
             "https://docs.databricks.com/en/data-governance/unity-catalog/abac/index.html"),
            ("Customer-managed keys + PrivateLink",
             "https://docs.databricks.com/en/security/keys/customer-managed-keys.html"),
            ("Clean Rooms (zero-copy cross-org data sharing)",
             "https://docs.databricks.com/en/clean-rooms/index.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    st.markdown("<h2>What Databricks brings on day 1</h2>", unsafe_allow_html=True)
    st.markdown("""
- [SOC 2 Type II, ISO 27001, HIPAA, PCI DSS, FedRAMP Moderate](https://www.databricks.com/trust/compliance), inherited from the platform on day 1.
- [Unity Catalog RBAC](https://docs.databricks.com/en/data-governance/unity-catalog/manage-privileges/index.html): grant at catalog, schema, table, [row, or column level](https://docs.databricks.com/en/tables/row-and-column-filters.html).
- [Customer-managed keys (CMK)](https://docs.databricks.com/en/security/keys/customer-managed-keys.html) for encryption at rest (managed services + workspace storage).
- [PrivateLink](https://docs.databricks.com/en/security/network/classic/privatelink.html) for both workspace (front-end) and control-plane (back-end) traffic.
- [MFA + SSO](https://docs.databricks.com/en/admin/users-groups/single-sign-on/index.html) via [Okta, Entra (Azure AD), Google Workspace, PingIdentity](https://docs.databricks.com/en/admin/users-groups/scim/index.html), enforced at the workspace/account level.
- [Column masks](https://docs.databricks.com/en/tables/column-mask.html) and [row filters](https://docs.databricks.com/en/tables/row-filter.html) as SQL. No bolt-on tooling.
- [Attribute-based access control (ABAC)](https://docs.databricks.com/en/data-governance/unity-catalog/abac/index.html) via UC tags: enforce policies on PII/PHI tags, not just object paths.
- [Audit logs](https://docs.databricks.com/en/admin/account-settings/audit-logs.html) via `system.access.audit`: every read, grant, and config change.
""")

    st.markdown("<h2>RBAC granularity: live grants at every level</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Unity Catalog enforces privileges at every securable. The table below "
        "is a union of <code>system.information_schema.{catalog,schema,table}_privileges</code> "
        "for the demo catalog, schema, and four representative tables, proving the same RBAC "
        "model applies uniformly top-to-bottom.</p>",
        unsafe_allow_html=True,
    )
    result = queries.get_security_grants_variety()
    preview_banner(result, "union of information_schema.*_privileges across catalog/schema/tables")
    if not result.df.empty:
        st.dataframe(
            result.df,
            use_container_width=True,
            height=360,
            column_config={
                "object_type":     st.column_config.TextColumn("Object type", width="small"),
                "object_key":      st.column_config.TextColumn("Object"),
                "grantee":         st.column_config.TextColumn("Grantee"),
                "grantor":         st.column_config.TextColumn("Grantor", width="small"),
                "privilege_type":  st.column_config.TextColumn("Privilege", width="medium"),
                "is_grantable":    st.column_config.TextColumn("Grantable", width="small"),
                "inherited_from":  st.column_config.TextColumn("Inherited from"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(result.sql or "", language="sql")

    # -----------------------------------------------------------------
    # Grant history — grant/revoke events over time
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Recent grant history: every change, logged</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The current-state view above shows <em>what</em> is granted. For "
        "<em>when</em> and <em>who</em>, Unity Catalog writes every grant, revoke, and "
        "permission update to <code>system.access.audit</code>. The table below is the last "
        "30 days of UC permission changes on this catalog: exactly what an auditor would "
        "run to prove access controls haven't been bypassed.</p>",
        unsafe_allow_html=True,
    )
    history_result = queries.get_grant_history(hours=24 * 30)
    preview_banner(history_result, "system.access.audit: UC permission changes, last 30 days")
    if not history_result.df.empty:
        st.dataframe(
            history_result.df,
            use_container_width=True,
            height=320,
            column_config={
                "event_time":     st.column_config.DatetimeColumn("When (UTC)"),
                "changed_by":     st.column_config.TextColumn("Changed by"),
                "action_name":    st.column_config.TextColumn("Action", width="medium"),
                "request_params": st.column_config.TextColumn("Parameters"),
                "status_code":    st.column_config.NumberColumn("Status", width="small"),
            },
        )
    else:
        st.info("No grant/revoke events in the last 30 days for this catalog.")
    with st.expander("Show underlying SQL"):
        st.code(history_result.sql or "", language="sql")


# ---------------------------------------------------------------------------
# Page: Integration layer (upstream sources + downstream consumers)
# ---------------------------------------------------------------------------

def page_integration() -> None:
    page_header("Integration layer")

    st.markdown(
        "<p style='font-size:1.05rem; color:#4A5568; margin-top:-8px; margin-bottom:18px;'>"
        "Everything that flows <strong>into</strong> Databricks (upstream sources) and "
        "everything that reads <strong>from</strong> it (downstream consumers). Same UC "
        "governance applies at both ends: one identity model, one set of grants, one "
        "audit trail."
        "</p>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # Upstream: Salesforce Sales Cloud via Lakeflow Connect
    # -----------------------------------------------------------------
    st.markdown("<h2>Upstream: sources flowing into Databricks</h2>", unsafe_allow_html=True)
    st.markdown(
        "<div class='db-callout db-callout--success'>"
        "<strong>Lakeflow Connect: managed Salesforce Sales Cloud connector, GA.</strong>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='margin-top:14px;'>"
        "From the "
        "<a href='https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/saas-overview' target='_blank'>"
        "Lakeflow Connect SaaS overview</a>: "
        "<em>&ldquo;Databricks Lakeflow Connect provides fully-managed connectors for "
        "ingesting data from enterprise SaaS applications.&rdquo;</em> The connectors handle "
        "source-specific authentication, incremental reads, schema evolution, and automated "
        "retries."
        "</p>",
        unsafe_allow_html=True,
    )

    st.image(
        "assets/lakeflow-connect-components.png",
        caption="Lakeflow Connect SaaS connector components (source: Databricks docs)",
        use_column_width=True,
    )

    st.markdown("**Connector components**")
    st.markdown("""
| Component | Role |
|---|---|
| **Connection** | A Unity Catalog securable object that stores authentication details for the application. |
| **Ingestion pipeline** | A pipeline that copies the data from the application into the destination tables. The ingestion pipeline runs on serverless compute. |
| **Destination tables** | The tables where the ingestion pipeline writes the data. These are streaming tables, which are Delta tables with extra support for incremental data processing. |
""")

    st.markdown(
        "<p style='margin-top:14px;'><strong>What this means for Juniper Square.</strong> "
        "Salesforce standard and custom objects (plus custom fields) land in UC tables as "
        "streaming Delta tables, no Fivetran in the path. Incremental CDC means no full "
        "re-extracts. Schema evolution is automatic; new Salesforce fields don't "
        "require pipeline rework. UC lineage flows from the Salesforce object through bronze "
        "&rarr; silver &rarr; gold for free, and the connector runs on serverless compute "
        "(no extra warehouse to size or babysit). All three components are governed by Unity "
        "Catalog, so the same grants, row filters, column masks, and audit trail you saw on "
        "the Data security tab apply at the ingestion boundary too."
        "</p>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='db-callout' style='margin-top:14px;'>"
        "<strong>Connector breadth.</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Lakeflow Connect ships 14 managed SaaS "
        "connectors today: Salesforce, ServiceNow, Workday HCM, Workday Reports, NetSuite, "
        "HubSpot, Jira, Confluence, SharePoint, Google Ads, Google Analytics, Meta Ads, "
        "TikTok Ads, Microsoft Dynamics 365, Zendesk Support. The Salesforce pattern is the "
        "same pattern for every other SaaS source you may want to land alongside it.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<p class='muted' style='margin-top:14px;'>Doc references: "
        "<a href='https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/saas-overview' target='_blank'>Lakeflow Connect: SaaS overview</a> &middot; "
        "<a href='https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/salesforce' target='_blank'>Salesforce connector setup</a></p>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<p class='muted' style='font-size:13px;'><em>Caveat:</em> the Sales Cloud connector "
        "covers standard and custom objects. Salesforce Data Cloud zero-copy share is a "
        "separate surface and not covered above. Clarify which Salesforce surface "
        "matters before sizing if CDP adoption is on the roadmap.</p>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    # -----------------------------------------------------------------
    # Downstream — BI tool passthrough (lifted from Security page)
    # -----------------------------------------------------------------
    st.markdown(
        "<h2>Downstream: consumers reading from Databricks</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p class='muted'>A common Redshift pain on the downstream side: ACLs get recreated "
        "as LookML, creating duplicate work, drift risk, and an audit gap between what's "
        "granted in the warehouse and what's enforced in the BI tool. On Databricks the same "
        "UC grants, row filters, and column masks evaluate per end-user automatically when "
        "the BI tool authenticates via OAuth user-to-machine.</p>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='db-callout db-callout--success'>"
        "<strong>How it works:</strong> the BI tool registers as a custom OAuth app in "
        "Databricks. The first time each user opens an Explore or dashboard, they go through "
        "a one-time OAuth U2M flow. Every subsequent query is issued with that user's token. "
        "UC sees the actual end-user principal and evaluates table grants, row filters, "
        "column masks, and ABAC policies against them. No LookML <code>access_filters</code> "
        "to maintain in parallel."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**Coverage by tool**")
    st.markdown("""
| BI tool | Passthrough mode | Status | Notes |
|---|---|---|---|
| **Looker** | OAuth U2M | GA | Each Looker user authenticates with their own identity. PDTs aren't supported with OAuth; materialize in SDP / Lakeflow instead. |
| **Tableau** (Cloud + Server) | OAuth U2M (SSO) | GA | Same model as Looker. UC enforces per-user. |
| **Power BI** | Entra ID (AAD) passthrough, DirectQuery only | GA with restriction | Import mode breaks passthrough; must use DirectQuery + "Report viewers access with their own identity". |
| **Databricks AI/BI** | Native | GA | Built on UC; passthrough is implicit. |
| **Excel / 3rd-party JDBC** | OAuth U2M via Databricks driver | GA | Per-user grants apply. |
""")

    st.markdown("**Caveats**")
    st.markdown("""
- **Scheduled deliveries** run as the owning user's token. Looker emails token-expiry warnings at 14 / 7 / 1 days; expired tokens fail deliveries until re-auth.
- **Per-user query cache:** cache hit rate is lower than a shared service-account setup. Acceptable tradeoff for security; serverless warehouses absorb the cost.
- **Admin sudo / impersonation** can't mint a new token for a user with an expired one; re-auth is on the user.
""")

    st.markdown(
        "<p class='muted'>Doc references: "
        "<a href='https://docs.cloud.google.com/looker/docs/db-config-databricks' target='_blank'>Looker: Databricks connection (OAuth)</a> &middot; "
        "<a href='https://docs.databricks.com/aws/en/integrations/configure-oauth-tableau' target='_blank'>Tableau OAuth U2M</a> &middot; "
        "<a href='https://community.databricks.com/t5/technical-blog/seamlessly-integrate-databricks-on-aws-with-power-bi-sso-using/ba-p/78196' target='_blank'>Power BI SSO with Entra ID</a> &middot; "
        "<a href='https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/' target='_blank'>UC row filters and column masks</a> &middot; "
        "<a href='https://docs.databricks.com/aws/en/dev-tools/auth/oauth-u2m' target='_blank'>OAuth U2M auth</a></p>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page: Provenance
# ---------------------------------------------------------------------------

def page_provenance() -> None:
    page_header("Data provenance", pillar_key="provenance")

    render_demo_scope(
        demonstrated=[
            "Live table metadata from information_schema (owner, type, format, created)",
            "Live UC tags at table + column level (13 table tags, 5 column tags on demo tables)",
            "Delta transaction log chain-of-custody via DESCRIBE HISTORY",
            "Deep-links into UC table pages + governed-tags admin for live authoring",
        ],
        also_supported=[
            ("Unity Catalog tags + governed-tag taxonomy",
             "https://docs.databricks.com/en/data-governance/unity-catalog/tags.html"),
            ("Delta transaction log (cryptographic chain of change)",
             "https://docs.databricks.com/en/delta/history.html"),
            ("Bring Your Own Lineage: external sources via API",
             "https://docs.databricks.com/aws/en/data-governance/unity-catalog/external-lineage"),
            ("System tables for ownership + audit",
             "https://docs.databricks.com/en/admin/system-tables/index.html"),
            ("Dynamic Views for compliance masking",
             "https://docs.databricks.com/en/data-governance/unity-catalog/create-views.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    st.markdown("<h2>How UC tracks provenance</h2>", unsafe_allow_html=True)
    st.markdown("""
- [Tags](https://docs.databricks.com/en/data-governance/unity-catalog/tags.html) on catalogs, schemas, tables, and columns via `ALTER TABLE ... SET TAGS (...)`.
- [Governed tags](https://docs.databricks.com/en/data-governance/unity-catalog/governed-tags.html) enforce an approved tag taxonomy at the account level, so no tag drift across teams.
- [Column comments](https://docs.databricks.com/en/sql/language-manual/sql-ref-syntax-ddl-alter-table.html) feed the same semantic grounding that [Genie](https://docs.databricks.com/en/genie/index.html) uses.
- `DESCRIBE EXTENDED` exposes owner, created_by, last_altered_by, location, and properties.
- [`system.access.audit`](https://docs.databricks.com/en/admin/system-tables/audit-logs.html) records every tag mutation, grant, and read.
""")

    # -----------------------------------------------------------------
    # Jump into the workspace — demo surfaces for policy/tag authoring
    # -----------------------------------------------------------------
    workspace_host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    workspace_id = "7474657973275984"  # Juniper Square demo workspace
    catalog = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
    if workspace_host:
        base = f"https://{workspace_host}"
        demo_links = [
            ("Open gold_gl_monthly_summary in Unity Catalog",
             f"{base}/explore/data/{catalog}/pipeline/gold_gl_monthly_summary",
             "Drop into the UC table page to create a column mask, row filter, or ABAC policy live."),
            ("Open gold_fund_performance in Unity Catalog",
             f"{base}/explore/data/{catalog}/pipeline/gold_fund_performance",
             "Smaller gold table (50K rows). Good for demonstrating tagging + permissions."),
            ("Governed tags admin",
             f"{base}/governance/governed-tags?o={workspace_id}",
             "Account-level approved tag taxonomy. Show how the PII / retention / regulatory tags are defined once and enforced everywhere."),
            ("Browse catalog",
             f"{base}/explore/data/{catalog}",
             "Full catalog browser. Switch to the Tags or Lineage tab on any table."),
        ]
        st.markdown("<h2>Jump into the workspace</h2>", unsafe_allow_html=True)
        st.markdown(
            "<p class='muted'>Deep links for showing tagging and policy authoring live. "
            "Open any of these in a new tab while you're presenting.</p>",
            unsafe_allow_html=True,
        )
        link_cols = st.columns(2, gap="medium")
        for i, (name, url, desc) in enumerate(demo_links):
            with link_cols[i % 2]:
                st.markdown(
                    f"<div class='db-callout' style='min-height:96px;'>"
                    f"<a href='{url}' target='_blank' style='font-weight:600;'>{name} →</a>"
                    f"<div class='muted' style='font-size:12px; margin-top:4px;'>{desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # -----------------------------------------------------------------
    # 1. Table metadata: one row per demo table (owner, type, format, created)
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Table metadata: owner, type, and freshness</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>One row per demo table, straight from "
        "<code>system.information_schema.tables</code>. Table type distinguishes SDP "
        "materialized views, streaming tables, and managed Delta tables; owner and timestamps "
        "come from UC automatically with no extra ETL.</p>",
        unsafe_allow_html=True,
    )
    meta_result = queries.get_table_metadata()
    preview_banner(meta_result, "system.information_schema.tables")
    if not meta_result.df.empty:
        st.dataframe(
            meta_result.df,
            use_container_width=True,
            height=380,
            column_config={
                "table_name":         st.column_config.TextColumn("Table"),
                "table_type":         st.column_config.TextColumn("Type", width="small"),
                "data_source_format": st.column_config.TextColumn("Format", width="small"),
                "table_owner":        st.column_config.TextColumn("Owner"),
                "created":            st.column_config.DatetimeColumn("Created (UTC)"),
                "last_altered":       st.column_config.DatetimeColumn("Last altered (UTC)"),
                "comment":            st.column_config.TextColumn("Comment"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(meta_result.sql or "", language="sql")

    # -----------------------------------------------------------------
    # 2. Live UC tags — table + column level in a single view
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>UC tags: live classification at both levels</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Union of <code>system.information_schema.table_tags</code> and "
        "<code>column_tags</code> for the demo catalog. <strong>TABLE</strong> rows classify "
        "whole tables (domain, retention, regulatory, PII level, SLA). <strong>COLUMN</strong> "
        "rows classify individual columns (PII severity, sensitivity, attached masking policy), "
        "which is what ABAC and dynamic masks read from.</p>",
        unsafe_allow_html=True,
    )
    tags_result = queries.get_table_tags_live()
    preview_banner(tags_result, "information_schema.{table_tags, column_tags}")
    if not tags_result.df.empty:
        st.dataframe(
            tags_result.df,
            use_container_width=True,
            height=420,
            column_config={
                "level":       st.column_config.TextColumn("Level", width="small"),
                "table_name":  st.column_config.TextColumn("Table"),
                "column_name": st.column_config.TextColumn("Column"),
                "tag_name":    st.column_config.TextColumn("Tag"),
                "tag_value":   st.column_config.TextColumn("Value"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(tags_result.sql or "", language="sql")

    with st.expander("Tagging DDL (how these tags were set)"):
        st.code(
            """-- Classify the investor table at the table + column level
ALTER TABLE juniper_square_demo_catalog.pipeline.silver_investors
  SET TAGS ('pii_level' = 'high', 'domain' = 'finance', 'owner_team' = 'platform');

ALTER TABLE juniper_square_demo_catalog.pipeline.silver_investors
  ALTER COLUMN investor_name
  SET TAGS ('pii_level' = 'high', 'masking_policy' = 'full_name_mask');

ALTER TABLE juniper_square_demo_catalog.pipeline.silver_investors
  ALTER COLUMN city SET TAGS ('pii_level' = 'medium');

-- Regulatory retention on the GL fact table
ALTER TABLE juniper_square_demo_catalog.pipeline.silver_gl_transactions
  SET TAGS ('retention' = '7_years', 'regulatory' = 'sox', 'domain' = 'finance');

ALTER TABLE juniper_square_demo_catalog.pipeline.silver_gl_transactions
  ALTER COLUMN amount
  SET TAGS ('sensitivity' = 'internal', 'classification' = 'confidential');

-- Gold-tier SLA classification
ALTER TABLE juniper_square_demo_catalog.pipeline.gold_gl_monthly_summary
  SET TAGS ('domain' = 'finance', 'consumer_tier' = 'gold', 'sla_minutes' = '60');""",
            language="sql",
        )

    # -----------------------------------------------------------------
    # 3. DESCRIBE HISTORY — Delta chain-of-custody
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Chain of custody: DESCRIBE HISTORY</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The Delta transaction log is an append-only record of every "
        "operation on a table: who, when, what, and the row-level metrics. Below is the live "
        "history for <code>benchmark_summary</code>: the CREATE TABLE event, followed by each "
        "WRITE that appended benchmark results. Every managed Delta table has this same log, "
        "queryable with SQL, with no separate audit pipeline to stand up.</p>",
        unsafe_allow_html=True,
    )
    history_result = queries.get_table_history("benchmark_summary")
    preview_banner(history_result, "DESCRIBE HISTORY benchmark_summary")
    if not history_result.df.empty:
        st.dataframe(
            history_result.df,
            use_container_width=True,
            height=320,
            column_config={
                "version":             st.column_config.NumberColumn("Version", width="small"),
                "timestamp":           st.column_config.DatetimeColumn("Timestamp"),
                "userName":            st.column_config.TextColumn("User"),
                "operation":           st.column_config.TextColumn("Operation"),
                "operationParameters": st.column_config.TextColumn("Parameters"),
                "operationMetrics":    st.column_config.TextColumn("Metrics"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(history_result.sql or "", language="sql")

    # -----------------------------------------------------------------
    # 4. Beyond Databricks — external lineage / BYOL
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown(
        "<h2>Beyond Databricks: tracking sources outside the four walls</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p class='muted'>Tags, transaction logs, and DESCRIBE HISTORY cover provenance "
        "<em>inside</em> Databricks. The other half of the question, \"where did this "
        "data come from before it landed?\", is handled by Unity Catalog's "
        "<strong>Bring Your Own Lineage (BYOL)</strong> APIs, in Public Preview since "
        "June 2025.</p>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "**The pattern:** register an upstream system (Salesforce, a Postgres source, a "
        "Fivetran connector, an SFTP drop) as a UC external object via the "
        "**External Metadata API**, then wire a lineage edge from that object to the Delta "
        "table it feeds via the **External Lineage API**. The external node renders in the "
        "Catalog Explorer lineage graph alongside in-Databricks nodes: same UI, same "
        "audit trail, same column-level granularity."
    )

    st.markdown("**Sample registration: Fivetran connector to bronze table**")
    st.code("""# 1. Register the Fivetran connector as an external metadata object
POST /api/2.1/unity-catalog/external-metadata
{
  "name": "fivetran_salesforce_connector",
  "system_type": "FIVETRAN",
  "entity_type": "CONNECTOR",
  "url": "https://fivetran.com/dashboard/connectors/salesforce_prod",
  "description": "Production Salesforce -> Databricks ingestion",
  "custom_properties": {
    "connector_id": "salesforce_prod",
    "schedule": "every_15min",
    "owner_team": "data_platform",
    "last_sync_started": "2026-04-28T13:15:00Z"
  }
}

# 2. Wire the lineage edge to the Delta table it feeds
POST /api/2.1/unity-catalog/external-lineage
{
  "source": {
    "external_metadata": {"name": "fivetran_salesforce_connector"}
  },
  "target": {
    "table": {"name": "juniper_square_demo_catalog.bronze.salesforce_accounts"}
  }
}""", language="bash")

    st.markdown(
        "<div class='db-callout'>"
        "<strong>Honest read:</strong> there's no turnkey Fivetran provenance push today. "
        "UC's edge is <strong>column-level support</strong> and tighter integration with "
        "the rest of UC governance (tags, system tables, Genie context). The gap for the "
        "Fivetran-source case is a custom shim that polls Fivetran's metadata API and calls "
        "UC's External Lineage API per sync, a few hundred lines, not a platform "
        "rebuild."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<p class='muted'>Doc references: "
        "<a href='https://docs.databricks.com/aws/en/data-governance/unity-catalog/external-lineage' target='_blank'>BYOL: bring your own data lineage</a> &middot; "
        "<a href='https://docs.databricks.com/api/workspace/externallineage' target='_blank'>External Lineage API</a> &middot; "
        "<a href='https://docs.databricks.com/aws/en/release-notes/product/2025/june' target='_blank'>June 2025 release notes (Public Preview)</a></p>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Page: Auditability
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600, show_spinner="Loading audit log…")
def _cached_audit_log(hours: int) -> queries.QueryResult:
    return queries.get_audit_log(hours=hours)


def page_audit() -> None:
    page_header("Auditability", pillar_key="audit")

    render_demo_scope(
        demonstrated=[
            "Live query against system.access.audit scoped to this demo's activity",
            "Every benchmark query we just ran is visible in the audit stream",
            "Unique-user + service + event counts, all in SQL",
        ],
        also_supported=[
            ("System tables: access, billing, compute, lakeflow, query",
             "https://docs.databricks.com/en/admin/system-tables/index.html"),
            ("AI/BI dashboards on audit logs for compliance reporting",
             "https://docs.databricks.com/en/dashboards/index.html"),
            ("Databricks Alerts on anomalous access patterns",
             "https://docs.databricks.com/en/sql/user/alerts/index.html"),
            ("Delta transaction log (row-level change history)",
             "https://docs.databricks.com/en/delta/history.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    result = _cached_audit_log(hours=24)
    preview_banner(result, "system.access.audit (last 24h)")
    if st.button("Refresh", key="audit_refresh"):
        _cached_audit_log.clear()
        st.rerun()

    if not result.df.empty:
        c1, c2, c3 = st.columns(3, gap="medium")
        c1.metric("Events", f"{len(result.df):,}")
        c2.metric("Unique users", f"{result.df.get('user_email', pd.Series()).nunique()}")
        c3.metric("Services touched", f"{result.df.get('service_name', pd.Series()).nunique()}")

        st.dataframe(result.df, use_container_width=True, height=400)
    else:
        st.info("Audit log populates once the workspace has activity.")

    with st.expander("Show underlying SQL"):
        st.code(result.sql or "", language="sql")

    # -----------------------------------------------------------------
    # Self-service auditor queries — moves the page from "Scale" to
    # "Mature" tier on the scorecard. These are copy-paste SQL templates
    # an internal auditor can run without Databricks knowledge.
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Self-service auditor queries</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Canned SQL your security and compliance team can run directly against "
        "<code>system.access.audit</code>. No Databricks platform expertise required. Each "
        "query returns a flat result your auditor can export for SOX, GDPR, or HIPAA evidence "
        "requests.</p>",
        unsafe_allow_html=True,
    )

    with st.expander("Who accessed arena X in the last 90 days?", expanded=False):
        st.code(
            """-- All queries that referenced a specific tenant (arena) in the last 90 days.
-- Filter by user, service, or action as needed.
SELECT
    event_time,
    user_identity.email AS user_email,
    service_name,
    action_name,
    source_ip_address,
    response.status_code AS status_code
FROM system.access.audit
WHERE event_time >= current_timestamp() - INTERVAL 90 DAYS
  AND CAST(request_params AS STRING) LIKE '%arena_id=\\'ARN-00042\\'%'
ORDER BY event_time DESC
""",
            language="sql",
        )

    with st.expander("All grant / revoke changes on silver_* tables", expanded=False):
        st.code(
            """-- Every permission change on any silver_* table this quarter. Maps to
-- "who gave access to what, when", a standard SOX evidence request.
SELECT
    event_time,
    user_identity.email AS granted_by,
    action_name,
    request_params,
    response.status_code AS status_code
FROM system.access.audit
WHERE event_time >= current_timestamp() - INTERVAL 90 DAYS
  AND service_name = 'unityCatalog'
  AND action_name IN ('grantPermission', 'revokePermission', 'updatePermissions')
  AND CAST(request_params AS STRING) LIKE '%silver_%'
ORDER BY event_time DESC
""",
            language="sql",
        )

    with st.expander("All schema changes on gold_* tables this quarter", expanded=False):
        st.code(
            """-- DDL on gold_* tables over the last 90 days: adds, drops, schema evolution.
-- Pair with Delta's DESCRIBE HISTORY for row-level change tracking.
SELECT
    event_time,
    user_identity.email AS changed_by,
    action_name,
    request_params
FROM system.access.audit
WHERE event_time >= current_timestamp() - INTERVAL 90 DAYS
  AND service_name = 'unityCatalog'
  AND action_name IN ('createTable', 'alterTable', 'dropTable', 'updateTable')
  AND CAST(request_params AS STRING) LIKE '%gold_%'
ORDER BY event_time DESC
""",
            language="sql",
        )

    with st.expander("Failed authentications in the last 7 days", expanded=False):
        st.code(
            """-- Failed login / token-auth events. Pairs with Databricks Alerts to page
-- the on-call when failure rates cross a threshold.
SELECT
    event_time,
    user_identity.email AS user_email,
    service_name,
    action_name,
    source_ip_address,
    response.status_code AS status_code,
    response.error_message AS error_message
FROM system.access.audit
WHERE event_time >= current_timestamp() - INTERVAL 7 DAYS
  AND response.status_code BETWEEN 400 AND 499
  AND action_name IN ('tokenLogin', 'login', 'samlLogin')
ORDER BY event_time DESC
""",
            language="sql",
        )

    st.caption(
        "Every query above is a single SQL statement, no joins across services, no custom "
        "APIs. Wrap any of them in a Databricks Alert to page on the failure mode you care "
        "about, or schedule via AI/BI Dashboards for a compliance report your auditor opens "
        "themselves."
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

ROUTER = {
    "overview": page_overview,
    "latency": page_latency,
    "cost": page_cost,
    "klo": page_klo,
    "lineage": page_lineage,
    "security": page_security,
    "integration": page_integration,
    "provenance": page_provenance,
    "audit": page_audit,
}

ROUTER.get(current_page_key, page_overview)()
