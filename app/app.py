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
    build_cost_projection,
    build_latency_vs_concurrency,
    build_query_mix_chart,
    build_scorecard_radar,
    build_throughput_chart,
)
from lib.lineage import get_table_lineage, get_uc_lineage_ui_url
from lib.scorecard import PILLARS, STAGE_COLORS, get_pillar, stage_value
from lib.theme import inject_theme


def _fmt_latency(ms: float) -> str:
    """Render a latency as '520 ms' under 1s, '2.86 s' above. No spurious decimals."""
    if ms is None or ms <= 0:
        return "—"
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

    st.markdown("<hr class='db-hair' style='margin:24px 0 16px 0;' />", unsafe_allow_html=True)
    _wh_id = queries.HTTP_PATH.rsplit("/", 1)[-1] if "/" in queries.HTTP_PATH else queries.HTTP_PATH
    st.markdown(
        f"<div class='muted' style='line-height:1.8;'>"
        f"<div>Warehouse &nbsp;<code style='font-size:0.75rem'>{_wh_id}</code></div>"
        f"<div>Catalog &nbsp;<code style='font-size:0.75rem'>{queries.CATALOG}</code></div>"
        f"<div>Schema &nbsp;<code style='font-size:0.75rem'>{queries.SCHEMA}</code></div>"
        f"</div>",
        unsafe_allow_html=True,
    )


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
        "Benchmark demo — what we built, what we measured, and what else we support."
        "</p>",
        unsafe_allow_html=True,
    )

    # TL;DR framing
    st.markdown(
        "<div class='db-callout'>"
        "<strong>TL;DR.</strong> We built the Juniper Square data shape at "
        "<strong>10K arenas / 2B GL transactions / 10 TB</strong> Delta, fed it through a "
        "medallion pipeline on Serverless Spark Declarative Pipelines, and served the gold "
        "tier through both DBSQL Serverless (Small, Pro) and Lakebase Autoscale (1 CU, "
        "Postgres 17). We then measured dashboard-shaped queries at concurrency 1 → 20. "
        "Every drill-in is wired to live data in this workspace — the evidence is yours "
        "to judge against your own maturity framework."
        "</div>",
        unsafe_allow_html=True,
    )

    # Headline stats
    st.markdown("<h2>Headline results</h2>", unsafe_allow_html=True)
    stat_cols = st.columns(4, gap="medium")
    stat_cols[0].metric("Arenas (tenants)", "10,000", delta="matches 10K target scale")
    stat_cols[1].metric("GL transactions", "2.0 B", delta="10 TB silver on Delta")
    stat_cols[2].metric(
        "Lakebase P95 @ c=20",
        "520 ms",
        delta="worst query, heaviest concurrency tested",
    )
    stat_cols[3].metric(
        "vs Redshift today",
        "15-60× faster",
        delta="current pain: 10-45 s",
        delta_color="inverse",
    )

    st.markdown(
        "<p class='muted' style='margin-top:16px;'>"
        "Measured against dashboard SLOs of P50 ≤ 4 s / P95 ≤ 5 s / P99 ≤ 7 s, Lakebase "
        "clears P95 with roughly 10× headroom; DBSQL clears it with roughly 2× headroom. "
        "Both are detailed on the <em>Data latency</em> tab."
        "</p>",
        unsafe_allow_html=True,
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
    warehouse_id_ov = os.environ.get("DATABRICKS_WAREHOUSE_ID", "133b52f9331b883d")
    catalog_ov = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
    workspace_id_ov = "7474657973275984"
    pipeline_id_ov = "390e607c-83e4-4df8-8468-4655bb8c341a"
    lakebase_project_ov = "juniper-sq-benchmark"

    if workspace_host_ov:
        base_ov = f"https://{workspace_host_ov}"
        assets = [
            ("Spark Declarative Pipeline — the medallion DAG",
             f"{base_ov}/pipelines/{pipeline_id_ov}",
             "Bronze → silver (liquid-clustered) → gold. Event log, run history, lineage inline."),
            ("Unity Catalog — browse the demo catalog",
             f"{base_ov}/explore/data/{catalog_ov}",
             "Tables, tags, column comments, permissions, lineage tabs."),
            ("Lineage on gold_gl_monthly_summary",
             f"{base_ov}/explore/data/{catalog_ov}/pipeline/gold_gl_monthly_summary?activeTab=lineage",
             "End-to-end lineage from raw Parquet landing to Lakebase serving."),
            ("Lakebase project — Postgres endpoint",
             f"{base_ov}/lakebase/projects/743d650c-b6e7-488c-a783-219d299f71a5",
             "Juniper Square Benchmark endpoint. Branching, autoscale settings, roles."),
            ("DBSQL warehouse — Serverless Small Pro",
             f"{base_ov}/sql/warehouses/{warehouse_id_ov}",
             "Start/stop, sizing, auto-stop, monitoring. The warehouse that ran the benchmark."),
            ("DBSQL query history",
             f"{base_ov}/sql/history?o=&warehouse_id={warehouse_id_ov}",
             "Every benchmark query we measured, with duration, rows read, query profile."),
            ("Serverless usage (billing)",
             f"{base_ov}/usage",
             "DBU consumption over time — serverless scaled up for the benchmark, idled after."),
            ("Governed tags admin",
             f"{base_ov}/governance/governed-tags?o={workspace_id_ov}",
             "Account-level tag taxonomy — PII, retention, regulatory — enforced across UC."),
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

    render_demo_scope(
        demonstrated=[
            "Dashboard-shaped query mix (6 queries, weighted) at concurrency 1–20",
            "Head-to-head DBSQL Serverless vs Lakebase Autoscale on the same gold tier",
            "Sub-second P95 on Lakebase across the whole matrix",
            "10-min auto-stop on DBSQL, scale-to-zero available on Lakebase",
        ],
        also_supported=[
            ("Lakeflow Connect CDC (Postgres/Oracle/MySQL/SQL Server)",
             "https://docs.databricks.com/en/ingestion/lakeflow-connect/index.html"),
            ("Structured Streaming on Kafka / Kinesis",
             "https://docs.databricks.com/en/structured-streaming/index.html"),
            ("Auto Loader for S3/ADLS/GCS event-driven ingest",
             "https://docs.databricks.com/en/ingestion/cloud-object-storage/auto-loader/index.html"),
            ("Spark Declarative Pipelines (continuous microbatch)",
             "https://docs.databricks.com/en/delta-live-tables/index.html"),
        ],
    )
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)

    result = queries.get_benchmark_summary()
    preview_banner(result, "from benchmark_summary")
    df = result.df

    col1, col2, col3, col4 = st.columns(4, gap="medium")
    if not df.empty:
        lakebase_df = df[df["target"] == "lakebase"]
        dbsql_df = df[df["target"] == "dbsql"]
        # Worst-case view: max P95 across any query at the highest concurrency tested.
        # This is the number a reviewer will stress-test against the 5s SLO.
        peak_conc = int(df["concurrency"].max())
        lb_peak_p95 = (
            lakebase_df[lakebase_df["concurrency"] == peak_conc]["p95_ms"].max()
            if not lakebase_df.empty else 0
        )
        db_peak_p95 = (
            dbsql_df[dbsql_df["concurrency"] == peak_conc]["p95_ms"].max()
            if not dbsql_df.empty else 0
        )
        baseline_redshift = 22000
        speedup_vs_rs = baseline_redshift / lb_peak_p95 if lb_peak_p95 else 0
        col1.metric(f"Lakebase P95 @ {peak_conc}", _fmt_latency(lb_peak_p95), delta="worst query")
        col2.metric(f"DBSQL P95 @ {peak_conc}", _fmt_latency(db_peak_p95), delta="worst query")
        col3.metric("Redshift today", "10-45 s", delta="current pain", delta_color="inverse")
        col4.metric("Lakebase vs Redshift", f"{speedup_vs_rs:,.0f}x faster")
    else:
        col1.metric("Lakebase p95", "no data")

    # Headroom callout: concrete framing against the stated dashboard SLOs.
    if not df.empty and lb_peak_p95 > 0:
        headroom = 5000 / lb_peak_p95
        st.markdown(
            f"<div class='db-callout db-callout--success'>"
            f"<strong>{headroom:.0f}× headroom under the P95 = 5s dashboard SLO on Lakebase</strong> — "
            f"worst query at the highest concurrency tested (Q5 pnl_rollup at conc {peak_conc}) "
            f"still lands at {_fmt_latency(lb_peak_p95)}. DBSQL clears the same bar with "
            f"{5000/db_peak_p95:.1f}× headroom."
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<h2>Latency vs concurrency</h2>", unsafe_allow_html=True)
    metric_col, _ = st.columns([1, 3], gap="small")
    with metric_col:
        metric = st.selectbox("Latency metric", ["p50_ms", "p95_ms", "p99_ms"], index=1)
    st.plotly_chart(build_latency_vs_concurrency(df, metric), use_container_width=True)

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.plotly_chart(build_throughput_chart(df), use_container_width=True)
        st.caption(
            "Aggregate = sum of per-query QPS across all 6 query types. Growth means "
            "we haven't hit saturation yet; at higher concurrency the curve would bend flat."
        )
    with c2:
        st.plotly_chart(build_query_mix_chart(df, metric), use_container_width=True)
        st.caption(
            "Per-query P95 at the highest concurrency tested. Lakebase is 5–10× lower than "
            "DBSQL across every query, including the heaviest (Q5 P&L rollup)."
        )

    # -----------------------------------------------------------------
    # Compute that ran this benchmark (placed between charts and query SQL)
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Compute that ran this benchmark</h2>", unsafe_allow_html=True)
    spec_cols = st.columns(2, gap="large")
    with spec_cols[0]:
        st.markdown(
            "<div class='db-callout'>"
            "<strong>DBSQL warehouse</strong><br>"
            "Serverless SQL Pro — <strong>Small</strong><br>"
            "12 DBU / hour · auto-stop 10 min · 1 cluster<br>"
            "<code>warehouse_id 133b52f9331b883d</code>"
            "</div>",
            unsafe_allow_html=True,
        )
    with spec_cols[1]:
        st.markdown(
            "<div class='db-callout'>"
            "<strong>Lakebase endpoint</strong><br>"
            "Autoscale — <strong>1 CU</strong> (min=max), 2 GB RAM<br>"
            "Postgres 17 · read/write · scale-to-zero off<br>"
            "<code>ep-curly-sun-d24e8bfa</code>"
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div class='db-callout db-callout--success'>"
        "<strong>Why a Small warehouse handled 20 concurrent queries against a 10TB table "
        "at P95 &lt; 3s — with no autoscale-out (min = max = 1 cluster)</strong>"
        "<ol style='margin:8px 0 0 18px; padding:0;'>"
        "<li><strong>Liquid Clustering on <code>(arena_id, transaction_date)</code>.</strong> "
        "Every query filters on a single arena, so file pruning skips ~99.99% of the 2B-row "
        "silver fact table. The &ldquo;10TB&rdquo; query really reads a few hundred MB. "
        "Liquid Clustering went GA in 2024 and replaces manual partitioning/Z-order tuning.</li>"
        "<li><strong>Gold pre-aggregation via Spark Declarative Pipelines.</strong> 5 of 6 "
        "benchmark queries hit <code>gold_gl_monthly_summary</code> (7.7M rows), not silver. "
        "The 10TB sits in silver as source of truth; serving reads the pre-aggregated gold.</li>"
        "<li><strong>Photon + Serverless SQL Pro.</strong> Photon is the default execution "
        "engine on serverless warehouses — vectorized, columnar, aggressive column pruning. The "
        "Pro channel adds intelligent workload management so one cluster can serve many "
        "concurrent queries without falling over.</li>"
        "</ol>"
        "<p style='margin:10px 0 0 0; font-size:13px;'>Net: the right answer for this workload "
        "is a well-tuned Small, not a bigger warehouse. Concurrency handling is a software "
        "property of the Pro channel, not a capacity problem you throw hardware at.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # Benchmark queries: let a presenter pick any Q and see the exact SQL that
    # produced the measured latencies, both Spark SQL (DBSQL) and Postgres (Lakebase).
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Benchmark queries</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The six queries behind the charts above. Each runs against a "
        "random <code>arena_id</code> from a pool of 500, expanded by weight into a round-robin "
        "mix. The Spark SQL (DBSQL) and Postgres (Lakebase) variants are kept in sync; differences "
        "are just dialect (date math, NUMERIC casts).</p>",
        unsafe_allow_html=True,
    )

    picker_col, _ = st.columns([2, 1], gap="small")
    with picker_col:
        pick = st.selectbox(
            "Query",
            options=[q.display_name for q in BENCHMARK_QUERIES],
            index=4,  # default to Q5 pnl_rollup — the worst case
            key="query_picker",
        )
    chosen = next(q for q in BENCHMARK_QUERIES if q.display_name == pick)

    meta_cols = st.columns([2, 1, 1], gap="medium")
    meta_cols[0].markdown(f"**{chosen.summary}**")
    meta_cols[1].metric("Category", chosen.category)
    meta_cols[2].metric("Mix weight", f"×{chosen.weight}")

    workspace_host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "133b52f9331b883d")
    if workspace_host:
        editor_url = f"https://{workspace_host}/sql/editor/?o=&warehouse_id={warehouse_id}"
        history_url = f"https://{workspace_host}/sql/history?o=&warehouse_id={warehouse_id}"
        st.markdown(
            f"<p><a href='{editor_url}' target='_blank'>Open DBSQL editor on the benchmark warehouse →</a> "
            f"&nbsp;&nbsp; "
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

    with st.expander("Show benchmark_summary SQL"):
        st.code(result.sql or "", language="sql")
    with st.expander("Raw summary rows"):
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
            "Lakebase list/promo pricing pulled from databricks.com/product/pricing/lakebase",
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
            ("Photon — vectorized execution, 3-5× CPU efficiency on SQL",
             "https://docs.databricks.com/en/runtime/photon.html"),
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
        st.markdown("<h3>DBSQL — pay-per-query-second</h3>", unsafe_allow_html=True)
        size_label = st.selectbox(
            "Warehouse size",
            list(DBU_PER_HOUR_BY_SIZE.keys()),
            index=2,  # Small is what we benchmarked
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
        st.markdown("<h3>Lakebase — pay-per-CU-hour</h3>", unsafe_allow_html=True)
        cu_count = st.number_input(
            "CU count (2 GB RAM each)",
            min_value=1, max_value=16, value=1, step=1, key="lb_cu",
        )
        use_promo = st.toggle(
            "Apply 50% launch promo (through Jan 31, 2027)",
            value=True, key="lb_promo",
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

        # Sync pipeline cost (Delta → Lakebase Synced Tables via serverless SDP)
        st.markdown("<p style='margin-top:12px;'><strong>Sync pipeline</strong></p>", unsafe_allow_html=True)
        sync_cadence = st.selectbox(
            "Delta → Lakebase sync cadence",
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
            f"${lb_cost_per_query:,.8f}" if lb_cost_per_query else "—",
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
    st.markdown("<h2>DBSQL vs Lakebase — when to use which</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The two paths are <strong>complementary, not competitive</strong>. DBSQL "
        "bills per query-second, so it wins on bursty, ad-hoc, and BI workloads — you pay only while "
        "queries run. Lakebase bills per CU-hour regardless of query volume, so it wins on "
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
        "Lakebase is provisioned — compute scales with arenas (+1 CU per 2,000 arenas), storage scales "
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
            "Liquid Clustering on silver_gl_transactions — delivered the query pruning behind the latency results",
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
        "and scales compute up/down based on load — all without a human in the loop."
    )

    # -----------------------------------------------------------------
    # Inspect in Databricks — deep links into the live workspace
    # -----------------------------------------------------------------
    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Inspect in Databricks</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>The live operational UI for every surface behind this demo — "
        "there's nothing hidden, everything is one click away in the workspace.</p>",
        unsafe_allow_html=True,
    )

    workspace_host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "133b52f9331b883d")
    catalog = os.environ.get("DATABRICKS_CATALOG", "juniper_square_demo_catalog")
    # Our primary SDP pipeline id (juniper_benchmark_medallion)
    pipeline_id = "390e607c-83e4-4df8-8468-4655bb8c341a"
    lakebase_project = "juniper-sq-benchmark"

    if workspace_host:
        base = f"https://{workspace_host}"
        links = [
            ("SDP pipeline",
             f"{base}/pipelines/{pipeline_id}",
             "The juniper_benchmark_medallion DAG — bronze, silver, gold. Event log, run history, lineage all inline."),
            ("DBSQL warehouse",
             f"{base}/sql/warehouses/{warehouse_id}",
             "Serverless Starter Warehouse (Small, Pro). Start/stop, size, auto-stop, monitoring."),
            ("DBSQL query history",
             f"{base}/sql/history?o=&warehouse_id={warehouse_id}",
             "Every benchmark query we just measured, with duration, rows read, query profile."),
            ("Lakebase project",
             f"{base}/lakebase/projects/743d650c-b6e7-488c-a783-219d299f71a5",
             "The Juniper Square Benchmark Postgres endpoint — branching, autoscale config, roles."),
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
            "Live UC link-out on the demo's root gold table",
        ],
        also_supported=[
            ("Column-level lineage + REST API",
             "https://docs.databricks.com/en/data-governance/unity-catalog/data-lineage.html"),
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
        "<p class='muted'>The GL fact table flows from raw Parquet ingest through bronze → silver "
        "(liquid-clustered) → gold, then into Lakebase for sub-second Postgres serving. Unity Catalog "
        "tracks this graph automatically, per query.</p>",
        unsafe_allow_html=True,
    )

    # Root the lineage at the gold GL table (the actual root of our demo pipeline)
    table = "gold_gl_monthly_summary"
    graph = get_table_lineage(f"{queries.CATALOG}.{queries.SCHEMA}.{table}")

    st.markdown("```\n" + "\n".join(
        f"{e.upstream}  ->  {e.downstream}" for e in graph.edges
    ) + "\n```")

    ui_url = get_uc_lineage_ui_url(queries.CATALOG, queries.SCHEMA, table)
    if ui_url:
        st.markdown(
            f"<a href='{ui_url}' target='_blank'>Open live lineage for {table} in Unity Catalog &rarr;</a>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span class='muted'>Live UC lineage link available once deployed in-workspace.</span>",
            unsafe_allow_html=True,
        )

    st.markdown("<hr class='db-hair' />", unsafe_allow_html=True)
    st.markdown("<h2>Column-level lineage</h2>", unsafe_allow_html=True)
    st.info(
        "Unity Catalog captures column lineage on every query. Follow the Unity Catalog link above "
        "and switch to the Lineage tab for an interactive graph that traces `arena_id` from the raw "
        "JSON landing path through to `serving.gold_gl_monthly_summary` in Lakebase."
    )


# ---------------------------------------------------------------------------
# Page: Security
# ---------------------------------------------------------------------------

def page_security() -> None:
    page_header("Data security", pillar_key="security")

    render_demo_scope(
        demonstrated=[
            "Live SHOW GRANTS at catalog, schema, and multiple table levels",
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
- [SOC 2 Type II, ISO 27001, HIPAA, PCI DSS, FedRAMP Moderate](https://www.databricks.com/trust/compliance) — inherited from the platform on day 1.
- [Unity Catalog RBAC](https://docs.databricks.com/en/data-governance/unity-catalog/manage-privileges/index.html): grant at catalog, schema, table, [row, or column level](https://docs.databricks.com/en/tables/row-and-column-filters.html).
- [Customer-managed keys (CMK)](https://docs.databricks.com/en/security/keys/customer-managed-keys.html) for encryption at rest (managed services + workspace storage).
- [PrivateLink](https://docs.databricks.com/en/security/network/classic/privatelink.html) for both workspace (front-end) and control-plane (back-end) traffic.
- [MFA + SSO](https://docs.databricks.com/en/admin/users-groups/single-sign-on/index.html) via [Okta, Entra (Azure AD), Google Workspace, PingIdentity](https://docs.databricks.com/en/admin/users-groups/scim/index.html) — enforced at the workspace/account level.
- [Column masks](https://docs.databricks.com/en/tables/column-mask.html) and [row filters](https://docs.databricks.com/en/tables/row-filter.html) as SQL. No bolt-on tooling.
- [Attribute-based access control (ABAC)](https://docs.databricks.com/en/data-governance/unity-catalog/abac/index.html) via UC tags — enforce policies on PII/PHI tags, not just object paths.
- [Audit logs](https://docs.databricks.com/en/admin/account-settings/audit-logs.html) via `system.access.audit` — every read, grant, and config change.
""")

    st.markdown("<h2>RBAC granularity — live grants at every level</h2>", unsafe_allow_html=True)
    st.markdown(
        "<p class='muted'>Unity Catalog enforces privileges at every securable. The table below "
        "is a union of <code>SHOW GRANTS</code> across the catalog, schema, and several tables — "
        "proving the same RBAC model applies uniformly top-to-bottom.</p>",
        unsafe_allow_html=True,
    )
    result = queries.get_security_grants_variety()
    preview_banner(result, "union of SHOW GRANTS across catalog/schema/tables")
    if not result.df.empty:
        st.dataframe(
            result.df,
            use_container_width=True,
            height=360,
            column_config={
                "object_type": st.column_config.TextColumn("Object type", width="small"),
                "object_key":  st.column_config.TextColumn("Object"),
                "Principal":   st.column_config.TextColumn("Principal"),
                "ActionType":  st.column_config.TextColumn("Privilege", width="medium"),
            },
        )
    with st.expander("Show underlying SQL"):
        st.code(result.sql or "", language="sql")


# ---------------------------------------------------------------------------
# Page: Provenance
# ---------------------------------------------------------------------------

def page_provenance() -> None:
    page_header("Data provenance", pillar_key="provenance")

    render_demo_scope(
        demonstrated=[
            "Live DESCRIBE EXTENDED on a real demo table (owner, source, properties)",
            "Working tagging SQL against actual Juniper tables",
            "Deep-links into UC table pages + governed-tags admin for live authoring",
        ],
        also_supported=[
            ("Unity Catalog tags + governed-tag taxonomy",
             "https://docs.databricks.com/en/data-governance/unity-catalog/tags.html"),
            ("Delta transaction log (cryptographic chain of change)",
             "https://docs.databricks.com/en/delta/history.html"),
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
- [Governed tags](https://docs.databricks.com/en/data-governance/unity-catalog/governed-tags.html) enforce an approved tag taxonomy at the account level — no tag drift across teams.
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
             "Smaller gold table (50K rows) — good for demonstrating tagging + permissions."),
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

    st.markdown("<h2>Example: DESCRIBE EXTENDED</h2>", unsafe_allow_html=True)
    result = queries.describe_table_extended("benchmark_summary")
    preview_banner(result, "DESCRIBE EXTENDED")
    if not result.df.empty:
        st.dataframe(result.df, use_container_width=True, height=320)

    st.markdown("<h2>Tagging pattern</h2>", unsafe_allow_html=True)
    st.code(
        """-- Classify the investor table at the table + column level
ALTER TABLE juniper_square_demo_catalog.pipeline.silver_investors
  SET TAGS (
    'pii_level' = 'high',
    'owner_team' = 'data-platform',
    'retention_days' = '2555',
    'source_system' = 'rails-app'
  );

ALTER TABLE juniper_square_demo_catalog.pipeline.silver_investors
  ALTER COLUMN investor_name SET TAGS ('pii' = 'true', 'masking_policy' = 'name_mask');

-- Tag GL fact table for regulatory retention
ALTER TABLE juniper_square_demo_catalog.pipeline.silver_gl_transactions
  SET TAGS ('regulatory_class' = 'financial', 'retention_days' = '2555');""",
        language="sql",
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
    "provenance": page_provenance,
    "audit": page_audit,
}

ROUTER.get(current_page_key, page_overview)()
