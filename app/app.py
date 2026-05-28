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
from lib.charts import (
    build_latency_cdf,
    build_latency_timeseries,
    build_q8_percentile_comparison,
    build_q8_latency_timeline,
    build_qps_timeseries,
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


# Custom Q8 tile renderer — gives full color control over value + subtext.
# Streamlit's st.metric is too opinionated (always-green delta with up arrow,
# can't recolor the value). For the Q8 result tiles we want red for the slow
# silver number, green for the fast medallion and the speedup, and neutral for
# scale facts — none of which st.metric supports natively.
_Q8_TILE_ACCENTS = {
    "red":     {"value": "#FF3621", "subtext": "#0B2026"},   # lava — slow, expected
    "green":   {"value": "#00A972", "subtext": "#00A972"},   # green — winner
    "neutral": {"value": "#0B2026", "subtext": "#4A5568"},   # primary / muted
}


def q8_tile(label: str, value: str, subtext: str, accent: str = "neutral") -> None:
    """Render a Q8 result tile with color-coded value + neutral subtext.

    accent ∈ {"red", "green", "neutral"}. No arrows, no streamlit metric chrome —
    just a clean color-cued tile matching the brand container styling.
    """
    a = _Q8_TILE_ACCENTS.get(accent, _Q8_TILE_ACCENTS["neutral"])
    st.markdown(
        f"""
        <div style="background:#fff; border:1px solid #E8E4DE; border-radius:8px;
                    padding:14px 16px; box-shadow:0 1px 2px rgba(11,32,38,0.04);
                    height:100%;">
          <div style="color:#4A5568; font-size:13px; font-weight:500;
                      margin-bottom:6px; font-feature-settings:'tnum';">{label}</div>
          <div style="color:{a['value']}; font-size:28px; font-weight:700;
                      letter-spacing:-0.01em; font-feature-settings:'tnum';
                      line-height:1.1;">{value}</div>
          <div style="color:{a['subtext']}; font-size:12px; margin-top:6px;
                      font-feature-settings:'tnum';">{subtext}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    # Keep URL in sync so refresh restores the active tab + URLs are shareable
    if st.query_params.get("page") != current_page_key:
        st.query_params["page"] = current_page_key


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

    # -----------------------------------------------------------------
    # Redline → Architectural delta. The 500-line/50-table monster is a
    # Redshift artifact. With medallion (silver+gold MV), the same business
    # answer comes back in milliseconds. Headline tile is the delta.
    # -----------------------------------------------------------------
    st.markdown("<h2>Architectural delta: worst-case query</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p style='font-size:1.05rem; color:#4A5568; margin-top:-8px; margin-bottom:16px;'>"
        "Juniper's worst-case query touches 50 tables and runs ~500 lines because Redshift "
        "forces business logic, permission filtering, and audit trail joins into every dashboard "
        "request. With a medallion architecture (silver + pre-aggregated gold), the same business "
        "answer becomes a 30-line SELECT against gold. We measure both shapes side-by-side."
        "</p>",
        unsafe_allow_html=True,
    )
    # Q8 headline data — populates from benchmark_summary once the redline run lands.
    q8_data = queries.get_q8_headline()
    q8_lookup = {}
    if not q8_data.preview_mode and not q8_data.df.empty:
        for _, row in q8_data.df.iterrows():
            q8_lookup[(row["query_name"], row["target"])] = row

    # Sentinel: p95 >= 7,200,000 ms (2 hours) means the query did not complete.
    SENTINEL_DNF_MS = 7_200_000

    def _fmt_p95(q_name):
        row = q8_lookup.get((q_name, "dbsql"))
        if row is None:
            return "pending"
        ms = row["p95_ms"]
        if ms is None:
            return "pending"
        if ms >= SENTINEL_DNF_MS:
            return "did not complete"
        if ms >= 1000:
            return f"{ms/1000:.1f} s"
        return f"{ms:.0f} ms"

    def _fmt_p99(q_name):
        """Return 'P99 X.X s' string suitable for tile subtext, or '' if no data."""
        row = q8_lookup.get((q_name, "dbsql"))
        if row is None or row.get("p99_ms") is None:
            return ""
        ms = row["p99_ms"]
        if ms >= SENTINEL_DNF_MS:
            return ""
        if ms >= 1000:
            return f"P99 {ms/1000:.1f} s"
        return f"P99 {ms:.0f} ms"

    def _speedup(shape_q, refactored_q):
        s = q8_lookup.get((shape_q, "dbsql"))
        r = q8_lookup.get((refactored_q, "dbsql"))
        if s is None or r is None or s["p95_ms"] is None or r["p95_ms"] is None or r["p95_ms"] == 0:
            return None
        if s["p95_ms"] >= SENTINEL_DNF_MS:
            return "DNF"  # signal "didn't finish" delta
        return s["p95_ms"] / r["p95_ms"]

    q8_speedup = _speedup("q8_shape", "q8_refactored")

    def _speedup_delta(speedup, line_count):
        if speedup is None:
            return f"{line_count}-line SELECT on gold MV"
        if speedup == "DNF":
            return f"shape didn't finish; medallion ran. {line_count}-line SELECT on gold"
        return f"{speedup:.0f}× faster, {line_count}-line SELECT on gold"

    redline_cols = st.columns(3, gap="medium")
    def _sub_with_p99(q_name, base_subtext):
        p99 = _fmt_p99(q_name)
        return f"{p99} · {base_subtext}" if p99 else base_subtext

    with redline_cols[0]:
        q8_tile(
            "Fund roll-up: silver shape · P95",
            _fmt_p95("q8_shape"),
            _sub_with_p99("q8_shape", "500-line, 50-table SELECT against silver — expected to be slow"),
            accent="neutral",
        )
    with redline_cols[1]:
        q8_tile(
            "Fund roll-up: medallion refactor · P95",
            _fmt_p95("q8_refactored"),
            _sub_with_p99("q8_refactored", _speedup_delta(q8_speedup, 25)),
            accent="green",
        )
    with redline_cols[2]:
        q8_tile(
            "Data scale tested",
            "1.22 TB",
            "30 B GL rows — matches Juniper's actual production scale",
            accent="neutral",
        )

    # Cost comparison callout — pairs the latency story with the dollars,
    # sitting directly under the 3 result tiles for an immediate so-what.
    st.markdown(
        "<div class='db-callout db-callout--success' style='margin-top:14px;'>"
        "<strong>Projected cost at 10× scale (1K → 10K customers)</strong>"
        "<div style='margin-top:8px;'>"
        "<strong style='font-size:1.8rem; color:#00A972;'>44% lower</strong> "
        "<span class='muted' style='font-size:13px;'>than current Redshift baseline · "
        "$409K/yr vs $737K/yr · ~$328K annual savings</span>"
        "</div>"
        "<p style='font-size:13px; margin-top:10px; margin-bottom:0;'>"
        "Modeled with Databricks SQL Pro Small + Serverless ETL pipelines post-medallion "
        "rebuild, against Juniper's stated Redshift footprint (3× ra3.4xlarge + "
        "2+2× ra3.large, 1yr RI, CS auto). "
        "<a href='?page=cost'>See Cost of doing business tab for full breakdown →</a>"
        "</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<div class='db-callout' style='margin-top:14px;'>"
        "<strong>The architectural difference</strong>"
        "<ul style='margin:8px 0 0 0; padding-left:20px; font-size:13px;'>"
        "<li><strong>Permission filtering applied once at silver.</strong> Arena scoping, "
        "RBAC, and multi-tenant filters become transformation logic, not every-query overhead. "
        "Collapses ~50 lines of repeated filter logic into zero.</li>"
        "<li><strong>Pre-aggregated business metrics on gold.</strong> PCAP roll-up, fund "
        "IRR/MOIC/TVPI/DPI, property attribution all computed by scheduled pipelines. "
        "Query becomes a lookup, not a computation.</li>"
        "<li><strong>Same audit trail, queryable separately.</strong> SOX events live in "
        "<code>fact_audit_event</code> with their own lineage. Not joined into every query.</li>"
        "<li><strong>Refresh cadence configurable.</strong> Daily, hourly, or as data lands "
        "via SDP. Matches PCAP quarterly reporting cycle natively.</li>"
        "</ul>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        "<h3 style='margin-top:18px; margin-bottom:8px;'>What this means for Juniper</h3>",
        unsafe_allow_html=True,
    )
    hyp_cols = st.columns(3, gap="medium")
    hyp_cols[0].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>Where would you spend the headroom you just got back?</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Even at production scale the "
        "refactored gold query returns in milliseconds, so the team is no longer "
        "rationing warehouse capacity around a single worst-case query. "
        "More room for ad-hoc Insights AI, internal analytics, and customer-facing "
        "dashboards.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    hyp_cols[1].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>Engineering effort scales differently</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>Today's pattern: every team writing "
        "500-line queries that drift in business logic. Medallion: business logic lives once "
        "in SDP, every consumer reads consistent gold.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    hyp_cols[2].markdown(
        "<div class='db-callout' style='min-height:148px;'>"
        "<strong>Worst-case still measured</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>We also run the 50-table shape on the "
        "same data. Survival numbers in the expander below. If the customer keeps the old "
        "query pattern unchanged, Databricks still handles it.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p class='muted' style='margin-top:14px; font-size:13px;'>"
        "<strong>Synth note:</strong> The fund performance roll-up here is our "
        "reconstruction of the 50-table query shape Juniper Square described on the "
        "5/13 call. Domain-validated against PCAP / waterfall / IRR conventions in "
        "real-estate fund accounting. Numbers above pull live from "
        "<code>benchmark_summary</code> in this workspace."
        "</p>",
        unsafe_allow_html=True,
    )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    # BI Query Results Summary stats
    st.markdown("<h2 style='margin-top:18px;'>BI Query Results Summary</h2>", unsafe_allow_html=True)

    # Pull the latest sustained 5 QPS run so the dashboard-mix tile shows live
    # P95 + P99 rather than the old hardcoded "~1.0 s" placeholder.
    _sustained = queries.get_sustained_runs().df
    _dash5_p95 = None
    _dash5_p99 = None
    if not _sustained.empty:
        _dash5 = (
            _sustained[(_sustained["target"] == "dbsql") & (_sustained["target_rate_qps"] == 5.0)]
            .sort_values("started_at", ascending=False)
        )
        if not _dash5.empty:
            _dash5_p95 = _dash5.iloc[0].get("p95_median_ms")
            _dash5_p99 = _dash5.iloc[0].get("p99_median_ms")
    _dash5_value = _fmt_latency(_dash5_p95) if _dash5_p95 else "P95 ~1.0 s"
    _dash5_sub = (
        f"P99 {_fmt_latency(_dash5_p99)} · DBSQL Pro Medium 1→8 · holds 5 s SLO"
        if _dash5_p99 else "Databricks SQL Pro Medium 1→8 · holds 5 s SLO"
    )

    stat_cols = st.columns(4, gap="medium")
    with stat_cols[0]:
        q8_tile("GL transactions", "30 B", "1.22 TB silver fact table", accent="neutral")
    with stat_cols[1]:
        q8_tile(
            "Fund roll-up: silver shape · P95",
            _fmt_p95("q8_shape"),
            _sub_with_p99("q8_shape", "500-line, 50-table SELECT against silver — expected to be slow"),
            accent="neutral",
        )
    with stat_cols[2]:
        q8_tile(
            "Fund roll-up: medallion refactor · P95",
            _fmt_p95("q8_refactored"),
            _sub_with_p99("q8_refactored", _speedup_delta(q8_speedup, 25)),
            accent="green",
        )
    with stat_cols[3]:
        q8_tile(
            "Dashboard mix @ peak (5 QPS) · P95",
            _dash5_value,
            _dash5_sub,
            accent="green",
        )

    st.markdown(
        "<p class='muted' style='margin-top:16px;'>"
        "Dashboard SLOs of P50 ≤ 4 s / P95 ≤ 5 s / P99 ≤ 7 s: Databricks SQL Pro Medium "
        "1→8 clears them on the dashboard mix at sustained 5 and 10 QPS Poisson arrivals. "
        "The architectural delta on the fund roll-up is the headline: medallion makes "
        "the 500-line query unnecessary, not just faster. See the Data Latency tab for "
        "the time-series and IWM evidence."
        "</p>",
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------
    # Sustained-rate scenarios — DBSQL Pro dashboard mix at peak + 2× peak
    # -----------------------------------------------------------------
    sustained_runs_result = queries.get_sustained_runs()
    sustained_runs_df = sustained_runs_result.df
    if not sustained_runs_df.empty:
        st.markdown("<h3 style='margin-top:18px;'>Dashboard mix at sustained load</h3>",
                    unsafe_allow_html=True)
        st.markdown(
            "<p class='muted'>Poisson arrivals at target QPS, 10 min measurement window after "
            "90 s warmup. Coordinated-omission fixed. Median P95 across the dashboard mix. "
            "Drill into the Data Latency tab for time-series and CDF.</p>",
            unsafe_allow_html=True,
        )
        latest_per_rate = (
            sustained_runs_df[sustained_runs_df["target"] == "dbsql"]
            .sort_values("started_at", ascending=False)
            .drop_duplicates(subset=["target_rate_qps"], keep="first")
        )
        main_rates = [5.0, 10.0]
        sus_cols = st.columns(len(main_rates), gap="medium")
        for col, rate in zip(sus_cols, main_rates):
            sub = latest_per_rate[latest_per_rate["target_rate_qps"] == rate]
            label = "Peak (5 QPS, your stated peak)" if rate == 5.0 else "2× headroom (10 QPS)"
            if sub.empty:
                col.metric(label, "no run yet", delta="run sustained scenario to populate")
                continue
            row = sub.iloc[0]
            samples = int(row.get("total_samples") or 0)
            p99 = row.get("p99_median_ms")
            p99_str = f"P99 {_fmt_latency(p99)} · " if p99 else ""
            col.metric(
                label,
                _fmt_latency(row.get("p95_median_ms")),
                delta=f"{p99_str}DBSQL Pro · {samples:,} samples",
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

    if workspace_host_ov:
        base_ov = f"https://{workspace_host_ov}"
        assets = [
            ("ETL pipeline: the medallion DAG",
             f"{base_ov}/pipelines/{pipeline_id_ov}",
             "Bronze, silver (liquid-clustered), gold. Event log, run history, lineage inline."),
            ("Unity Catalog: browse the demo catalog",
             f"{base_ov}/explore/data/{catalog_ov}",
             "Tables, tags, column comments, permissions, lineage tabs."),
            ("Lineage on gold_gl_monthly_summary",
             f"{base_ov}/explore/data/{catalog_ov}/pipeline/gold_gl_monthly_summary?activeTab=lineage",
             "End-to-end lineage from raw Parquet landing through bronze, silver, gold."),
            ("DBSQL warehouse: Serverless Medium Pro, autoscale 1→8",
             f"{base_ov}/sql/warehouses/{warehouse_id_ov}",
             "Start/stop, sizing, auto-stop, monitoring. The warehouse that ran the benchmark."),
            ("DBSQL query history",
             f"{base_ov}/sql/history?o=&warehouse_id={warehouse_id_ov}",
             "Every benchmark query we measured, with duration, rows read, query profile."),
            ("Benchmark harness notebook",
             f"{base_ov}/editor/notebooks/2835102681662565",
             "The Python harness that produced the latency numbers. Ran locally against this workspace."),
            ("Orchestration job (ETL + 4 parallel syncs)",
             f"{base_ov}/jobs/658584579307262",
             "5-task DAG: medallion pipeline, then 4 downstream sync pipelines in parallel. All serverless."),
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

    # =====================================================================
    # SECTION 1: Fund roll-up architectural delta — leads the page
    # =====================================================================
    st.markdown(
        "<p style='font-size:1.05rem; color:#4A5568; margin-top:-8px; margin-bottom:18px;'>"
        "At Juniper Square's actual production scale (<strong>1.22 TB silver, 30 B GL "
        "rows</strong>), the 500-line, 50-table monster query lands at <strong>P95 21.2 s</strong> "
        "against silver. Refactored as a 25-line SELECT against a gold materialized view, the "
        "<strong>same business answer lands at P95 977 ms</strong>. The medallion architecture "
        "doesn't make Redshift's worst query faster; it makes the worst query unnecessary."
        "</p>",
        unsafe_allow_html=True,
    )

    q8_data = queries.get_q8_headline()
    q8_lookup = {}
    if not q8_data.preview_mode and not q8_data.df.empty:
        for _, row in q8_data.df.iterrows():
            q8_lookup[(row["query_name"], row["target"])] = row
    SENTINEL_DNF_MS = 7_200_000

    def _fmt_q8_p95(q_name):
        row = q8_lookup.get((q_name, "dbsql"))
        if row is None or row["p95_ms"] is None:
            return "pending"
        ms = row["p95_ms"]
        if ms >= SENTINEL_DNF_MS:
            return "did not complete"
        if ms >= 1000:
            return f"{ms/1000:.1f} s"
        return f"{ms:.0f} ms"

    def _q8_speedup():
        s = q8_lookup.get(("q8_shape", "dbsql"))
        r = q8_lookup.get(("q8_refactored", "dbsql"))
        if s is None or r is None or s["p95_ms"] is None or r["p95_ms"] is None or r["p95_ms"] == 0:
            return None
        if s["p95_ms"] >= SENTINEL_DNF_MS:
            return "DNF"
        return s["p95_ms"] / r["p95_ms"]

    def _fmt_q8_p99(q_name):
        row = q8_lookup.get((q_name, "dbsql"))
        if row is None or row.get("p99_ms") is None:
            return ""
        ms = row["p99_ms"]
        if ms >= SENTINEL_DNF_MS:
            return ""
        if ms >= 1000:
            return f"P99 {ms/1000:.1f} s"
        return f"P99 {ms:.0f} ms"

    def _q8_sub_with_p99(q_name, base_subtext):
        p99 = _fmt_q8_p99(q_name)
        return f"{p99} · {base_subtext}" if p99 else base_subtext

    speedup = _q8_speedup()
    q8_cols = st.columns(3, gap="medium")
    with q8_cols[0]:
        q8_tile(
            "Fund roll-up: silver shape · P95",
            _fmt_q8_p95("q8_shape"),
            _q8_sub_with_p99("q8_shape", "500-line, 50-table SELECT against silver — expected to be slow"),
            accent="neutral",
        )
    with q8_cols[1]:
        q8_tile(
            "Fund roll-up: medallion refactor · P95",
            _fmt_q8_p95("q8_refactored"),
            _q8_sub_with_p99("q8_refactored", "25-line SELECT against gold MV"),
            accent="green",
        )
    speedup_label = "pending" if speedup is None else (
        "DNF baseline" if speedup == "DNF" else f"{speedup:.0f}×"
    )
    with q8_cols[2]:
        q8_tile(
            "Speedup at production scale",
            speedup_label,
            "1.22 TB silver / 30 B GL rows",
            accent="green",
        )

    st.markdown(
        "<div class='db-callout' style='margin-top:14px;'>"
        "<strong>The architectural difference</strong>"
        "<ul style='margin:8px 0 0 0; padding-left:20px; font-size:13px;'>"
        "<li><strong>Permission filtering applied once at silver.</strong> Arena scoping, "
        "RBAC, and multi-tenant filters become transformation logic, not every-query "
        "overhead. Collapses ~50 lines of repeated filter logic into zero.</li>"
        "<li><strong>Pre-aggregated business metrics on gold.</strong> PCAP roll-up, fund "
        "IRR/MOIC/TVPI/DPI, property attribution all computed by scheduled pipelines. The query "
        "becomes a lookup, not a computation.</li>"
        "<li><strong>Same audit trail, queryable separately.</strong> SOX events live in "
        "<code>fact_audit_event</code> with their own lineage. Not joined into every query.</li>"
        "<li><strong>Refresh cadence configurable.</strong> Daily, hourly, or as data lands "
        "via scheduled pipelines. Matches PCAP quarterly reporting cycle natively.</li>"
        "</ul>"
        "</div>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 1b: Per-sample latency strip plot from benchmark_raw.
    #
    # Three sustained runs feed this chart:
    #   - q8_shape       @ 1 QPS, 30 min   (silver shape, ~75 measured samples)
    #   - q8_refactored  @ 5 QPS, 10 min   (medallion at BI peak rate)
    #   - q8_refactored  @ 10 QPS, 10 min  (medallion at 2× headroom)
    #
    # Falls back to the percentile-bar view if benchmark_raw is empty for q8.
    # =====================================================================
    st.markdown("<h3 style='margin-top:18px;'>Per-sample latency distribution</h3>",
                unsafe_allow_html=True)

    q8_samples_result = queries.get_q8_samples()
    if q8_samples_result.preview_mode or q8_samples_result.df.empty:
        # Fallback: percentile bars from benchmark_summary (legacy path).
        q8_filter_label = st.radio(
            "Show",
            ["Both", "Silver shape only", "Medallion refactor only"],
            index=0, horizontal=True, key="q8_shape_filter",
            label_visibility="collapsed",
        )
        q8_filter_map = {
            "Both": "both",
            "Silver shape only": "q8_shape",
            "Medallion refactor only": "q8_refactored",
        }
        q8_perc_result = queries.get_q8_headline()
        preview_banner(q8_perc_result, "from benchmark_summary WHERE query_name LIKE 'q8_%'")
        st.plotly_chart(
            build_q8_percentile_comparison(
                q8_perc_result.df,
                show=q8_filter_map[q8_filter_label],
            ),
            use_container_width=True,
        )
        st.caption(
            "P50/P95/P99 from the most-recent sustained run per variant. "
            "benchmark_raw didn't have per-sample rows for q8 yet — re-running "
            "the three Q8 scenarios will populate the strip plot view."
        )
    else:
        q8_filter_label = st.radio(
            "Show",
            ["Both series", "Silver shape only", "Medallion refactor only"],
            index=0, horizontal=True, key="q8_strip_filter",
            label_visibility="collapsed",
        )
        q8_filter_map = {
            "Both series": "all",
            "Silver shape only": "silver",
            "Medallion refactor only": "medallion",
        }
        preview_banner(
            q8_samples_result,
            "from benchmark_raw WHERE query_name IN ('q8_shape','q8_refactored')",
        )
        st.plotly_chart(
            build_q8_latency_timeline(
                q8_samples_result.df,
                show=q8_filter_map[q8_filter_label],
            ),
            use_container_width=True,
        )
        st.caption(
            "Each marker is one measured query (warmup excluded). Silver shape "
            "@ 1 QPS — the realistic ceiling since the query saturates the "
            "warehouse above that rate. Medallion refactor @ 10 QPS (2× BI peak) "
            "demonstrating headroom. Log-Y axis handles the ~22× spread between "
            "the two variants."
        )

    # =====================================================================
    # SECTION 2: Dashboard mix at sustained load (DBSQL Pro)
    # =====================================================================
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    with st.expander("Dashboard mix at peak + 2× headroom (sustained-load detail)"):
        st.markdown("<h2>Dashboard mix holds SLO at peak and 2× peak</h2>", unsafe_allow_html=True)
        st.markdown(
            "<p style='color:#4A5568; margin-top:-8px; margin-bottom:14px;'>"
            "Six dashboard-shaped queries (fund performance, GL monthly rollup, property "
            "financials, top properties, multi-month P&amp;L, investor commitments) run at "
            "sustained Poisson arrivals against DBSQL Pro Medium, autoscale 1→8. Peak "
            "(5 QPS) is Juniper's stated load; 2× headroom (10 QPS) covers the June PDF "
            "Reporting rollout. Both hold within the 5 s P95 dashboard SLO, compared to "
            "Redshift's current <strong>10–45 s</strong> on the same shapes."
            "</p>",
            unsafe_allow_html=True,
        )

        sustained_runs_result = queries.get_sustained_runs()
        preview_banner(sustained_runs_result, "from benchmark_runs WHERE mode='sustained'")
        sustained_runs_df = sustained_runs_result.df

        # Only DBSQL rows at the two main-flow rates surface here.
        MAIN_RATES = [5.0, 10.0]
        if sustained_runs_df.empty:
            st.info(
                "No sustained-rate runs in Delta yet. Run the harness to populate the headline tiles "
                "and time-series charts."
            )
        else:
            latest_per_rate_target = (
                sustained_runs_df
                .sort_values("started_at", ascending=False)
                .drop_duplicates(subset=["target_rate_qps", "target"], keep="first")
            )
            tile_cols = st.columns(len(MAIN_RATES), gap="medium")
            for col, rate in zip(tile_cols, MAIN_RATES):
                row_match = latest_per_rate_target[
                    (latest_per_rate_target["target_rate_qps"] == rate) &
                    (latest_per_rate_target["target"] == "dbsql")
                ]
                label = "Peak (5 QPS, your stated peak)" if rate == 5.0 else "2× headroom (10 QPS)"
                if row_match.empty:
                    col.metric(label, "no run yet", delta="DBSQL Pro · up to 8 clusters")
                    continue
                r = row_match.iloc[0]
                samples = int(r.get("total_samples") or 0)
                p99 = r.get("p99_median_ms")
                p99_str = f"P99 {_fmt_latency(p99)} · " if p99 else ""
                col.metric(
                    label,
                    _fmt_latency(r.get("p95_median_ms")),
                    delta=f"{p99_str}DBSQL Pro · {samples:,} samples · median across dashboard mix",
                )

        # =====================================================================
        # SECTION 3: Latency over time (5 QPS default)
        # =====================================================================
        selected_run_id = None
        selected_rate = None
        selected_label = None
        if not sustained_runs_df.empty:
            st.markdown("<h3 style='margin-top:18px;'>Latency over time</h3>", unsafe_allow_html=True)

            # Only main-flow rates in the picker.
            latest_per_rate = (
                sustained_runs_df[sustained_runs_df["target_rate_qps"].isin(MAIN_RATES)]
                .sort_values("started_at", ascending=False)
                .drop_duplicates(subset=["target_rate_qps"], keep="first")
                .sort_values("target_rate_qps")
            )
            SCENARIO_LABELS = {
                5.0: "Peak (5 QPS)",
                10.0: "2× headroom (10 QPS)",
            }
            available_rates = [r for r in latest_per_rate["target_rate_qps"].tolist() if r in SCENARIO_LABELS]
            label_to_rate = {SCENARIO_LABELS[r]: r for r in available_rates}
            options = list(label_to_rate.keys())

            if options:
                picker_col, _ = st.columns([2, 3], gap="small")
                with picker_col:
                    selected_label = st.selectbox(
                        "Scenario", options=options, index=0, key="sustained_scenario_picker",
                    )
                selected_rate = label_to_rate[selected_label]
                selected_row = latest_per_rate[latest_per_rate["target_rate_qps"] == selected_rate].iloc[0]
                selected_run_id = selected_row["run_id"]

                buckets_result = queries.get_timeseries_buckets(selected_run_id)
                buckets_df = buckets_result.df
                # DBSQL-only view
                dbsql_buckets = (
                    buckets_df[buckets_df["target"] == "dbsql"]
                    if not buckets_df.empty else buckets_df
                )
                st.plotly_chart(
                    build_latency_timeseries(dbsql_buckets),
                    use_container_width=True,
                )
                st.caption(
                    f"Scenario: **{selected_label}** · run_id `{selected_run_id}`. Measurement "
                    f"window only (post-90 s warmup)."
                )

                chart_cols = st.columns(2, gap="medium")
                with chart_cols[0]:
                    st.plotly_chart(
                        build_qps_timeseries(dbsql_buckets, target_rate=float(selected_rate or 0)),
                        use_container_width=True,
                    )
                    st.caption(
                        "Coordinated-omission canary: achieved-QPS lagging the target line "
                        "= warehouse throttling."
                    )
                with chart_cols[1]:
                    cdf_result = queries.get_latency_cdf(selected_run_id)
                    dbsql_cdf = (
                        cdf_result.df[cdf_result.df["target"] == "dbsql"]
                        if not cdf_result.df.empty else cdf_result.df
                    )
                    st.plotly_chart(
                        build_latency_cdf(dbsql_cdf),
                        use_container_width=True,
                    )
                    st.caption(
                        "Cumulative distribution of post-warmup total latency. "
                        "Vertical guides at 4 / 5 / 7 s SLO."
                    )

    # =====================================================================
    # Compute + IWM evidence — visible by default since the warehouse story
    # is what explains the headline latency numbers above.
    # =====================================================================
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Compute that ran this benchmark + IWM in action</h2>", unsafe_allow_html=True)
    st.markdown(
        "<div class='db-callout'>"
        "<strong>DBSQL warehouse</strong><br>"
        "Serverless SQL Pro, <strong>Medium, autoscale 1→8 clusters</strong><br>"
        "24 DBU / hour per cluster · auto-stop 60 min · Photon enabled"
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
        "IWM's ML cost model predicted load and provisioned the warehouse from 1 to 2 to 6 "
        "clusters within ~5 minutes during the high-load phase. New-cluster start time is "
        "documented at 2-6 seconds (DBSQL Serverless). Redshift Concurrency Scaling is "
        "reactive (waits for queue-depth threshold) and provisions in minutes per AWS docs."
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
    # EXPANDER 2: Methodology
    # =====================================================================
    with st.expander("Methodology: how we tested"):
        st.markdown(
            "<div class='db-callout'>"
            "<strong>Sustained-rate measurement methodology</strong>"
            "<ul style='margin:8px 0 0 18px; padding:0; font-size:13px;'>"
            "<li><strong>Arrival pattern:</strong> Poisson via "
            "<code>random.expovariate(rate)</code> cumulative on an independent submitter "
            "timeline (fixes coordinated omission per wrk2 / HdrHistogram pattern). If a "
            "submission slips because the warehouse stalls, queue time is captured "
            "separately from service time.</li>"
            "<li><strong>Warmup:</strong> 90 s at target rate, results written to Delta with "
            "<code>is_warmup=true</code> and excluded from headline statistics.</li>"
            "<li><strong>Dashboard mix scenarios:</strong> Peak (5 QPS) · 2× headroom "
            "(10 QPS). 600 s measurement window after warmup. Weighted mix across six "
            "dashboard-shaped queries against DBSQL Pro.</li>"
            "<li><strong>Fund roll-up architectural delta:</strong> silver shape "
            "(500-line silver scan) vs medallion refactor (25-line gold SELECT). Silver "
            "shape run at 1 QPS sustained (n=180 measurement samples after a 5-min warmup "
            "to absorb cluster cold-start); medallion refactor run at 10 QPS sustained "
            "(n=248 samples). Sample sizes shown inline on each Q8 chart trace.</li>"
            "<li><strong>Warehouse:</strong> Medium-Pro autoscale 1-8. Photon enabled. "
            "<code>auto_stop_mins</code> 60.</li>"
            "<li><strong>Data scale:</strong> 1.22 TB silver, 30 B GL rows, 10 K arenas. "
            "Wider GL schema with memo_text / currency / approval / counterparty / cost_center.</li>"
            "</ul>"
            "</div>",
            unsafe_allow_html=True,
        )

    # =====================================================================
    # EXPANDER 3: Fund roll-up SQL — shape vs medallion refactor side-by-side
    # =====================================================================
    with st.expander("Fund roll-up SQL: the 500-line monster vs the 25-line medallion refactor"):
        workspace_host_lat = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
        warehouse_id_q = os.environ.get("DATABRICKS_WAREHOUSE_ID", "aae8e7baf626bd0d")
        sql_dir = Path(__file__).resolve().parent / "sql"
        shape_path = sql_dir / "Q8_shape.sql"
        refactored_path = sql_dir / "Q8_refactored.sql"
        shape_sql = shape_path.read_text() if shape_path.exists() else "-- silver-shape SQL not found"
        refactored_sql = (
            refactored_path.read_text() if refactored_path.exists()
            else "-- medallion-refactor SQL not found"
        )
        shape_lines = shape_sql.count("\n")
        refactored_lines = refactored_sql.count("\n")

        st.markdown(
            "<p class='muted' style='font-size:13px;'>"
            "Both queries return the same business answer: fund performance roll-up with "
            "IRR / MOIC / TVPI / DPI, property attribution, and peer-vintage benchmarks for "
            "a single fund. The silver shape replicates the 500-line / 50-table pattern "
            "Juniper Square described on the 5/13 call. The medallion refactor reads from "
            "<code>gold_fund_attribution_period</code>, a materialized view maintained by SDP."
            "</p>",
            unsafe_allow_html=True,
        )

        meta_cols = st.columns(2, gap="medium")
        meta_cols[0].metric("Silver shape", f"{shape_lines} lines",
                            delta="50-table touch, 15 CTEs")
        meta_cols[1].metric("Medallion refactor", f"{refactored_lines} lines",
                            delta="1 fact + 6 dim joins, no CTEs")

        if workspace_host_lat:
            editor_url = f"https://{workspace_host_lat}/sql/editor/?o=&warehouse_id={warehouse_id_q}"
            history_url = f"https://{workspace_host_lat}/sql/history?o=&warehouse_id={warehouse_id_q}"
            st.markdown(
                f"<p style='margin-top:10px;'>"
                f"<a href='{editor_url}' target='_blank'>Open DBSQL editor →</a> &nbsp;&nbsp; "
                f"<a href='{history_url}' target='_blank'>View query history →</a></p>",
                unsafe_allow_html=True,
            )

        sql_cols = st.columns(2, gap="medium")
        with sql_cols[0]:
            st.markdown(f"**Silver shape — {shape_lines} lines against silver**")
            st.code(shape_sql, language="sql")
        with sql_cols[1]:
            st.markdown(f"**Medallion refactor — {refactored_lines} lines against gold**")
            st.code(refactored_sql, language="sql")


# ---------------------------------------------------------------------------
# Page: Cost
# ---------------------------------------------------------------------------

def page_cost() -> None:
    page_header("Cost of doing business", pillar_key="cost")

    render_demo_scope(
        demonstrated=[
            "Redshift baseline calculator: Juniper's actual cluster sizing (3× ra3.4xlarge + 2+2× ra3.large, us-west-2)",
            "Databricks calculator: Serverless SQL Pro warehouse + Serverless ETL pipelines at matched workload",
            "10× growth projection: June PDF Reporting (5× data) + 1K→10K customer scale",
            "Tag-based cost attribution via system.billing.usage",
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

    st.markdown(
        "<p style='color:#4A5568;'>Two cost models for the same workload. Redshift is "
        "<strong>capacity-priced</strong>: you pay for the cluster whether it's busy or idle. "
        "Databricks is <strong>usage-priced</strong>: pay only when queries run, auto-stop when "
        "idle. The growth projection below uses both models against the same Juniper "
        "growth path (PDF Reporting + 1K→10K customer scale).</p>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 1: Today's footprint — Redshift baseline vs Databricks
    # =====================================================================
    st.markdown("<h2>Today's footprint at 1.22 TB / 30 K queries per month</h2>", unsafe_allow_html=True)

    # Optimization toggle — switches Databricks defaults between "same as Redshift workload"
    # and "post-rearchitecture optimized" (Small warehouse, fewer clusters, more ETL pipeline work).
    optimize_mode = st.toggle(
        "Databricks on optimized workload (post-medallion rebuild)",
        value=False, key="dbx_optimize_mode",
        help="When enabled, models steady-state after Juniper rebuilds with medallion: "
             "queries hit pre-aggregated gold MVs so warehouse can shrink to Small with "
             "fewer active clusters. More transformation work runs in scheduled ETL "
             "pipelines (compute-once-on-write beats compute-on-read at 5 QPS).",
    )
    if optimize_mode:
        DBX_DEFAULTS = dict(size_idx=0, clusters=1.0, hours=10, days=22, etl=160)
        dbx_header_label = "Databricks on optimized workload"
        dbx_header_desc = (
            "Post-rearchitecture sizing: Small warehouse (12 DBU/hr) since dashboard "
            "queries hit gold MVs; 1.0 avg cluster sufficient for 5 QPS on pre-aggregated "
            "tables; ETL pipelines absorb the transformation work that used to run on read."
        )
    else:
        DBX_DEFAULTS = dict(size_idx=1, clusters=1.5, hours=10, days=22, etl=100)
        dbx_header_label = "Databricks at the same workload"
        dbx_header_desc = (
            "Sizing matched to Juniper's stated load (5 QPS Looker peak, ~30 K queries/mo, "
            "1 TB/day ingest). Active-clusters default is measured from the 4/28 IWM run."
        )
    # Suffix keys so the widgets re-instantiate with fresh defaults when toggling.
    k = "_opt" if optimize_mode else "_std"

    redshift_col, dbsql_col = st.columns(2, gap="large")

    # --- Redshift baseline (Juniper's actual sizing) ---
    RA3_PRICING_USD_PER_HR = {
        # us-west-2 on-demand and effective-RI hourly rates per AWS pricing pages.
        # Effective RI rates approximate 1yr / 3yr "all upfront" amortized.
        "On-demand":             {"ra3.4xlarge": 3.26,  "ra3.large": 0.543},
        "1yr RI (~30% off)":     {"ra3.4xlarge": 2.28,  "ra3.large": 0.380},
        "3yr RI (~55% off)":     {"ra3.4xlarge": 1.47,  "ra3.large": 0.236},
    }
    REDSHIFT_STORAGE_GB_MONTH = 0.024  # RA3 managed storage list price

    with redshift_col:
        st.markdown("<h3>Redshift baseline</h3>", unsafe_allow_html=True)
        st.markdown(
            "<p class='muted' style='font-size:13px; margin-top:-6px;'>"
            "Juniper's confirmed sizing (5/20 reply): 3 RA3 clusters in us-west-2, "
            "WLM auto with Concurrency Scaling enabled (max 5/2/2)."
            "</p>",
            unsafe_allow_html=True,
        )
        rs_n_data_eng = st.number_input(
            "ra3.4xlarge data-eng nodes",
            min_value=1, max_value=16, value=3, step=1, key="rs_data_eng",
            help="Juniper data-eng cluster: 3 nodes at 12 vCPU / 96 GB RAM / 128 TB managed-storage cap each.",
        )
        rs_n_reporting = st.number_input(
            "ra3.large reporting nodes",
            min_value=1, max_value=16, value=2, step=1, key="rs_reporting",
            help="Juniper reporting cluster: 2 nodes at 2 vCPU / 16 GB RAM / 8 TB cap each.",
        )
        rs_n_insights = st.number_input(
            "ra3.large insights nodes",
            min_value=1, max_value=16, value=2, step=1, key="rs_insights",
        )
        rs_pricing_tier = st.selectbox(
            "Pricing tier",
            list(RA3_PRICING_USD_PER_HR.keys()),
            index=1,  # default 1yr RI as a reasonable midpoint
            key="rs_pricing",
        )
        rs_storage_tb = st.number_input(
            "Managed storage used (TB)",
            min_value=0.1, max_value=500.0, value=1.2, step=0.1, format="%.1f",
            key="rs_storage",
            help="Juniper's stated 1.2 TB in use today (5/20 reply). 416 TB provisioned capacity is "
                 "headroom, not billed separately on RA3.",
        )
        rs_cs_uplift = st.slider(
            "Concurrency Scaling uplift (% of base)",
            0, 50, 15, step=5, key="rs_cs",
            help="WLM auto with CS enabled adds compute during peak. Rough estimate; depends on "
                 "actual firing rate which Juniper hasn't shared.",
        )

        rates = RA3_PRICING_USD_PER_HR[rs_pricing_tier]
        rs_base_hourly = (
            rs_n_data_eng * rates["ra3.4xlarge"] +
            (rs_n_reporting + rs_n_insights) * rates["ra3.large"]
        )
        rs_base_monthly = rs_base_hourly * 24 * 30
        rs_cs_monthly = rs_base_monthly * (rs_cs_uplift / 100.0)
        rs_storage_monthly = rs_storage_tb * 1024 * REDSHIFT_STORAGE_GB_MONTH
        rs_monthly = rs_base_monthly + rs_cs_monthly + rs_storage_monthly
        rs_annual = rs_monthly * 12

        st.metric("Monthly", f"${rs_monthly:,.0f}")
        st.metric("Annual", f"${rs_annual:,.0f}")
        st.markdown(
            f"<p class='muted' style='font-size:12px;'>"
            f"<code>Base: ({rs_n_data_eng} × ${rates['ra3.4xlarge']} + "
            f"{rs_n_reporting + rs_n_insights} × ${rates['ra3.large']}) × 720 hr = "
            f"${rs_base_monthly:,.0f}</code><br>"
            f"<code>CS uplift ({rs_cs_uplift}%): ${rs_cs_monthly:,.0f}</code><br>"
            f"<code>Storage: {rs_storage_tb:.1f} TB × ${REDSHIFT_STORAGE_GB_MONTH}/GB-mo = "
            f"${rs_storage_monthly:,.0f}</code>"
            f"</p>",
            unsafe_allow_html=True,
        )

    # --- DBSQL Pro (pay per query-second) ---
    DBU_PER_HOUR_BY_SIZE = {
        "Small (12 DBU/hr)": 12,
        "Medium (24 DBU/hr)": 24,
        "Large (40 DBU/hr)": 40,
        "X-Large (80 DBU/hr)": 80,
    }
    # SDP serverless DBU rate (Advanced, AWS Premium list)
    SDP_DBU_RATE = 0.36

    with dbsql_col:
        st.markdown(f"<h3>{dbx_header_label}</h3>", unsafe_allow_html=True)
        st.markdown(
            f"<p class='muted' style='font-size:13px; margin-top:-6px;'>{dbx_header_desc}</p>",
            unsafe_allow_html=True,
        )
        size_label = st.selectbox(
            "Warehouse size",
            list(DBU_PER_HOUR_BY_SIZE.keys()),
            index=DBX_DEFAULTS["size_idx"],
            key=f"dbsql_size{k}",
        )
        dbu_per_hour = DBU_PER_HOUR_BY_SIZE[size_label]
        dbu_rate = st.number_input(
            "$ per DBU (Serverless SQL Pro, AWS Premium list)",
            min_value=0.10, value=0.70, step=0.05, key=f"dbsql_dbu_rate{k}",
            help="Serverless SQL Pro list price on AWS Premium is $0.70/DBU. "
                 "(Classic Pro at $0.55/DBU is a different SKU that uses customer-managed VMs.)",
        )
        active_hours_per_day = st.slider(
            "Active hours per day (warehouse running)",
            1, 24, DBX_DEFAULTS["hours"], key=f"dbsql_hours{k}",
            help="Business-day usage with 60 min auto-stop typically lands 8–12h/day for a "
                 "team-shared warehouse. Bursty patterns can go much lower.",
        )
        active_days_per_month = st.slider(
            "Active days per month",
            10, 31, DBX_DEFAULTS["days"], key=f"dbsql_days{k}",
            help="22 business days is a reasonable baseline. Higher if the team runs weekends.",
        )
        avg_clusters = st.slider(
            "Average active clusters (autoscale 1→8)",
            1.0, 8.0, DBX_DEFAULTS["clusters"], step=0.5, key=f"dbsql_clusters{k}",
            help="From the 4/28 IWM run: warehouse averaged 1–2 clusters during 5–10 QPS dashboard mix, "
                 "scaled to 6 clusters briefly during 20 QPS bursts. Drop to 1.0 if queries hit gold MVs only.",
        )
        sdp_dbu_per_day = st.number_input(
            "ETL pipelines: DBU per day",
            min_value=0, max_value=2000, value=DBX_DEFAULTS["etl"], step=10, key=f"dbsql_etl{k}",
            help="Serverless ETL pipelines at $0.36/DBU. For Juniper's 1 TB/day ingest "
                 "with continuous 1-min microbatch + 2-hr batch cadence: ~100 DBU/day at "
                 "today's shape; ~150-180 DBU/day after medallion rebuild (more transformation "
                 "work moves into scheduled pipelines).",
        )

        warehouse_monthly = (
            dbu_per_hour * avg_clusters * dbu_rate *
            active_hours_per_day * active_days_per_month
        )
        etl_monthly = sdp_dbu_per_day * SDP_DBU_RATE * 30
        # S3 managed storage for Delta tables (Databricks does not charge on top)
        delta_storage_monthly = rs_storage_tb * 1024 * 0.023  # S3 standard
        dbsql_total_monthly = warehouse_monthly + etl_monthly + delta_storage_monthly
        dbsql_total_annual = dbsql_total_monthly * 12

        st.metric("Monthly", f"${dbsql_total_monthly:,.0f}")
        st.metric("Annual", f"${dbsql_total_annual:,.0f}")
        st.markdown(
            f"<p class='muted' style='font-size:12px;'>"
            f"<code>Warehouse: {dbu_per_hour} DBU/hr × {avg_clusters} avg clusters × "
            f"${dbu_rate}/DBU × {active_hours_per_day}h × {active_days_per_month}d = "
            f"${warehouse_monthly:,.0f}</code><br>"
            f"<code>ETL pipelines: {sdp_dbu_per_day} DBU/day × ${SDP_DBU_RATE}/DBU × 30 = "
            f"${etl_monthly:,.0f}</code><br>"
            f"<code>Delta storage on S3: {rs_storage_tb:.1f} TB × $0.023/GB-mo = "
            f"${delta_storage_monthly:,.0f}</code>"
            f"</p>",
            unsafe_allow_html=True,
        )

    # Side-by-side delta callout
    delta_annual = rs_annual - dbsql_total_annual
    delta_pct = (delta_annual / rs_annual * 100) if rs_annual > 0 else 0
    callout_cls = "db-callout db-callout--success" if delta_annual > 0 else "db-callout"
    delta_word = "savings" if delta_annual > 0 else "uplift"
    st.markdown(
        f"<div class='{callout_cls}' style='margin-top:18px;'>"
        f"<strong>Today's annual delta: ${abs(delta_annual):,.0f} {delta_word} "
        f"({abs(delta_pct):.0f}%)</strong><br>"
        f"<span style='font-size:13px;'>Redshift baseline ${rs_annual:,.0f}/yr "
        f"vs Databricks ${dbsql_total_annual:,.0f}/yr at the same 1.22 TB / 30K queries/month "
        f"footprint. Move the inputs to test sensitivity.</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 2: Month-over-month projection (interactive)
    # =====================================================================
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Month-over-month projection</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#4A5568;'>Compound month-over-month growth, starting from today's "
        "footprint above. All inputs (warehouse size, RI tier, active hours, ETL DBU) carry "
        "forward. Move the slider to test your own growth assumption.</p>",
        unsafe_allow_html=True,
    )

    input_cols = st.columns(2, gap="medium")
    with input_cols[0]:
        growth_pct = st.slider(
            "Growth (% MoM)",
            min_value=0.0, max_value=15.0, value=3.0, step=0.5, key="mom_growth",
            help="Compounded monthly company-wide growth — customers, data, and query "
                 "volume scale together. 3% MoM ≈ 43% YoY · 5% MoM ≈ 80% YoY · 10% MoM "
                 "≈ 3.1× YoY. Juniper's 1K→10K customer roadmap implies ~6.5% MoM if "
                 "reached over 36 months.",
        )
    with input_cols[1]:
        horizon_label = st.radio(
            "Time horizon",
            ["1 year", "2 years", "3 years"],
            index=1, key="mom_horizon", horizontal=True,
        )
    horizon_months = {"1 year": 12, "2 years": 24, "3 years": 36}[horizon_label]
    growth_mom = 1 + (growth_pct / 100.0)

    # Per-month multipliers (compound). Month 0 = today's footprint.
    # One growth lever — data, customers, and queries assumed to scale together.
    months = list(range(1, horizon_months + 1))
    data_mults = [growth_mom ** (t - 1) for t in months]
    cust_mults = data_mults

    # --- Databricks month series ---
    # DBSQL warehouse: scales sub-linearly with growth (^0.4 for autoscale efficiency),
    # plus a one-time size bump when growth multiplier crosses 5×.
    SIZE_ORDER = ["Small (12 DBU/hr)", "Medium (24 DBU/hr)", "Large (40 DBU/hr)", "X-Large (80 DBU/hr)"]
    cur_size_idx = SIZE_ORDER.index(size_label) if size_label in SIZE_ORDER else 1
    dbsql_series = []
    sdp_series = []
    for t_idx, t in enumerate(months):
        c_mult = cust_mults[t_idx]
        d_mult = data_mults[t_idx]
        # Bump warehouse size once growth multiplier hits 5× (one tier up, capped at X-Large)
        size_idx_t = min(cur_size_idx + (1 if c_mult >= 5 else 0), len(SIZE_ORDER) - 1)
        dbu_per_hour_t = DBU_PER_HOUR_BY_SIZE[SIZE_ORDER[size_idx_t]]
        scaled_clusters_t = avg_clusters * (c_mult ** 0.4)
        dbsql_m = (
            dbu_per_hour_t * scaled_clusters_t * dbu_rate *
            active_hours_per_day * active_days_per_month
        )
        # ETL pipelines: linear with data in same-workload mode; sub-linear in optimized mode
        # (medallion gold tables are dimension-bounded, not row-bounded)
        etl_exp = 0.5 if optimize_mode else 1.0
        sdp_m = sdp_dbu_per_day * (d_mult ** etl_exp) * SDP_DBU_RATE * 30
        dbsql_series.append(dbsql_m)
        sdp_series.append(sdp_m)

    # --- Redshift month series ---
    # Stepped: node counts round up via sub-linear formula; CS uplift bumps at thresholds.
    # Storage excluded to keep the chart focused on compute (Redshift managed storage
    # is ~$30/mo at this scale — rounding error vs the $6K+ compute base).
    redshift_series = []
    rs_nodes_series = []  # for hover/breakdown
    for t_idx, t in enumerate(months):
        c_mult = cust_mults[t_idx]
        d_mult = data_mults[t_idx]
        n_data_eng_t = max(rs_n_data_eng, int(-(-rs_n_data_eng * (d_mult ** 0.7) // 1)))  # ceil
        n_reporting_t = max(rs_n_reporting, int(-(-rs_n_reporting * (c_mult ** 0.5) // 1)))
        n_insights_t = max(rs_n_insights, int(-(-rs_n_insights * (c_mult ** 0.5) // 1)))
        base_hourly_t = (
            n_data_eng_t * rates["ra3.4xlarge"] +
            (n_reporting_t + n_insights_t) * rates["ra3.large"]
        )
        base_monthly_t = base_hourly_t * 24 * 30
        # CS uplift steps with growth bursts
        cs_uplift_t = rs_cs_uplift + (0 if c_mult < 2 else 10 if c_mult < 5 else 30 if c_mult < 10 else 45)
        cs_monthly_t = base_monthly_t * (cs_uplift_t / 100.0)
        redshift_series.append(base_monthly_t + cs_monthly_t)
        rs_nodes_series.append((n_data_eng_t, n_reporting_t, n_insights_t))

    # --- Build the stacked-area chart ---
    import plotly.graph_objects as go
    mom_fig = go.Figure()
    mom_fig.add_trace(go.Scatter(
        x=months, y=dbsql_series, mode="lines", stackgroup="dbx",
        name="Databricks SQL (warehouse)",
        line=dict(width=0, color="rgba(229, 80, 32, 0.9)"),  # lava
        hovertemplate="Month %{x}<br>Databricks SQL: $%{y:,.0f}<extra></extra>",
    ))
    mom_fig.add_trace(go.Scatter(
        x=months, y=sdp_series, mode="lines", stackgroup="dbx",
        name="ETL pipelines",
        line=dict(width=0, color="rgba(0, 169, 114, 0.9)"),  # green
        hovertemplate="Month %{x}<br>ETL pipelines: $%{y:,.0f}<extra></extra>",
    ))
    mom_fig.add_trace(go.Scatter(
        x=months, y=redshift_series, mode="lines+markers",
        name="Redshift (compute)",
        line=dict(color="#222", width=3, dash="solid"),
        marker=dict(size=6),
        hovertemplate=(
            "Month %{x}<br>Redshift: $%{y:,.0f}<br>"
            "Nodes: %{customdata[0]} data-eng / %{customdata[1]} reporting / %{customdata[2]} insights"
            "<extra></extra>"
        ),
        customdata=rs_nodes_series,
    ))
    mom_fig.update_layout(
        title=f"Monthly spend over {horizon_label.lower()} · growth {growth_pct:.1f}% MoM",
        xaxis=dict(title="Month", dtick=3 if horizon_months > 12 else 1),
        yaxis=dict(title="Monthly spend ($)", rangemode="tozero", tickprefix="$", tickformat=","),
        hovermode="x unified",
        height=460,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=60, r=40, t=60, b=80),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(family="DM Sans, sans-serif"),
    )
    st.plotly_chart(mom_fig, use_container_width=True)

    # --- Summary stats over the horizon ---
    total_dbx = sum(dbsql_series) + sum(sdp_series)
    total_rs = sum(redshift_series)
    period_savings = total_rs - total_dbx
    period_savings_pct = (period_savings / total_rs * 100) if total_rs > 0 else 0
    final_mult = data_mults[-1]

    sum_cols = st.columns(4, gap="medium")
    sum_cols[0].metric(
        f"Total Redshift ({horizon_label})", f"${total_rs:,.0f}",
        delta=f"final month ${redshift_series[-1]:,.0f}",
    )
    sum_cols[1].metric(
        f"Total Databricks ({horizon_label})", f"${total_dbx:,.0f}",
        delta=f"final month ${(dbsql_series[-1] + sdp_series[-1]):,.0f}",
    )
    savings_color = "normal" if period_savings >= 0 else "inverse"
    sum_cols[2].metric(
        f"Cumulative {'savings' if period_savings >= 0 else 'uplift'}",
        f"${abs(period_savings):,.0f}",
        delta=f"{abs(period_savings_pct):.0f}% of Redshift spend",
        delta_color=savings_color,
    )
    sum_cols[3].metric(
        f"End-state (month {horizon_months})",
        f"{final_mult:.1f}× scale",
        delta=f"silver ≈ {rs_storage_tb * final_mult:,.1f} TB",
    )

    etl_scaling_note = (
        "ETL pipelines scale ∝ growth<sup>0.5</sup> (sub-linear: gold MVs are "
        "dimension-bounded by arenas × funds × properties × months, not by silver row "
        "count, so 10× data ≠ 10× pipeline work)"
        if optimize_mode else
        "ETL pipelines scale linearly with growth"
    )
    st.markdown(
        f"<p class='muted' style='font-size:13px; margin-top:10px;'>"
        f"<strong>How the chart scales:</strong> Databricks SQL warehouse compute grows "
        f"sub-linearly (avg clusters ∝ growth<sup>0.4</sup>) and bumps one warehouse tier "
        f"once growth ≥ 5×. {etl_scaling_note}. Redshift node counts step up via ceiling "
        f"on sub-linear scaling (data-eng ∝ growth<sup>0.7</sup>, reporting/insights ∝ "
        f"growth<sup>0.5</sup>), and CS uplift bumps at 2× / 5× / 10× thresholds. Storage "
        f"on both sides excluded — it's ~$30/mo and rounds out of the compute story.</p>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 3: Growth projection scenarios (3-card view)
    # =====================================================================
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Growth projection scenarios: today → PDF Reporting → 10× scale</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#4A5568;'>Three points on the same growth curve. "
        "<strong>Today</strong> is the 1.22 TB / 30 K queries/month footprint above. "
        "<strong>PDF Reporting</strong> adds 4.5 B documents (~5× data trigger, "
        "the stated catalyst). <strong>10× scale</strong> covers the 1K→10K customer "
        "expansion target. Storage and query volume scale; compute scales sub-linearly "
        "thanks to Liquid Clustering pruning and pre-aggregated gold.</p>",
        unsafe_allow_html=True,
    )

    SCENARIOS = [
        ("Today (1.22 TB)", 1.0, 1.0),
        ("PDF Reporting (~5× data)", 5.0, 2.0),
        ("10× scale (1K→10K customers)", 10.0, 10.0),
    ]

    # Redshift growth: node counts step up via sub-linear formula; CS uplift bumps at thresholds.
    def _rs_at_scale(data_mult: float, q_mult: float) -> float:
        scaled_data_eng = max(3, int(round(3 * (1 + (data_mult - 1) * 0.7))))
        scaled_reporting = max(2, int(round(2 * (1 + (q_mult - 1) * 0.5))))
        scaled_insights = max(2, int(round(2 * (1 + (q_mult - 1) * 0.5))))
        base_hourly = (
            scaled_data_eng * rates["ra3.4xlarge"] +
            (scaled_reporting + scaled_insights) * rates["ra3.large"]
        )
        base_monthly = base_hourly * 24 * 30
        cs_pct = rs_cs_uplift + (30 if q_mult >= 5 else 0)
        cs_monthly = base_monthly * (cs_pct / 100.0)
        storage_monthly = rs_storage_tb * data_mult * 1024 * REDSHIFT_STORAGE_GB_MONTH
        return (base_monthly + cs_monthly + storage_monthly) * 12

    # Databricks growth: warehouse size bump kicks in at 10× scale (Medium→Large);
    # ETL DBU scales linearly with data in same-workload mode, sub-linearly (^0.5) in
    # optimized mode because gold MVs are dimension-bounded; storage linear with data.
    def _dbsql_at_scale(data_mult: float, q_mult: float) -> float:
        scaled_dbu = dbu_per_hour if q_mult < 5 else (40 if dbu_per_hour < 40 else dbu_per_hour)
        scaled_clusters = avg_clusters + (q_mult - 1) * 0.4
        warehouse = (
            scaled_dbu * scaled_clusters * dbu_rate *
            active_hours_per_day * active_days_per_month
        )
        etl_exp = 0.5 if optimize_mode else 1.0
        etl = sdp_dbu_per_day * (data_mult ** etl_exp) * SDP_DBU_RATE * 30
        storage = rs_storage_tb * data_mult * 1024 * 0.023
        return (warehouse + etl + storage) * 12

    proj_data = []
    for label, data_mult, q_mult in SCENARIOS:
        rs_proj = _rs_at_scale(data_mult, q_mult)
        db_proj = _dbsql_at_scale(data_mult, q_mult)
        proj_data.append({
            "scenario": label,
            "redshift_annual": rs_proj,
            "dbsql_annual": db_proj,
            "savings": rs_proj - db_proj,
            "savings_pct": (rs_proj - db_proj) / rs_proj * 100 if rs_proj > 0 else 0,
        })

    proj_cols = st.columns(3, gap="medium")
    for col, row in zip(proj_cols, proj_data):
        savings_word = "savings" if row["savings"] > 0 else "uplift"
        savings_color = "#00A972" if row["savings"] > 0 else "#E53935"
        col.markdown(
            f"<div class='db-callout' style='min-height:170px;'>"
            f"<strong>{row['scenario']}</strong>"
            f"<hr style='margin:8px 0; border:0; border-top:1px solid var(--db-border-hair);' />"
            f"Redshift: <strong>${row['redshift_annual']:,.0f}/yr</strong><br>"
            f"Databricks: <strong>${row['dbsql_annual']:,.0f}/yr</strong>"
            f"<div style='margin-top:8px; color:{savings_color}; font-weight:600;'>"
            f"${abs(row['savings']):,.0f} {savings_word} "
            f"({abs(row['savings_pct']):.0f}%)</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<p class='muted' style='font-size:13px; margin-top:10px;'>"
        "<strong>Assumptions in the 10× model:</strong> Redshift data-eng node count grows "
        "~0.7× the data multiplier (Spectrum + Concurrency Scaling absorb some of it); "
        "reporting/insights nodes grow ~0.5× the query multiplier. Databricks SQL bumps "
        "from Medium (24 DBU/hr) to Large (40 DBU/hr) at 10× scale; average clusters grow "
        "~0.4× the query multiplier thanks to Liquid Clustering pruning. Storage scales "
        "linearly with data on both. Concurrency Scaling uplift bumps to +45% at 5×/10× "
        "scale to model peak burst behavior on Redshift.</p>",
        unsafe_allow_html=True,
    )

    # =====================================================================
    # SECTION 4: Tag-based cost attribution
    # =====================================================================
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
            "End-to-end serverless stack: ETL pipelines + DBSQL (zero clusters to manage)",
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
- **Declarative ETL pipelines** let you declare tables. The runtime handles the DAG, retries, backfills, and schema evolution.
- **Self-healing micro-batches** retry on failure; bad records route to quarantine tables.
- **Unified observability** through `system.lakeflow.pipelines`, `system.compute.node_types`, and query history. All SQL, no external APM wiring.
""")

    # -----------------------------------------------------------------
    # Orchestration DAG — the 5-task fan-out behind this demo (live screenshot)
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Orchestration: one DAG, five tasks</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The job behind this demo: medallion ETL pipeline runs, then fans "
        "out to four downstream sync pipelines in parallel. End-to-end wall clock was ~326s "
        "on 2026-04-24 (133s SDP, longest sync 193s). Add a schedule or a file-arrival "
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
        caption="Live orchestration DAG in the juniper-benchmark-refresh job. Medallion ETL pipeline fans out to four downstream sync pipelines.",
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
        delta="1 medallion + downstream syncs",
        help="Medallion ETL pipeline behind the benchmark, plus downstream synced-table pipelines.",
    )
    op_cols[1].metric(
        "Manual interventions",
        "0",
        delta="on this demo workspace · last 30 days",
        delta_color="off",
        help="Human pager events, config tweaks, cluster resizes on this demo workspace. Serverless ETL + DBSQL run themselves.",
    )
    op_cols[2].metric(
        "Serverless compute",
        "100%",
        delta="Pipelines · DBSQL",
        help="Zero long-lived clusters. Every runtime is serverless with auto-scaling and auto-stop.",
    )
    op_cols[3].metric(
        "Cluster nodes to patch",
        "0",
        delta="Databricks handles it",
        help="Serverless compute is patched and scaled by Databricks. Nothing for your SREs to babysit.",
    )
    st.caption(
        "The ETL pipeline self-heals on transient failures, retries bad records to quarantine, "
        "and scales compute up/down based on load, all without a human in the loop. Zero "
        "interventions above is for this demo workspace — the larger point is the operating "
        "envelope, not this specific run."
    )

    # -----------------------------------------------------------------
    # Why this matters at scale — independent industry data
    # -----------------------------------------------------------------
    st.markdown(
        "<div class='db-callout' style='margin-top:14px;'>"
        "<strong>The KLO time you reclaim is not free engineering capacity to spend elsewhere.</strong>"
        "<p style='margin:8px 0 0 0; font-size:13px;'>"
        "Independent industry data: data engineers spend "
        "<strong>44% of their time</strong> building and rebuilding pipelines, "
        "averaging "
        "<strong>~$520K/year in wasted effort per $100M+ company</strong> "
        "<span class='muted'>(Wakefield Research / Fivetran 2021 survey of 300 data &amp; "
        "analytics VPs+ across US, UK, Germany, France · "
        "<a href='https://www.fivetran.com/press/data-and-analytics-leaders-report-wasting-funds-on-bad-data' "
        "target='_blank'>source</a>)</span>. Serverless ETL + managed warehouses + "
        "Predictive Optimization push that line down by removing the categories of work "
        "(cluster sizing, driver patching, vacuum, partition tuning, manual scale-out) "
        "that account for most of it."
        "</p>"
        "</div>",
        unsafe_allow_html=True,
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

    if workspace_host:
        base = f"https://{workspace_host}"
        links = [
            ("ETL pipeline (medallion DAG)",
             f"{base}/pipelines/{pipeline_id}",
             "The juniper_benchmark_medallion DAG: bronze, silver, gold. Event log, run history, lineage all inline."),
            ("Orchestration job (ETL + 4 parallel syncs)",
             f"{base}/jobs/658584579307262",
             "5-task DAG: medallion ETL pipeline, then 4 downstream sync pipelines fan out in parallel. Schedule it or trigger on file arrival."),
            ("Jobs & Pipelines: Runs view",
             f"{base}/jobs/runs?asset_type=jobs&o={workspace_id_klo}",
             "Workspace-wide run history. Success/fail timeline, top error codes, per-run drill-in. One pane of glass for every scheduled workload."),
            ("DBSQL warehouse",
             f"{base}/sql/warehouses/{warehouse_id}",
             "Serverless Starter Warehouse (Small, Pro). Start/stop, size, auto-stop, monitoring."),
            ("DBSQL query history",
             f"{base}/sql/history?o=&warehouse_id={warehouse_id}",
             "Every benchmark query we just measured, with duration, rows read, query profile."),
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
            "Source-to-serving chain: raw Parquet → bronze → silver (liquid-clustered) → gold",
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
        "and gold materialized views. Unity Catalog tracks this graph automatically, per query. "
        "Below is a screenshot of the live UC lineage tab for <code>gold_fund_performance</code>; "
        "the same graph is available for every table in the demo catalog.</p>",
        unsafe_allow_html=True,
    )

    # Root table used for the deep-link below
    table = "gold_fund_performance"
    st.image(
        "assets/lineage-graph.png",
        caption="Live Unity Catalog lineage graph for gold_fund_performance: volumes, streaming bronze, streaming silver, materialized gold, with a downstream synced-table replica.",
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
    st.markdown(
        "<p class='muted' style='margin-top:-4px; margin-bottom:12px;'>"
        "The differentiated controls first, since baseline compliance certifications are "
        "table stakes in this eval. Compliance posture is listed at the bottom for completeness."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("""
- [Attribute-based access control (ABAC)](https://docs.databricks.com/en/data-governance/unity-catalog/abac/index.html) via UC tags: enforce policies on PII/PHI tags, not on object paths. Tag once, govern everywhere.
- [Column masks](https://docs.databricks.com/en/tables/column-mask.html) and [row filters](https://docs.databricks.com/en/tables/row-filter.html) as plain SQL functions, attached to tables in UC. No bolt-on tooling, no per-BI-tool re-implementation.
- [Unity Catalog RBAC](https://docs.databricks.com/en/data-governance/unity-catalog/manage-privileges/index.html): one privilege model from catalog → schema → table → [row / column](https://docs.databricks.com/en/tables/row-and-column-filters.html). Same grants flow to every downstream tool that reads through UC.
- [Customer-managed keys (CMK)](https://docs.databricks.com/en/security/keys/customer-managed-keys.html) for encryption at rest (managed services + workspace storage).
- [PrivateLink](https://docs.databricks.com/en/security/network/classic/privatelink.html) for workspace (front-end) and control-plane (back-end) traffic.
- [MFA + SSO](https://docs.databricks.com/en/admin/users-groups/single-sign-on/index.html) via [Okta, Entra, Google Workspace, PingIdentity](https://docs.databricks.com/en/admin/users-groups/scim/index.html), enforced at the workspace/account level.
- [Audit logs](https://docs.databricks.com/en/admin/account-settings/audit-logs.html) via `system.access.audit`: every read, grant, and config change.
- Compliance baseline: [SOC 2 Type II, ISO 27001, HIPAA, PCI DSS, FedRAMP Moderate](https://www.databricks.com/trust/compliance), inherited from the platform on day 1.
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
| **Looker** | OAuth U2M | GA | Each Looker user authenticates with their own identity. PDTs aren't supported with OAuth; materialize in Lakeflow pipelines instead. |
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
        "<code>system.information_schema.tables</code>. Table type distinguishes pipeline "
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
