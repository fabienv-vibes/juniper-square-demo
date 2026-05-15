"""Plotly figure builders for the Juniper Square benchmark app.

All charts render on the Databricks Light brand template. Stage-transition
colors on the radar (tomato for current, green for Databricks) are
intentional category encoding and bypass the general colorway.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

import pandas as pd
import plotly.graph_objects as go

from lib.plotly_theme import BRAND, register_template
from lib.scorecard import PILLARS, STAGE_COLORS, STAGE_VALUE, stage_value


# Register template + make it the default for every plotly.graph_objects.Figure
register_template("databricks")


# ---------------------------------------------------------------------------
# Target-specific series colors (semantic — not from the colorway)
# ---------------------------------------------------------------------------
TARGET_COLORS = {
    "lakebase": BRAND["green_500"],   # positive / best-path
    "dbsql":    BRAND["lava"],        # brand primary
    "redshift": BRAND["text_muted"],  # incumbent / neutral
}


def _apply_base(fig: go.Figure, title: Optional[str] = None, height: Optional[int] = None) -> go.Figure:
    """Apply brand template + title + larger hover label. Chart-specific axes stay."""
    layout: dict = {
        "template": "databricks",
        # Larger, less cramped hover labels. DM Sans matches the app chrome.
        "hoverlabel": dict(
            bgcolor=BRAND["white"],
            bordercolor=BRAND["border_hair"],
            font=dict(size=13, family="DM Sans", color=BRAND["text"]),
            namelength=-1,
        ),
    }
    if title is not None:
        layout["title"] = dict(text=title)
    if height is not None:
        layout["height"] = height
    fig.update_layout(**layout)
    return fig


def _format_ms(ms: float) -> str:
    """Latency pretty-printer for hovertemplates (matches app.py _fmt_latency)."""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:,.0f} ms"
    return f"{ms / 1000:.2f} s"


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> str:
    """Convert #RRGGBB to rgba(...) with the given alpha. Lightens a brand color
    while keeping the same hue — useful for P50 vs P95 series differentiation."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Scorecard radar
# ---------------------------------------------------------------------------

def build_scorecard_radar() -> go.Figure:
    """7-axis polar chart: single Databricks trace showing day-1 end state per pillar.

    Reaches the Scale ring (3) on most pillars and the Mature ring (4) on Security
    and Auditability. POC/MVP rings are rendered as gridlines only, no "current state"
    trace - we frame the visual as "where Databricks lands you", not a gap.
    """
    pillar_names = [p.name for p in PILLARS]
    dbx_vals = [stage_value(p.databricks_stage) for p in PILLARS]

    # Close the polygon
    pillar_names_closed = pillar_names + [pillar_names[0]]
    dbx_closed = dbx_vals + [dbx_vals[0]]

    fig = go.Figure()

    fig.add_trace(go.Scatterpolar(
        r=dbx_closed,
        theta=pillar_names_closed,
        mode="lines+markers",
        line=dict(color=BRAND["green_500"], width=3),
        marker=dict(size=10, color=BRAND["green_500"]),
        fill="toself",
        fillcolor="rgba(0, 169, 114, 0.18)",
        name="With Databricks on day 1",
        hovertemplate="<b>%{theta}</b><br>Day-1 stage: %{customdata}<extra></extra>",
        customdata=[p.databricks_stage for p in PILLARS] + [PILLARS[0].databricks_stage],
    ))

    fig.update_layout(
        template="databricks",
        polar=dict(
            bgcolor=BRAND["white"],
            radialaxis=dict(
                visible=True,
                range=[0, 4],
                tickmode="array",
                tickvals=[1, 2, 3, 4],
                ticktext=["POC", "MVP", "Scale", "Mature"],
                tickfont=dict(size=11, color=BRAND["text_muted"], family="DM Sans"),
                gridcolor=BRAND["border_hair"],
                linecolor=BRAND["border_hair"],
            ),
            angularaxis=dict(
                tickfont=dict(size=12, color=BRAND["text"], family="DM Sans"),
                gridcolor=BRAND["border_hair"],
                linecolor=BRAND["border_hair"],
            ),
        ),
        showlegend=False,
        # Extra l/r padding so "Data Latency" + "Data Provenance" labels don't clip.
        # Plotly bug: polar angularaxis labels extend beyond the plot area on wide text.
        margin=dict(l=110, r=110, t=48, b=56),
        height=520,
    )
    return fig


# ---------------------------------------------------------------------------
# Latency curves
# ---------------------------------------------------------------------------

def build_latency_vs_concurrency(
    df: pd.DataFrame,
    metric: str = "p95_ms",
    sustained_q7_metrics: Optional[dict] = None,
) -> go.Figure:
    """Line chart: latency (ms) vs concurrency, separating dashboard mix from worst-case Q7.

    sustained_q7_metrics: optional dict like {"p50_ms": 609, "p95_ms": 859, "p99_ms": 2043}
    from the most recent sustained Q7 run. When provided, draws a horizontal reference
    line for the currently-selected metric so the cold-burst spike is visually contrasted
    with steady-state Q7 cost.
    """
    fig = go.Figure()
    if df.empty or metric not in df.columns:
        return _apply_base(fig, title=f"{metric} vs concurrency (no data)", height=420)

    metric_label = {"p50_ms": "P50", "p95_ms": "P95", "p99_ms": "P99"}.get(metric, metric)

    # Split: dashboard queries (Q1-Q6) vs the worst-case Q7. Aggregating them
    # together would make the DBSQL line jump 10-50× at the concurrency levels
    # where Q7 is hitting autoscale lag — misleading for a dashboard SLO chart.
    worst_case_names = {"worst_case_yoy_growth"}
    dashboard_df = df[~df["query_name"].isin(worst_case_names)]
    worst_df = df[df["query_name"].isin(worst_case_names)]

    # Dashboard mix line (avg across Q1-Q6 per target)
    if not dashboard_df.empty:
        agg = (
            dashboard_df.groupby(["target", "concurrency"])[metric]
            .mean()
            .reset_index()
            .sort_values(["target", "concurrency"])
        )
        for target, sub in agg.groupby("target"):
            display_vals = [_format_ms(v) for v in sub[metric]]
            fig.add_trace(go.Scatter(
                x=sub["concurrency"],
                y=sub[metric],
                mode="lines+markers",
                name=f"{target} — dashboard mix (Q1–Q6)",
                line=dict(color=TARGET_COLORS.get(target, BRAND["info"]), width=3),
                marker=dict(size=8),
                customdata=display_vals,
                hovertemplate=(
                    f"<b>{target} dashboard mix</b><br>"
                    "Concurrency: %{x}<br>"
                    f"{metric_label}: %{{customdata}}"
                    "<extra></extra>"
                ),
            ))

    # Worst-case Q7 line (DBSQL only). Dashed to signal it's a different probe.
    if not worst_df.empty:
        agg_w = (
            worst_df.groupby(["target", "concurrency"])[metric]
            .mean()
            .reset_index()
            .sort_values(["target", "concurrency"])
        )
        for target, sub in agg_w.groupby("target"):
            display_vals = [_format_ms(v) for v in sub[metric]]
            fig.add_trace(go.Scatter(
                x=sub["concurrency"],
                y=sub[metric],
                mode="lines+markers",
                name=f"{target} — worst-case query",
                line=dict(color=BRAND["tomato_500"], width=3, dash="dot"),
                marker=dict(size=10, symbol="diamond"),
                customdata=display_vals,
                hovertemplate=(
                    f"<b>{target} worst-case query</b><br>"
                    "Concurrency: %{x}<br>"
                    f"{metric_label}: %{{customdata}}"
                    "<extra></extra>"
                ),
            ))

    # SLO guides at 4/5/7 s
    for slo_label, slo_ms, color in [
        ("P50 SLO 4s", 4000, BRAND["green_500"]),
        ("P95 SLO 5s", 5000, BRAND["amber"]),
        ("P99 SLO 7s", 7000, BRAND["tomato_500"]),
    ]:
        fig.add_hline(
            y=slo_ms,
            line=dict(color=color, width=1, dash="dot"),
            annotation_text=slo_label,
            annotation_position="top right",
            annotation_font=dict(color=color, size=10, family="DM Sans"),
        )

    # Sustained-Q7 steady-state reference line — contrasts the lockstep cold-burst spike
    # against the real Q7 cost when autoscale has settled (sustained 1 QPS, post-warmup).
    if sustained_q7_metrics and metric in sustained_q7_metrics:
        sus_val = sustained_q7_metrics[metric]
        fig.add_hline(
            y=sus_val,
            line=dict(color=BRAND["text"], width=2, dash="dashdot"),
            annotation_text=f"Worst-case query, sustained {metric_label} = {_format_ms(sus_val)} (steady-state)",
            annotation_position="bottom right",
            annotation_font=dict(color=BRAND["text"], size=11, family="DM Sans"),
        )

    # Make sure all measured concurrency levels appear on the x-axis even if
    # one trace has gaps (e.g. Q7 only on DBSQL).
    all_concurrency = sorted(df["concurrency"].unique())
    fig.update_layout(
        xaxis=dict(
            title="Concurrency (parallel clients)",
            type="category",
            categoryorder="array",
            categoryarray=[str(c) for c in all_concurrency],
        ),
        yaxis=dict(title=f"{metric_label} latency", type="log"),
        height=440,
    )
    # Cast x to strings so the categorical axis aligns
    for trace in fig.data:
        if hasattr(trace, "x") and trace.x is not None:
            trace.x = [str(c) for c in trace.x]
    return _apply_base(fig, title=f"{metric_label} vs concurrency — dashboard mix vs worst-case query")


def build_throughput_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty or "throughput_qps" not in df.columns:
        return _apply_base(fig, title="Throughput (no data)", height=380)

    # Aggregate dashboard-mix only (Q1-Q6); Q7 is a worst-case probe whose QPS
    # is naturally tiny and would distort the bar comparison.
    dashboard_df = df[~df["query_name"].isin(["worst_case_yoy_growth"])]
    agg = (
        dashboard_df.groupby(["target", "concurrency"])["throughput_qps"]
        .sum()
        .reset_index()
        .sort_values(["target", "concurrency"])
    )
    concurrency_levels = sorted(agg["concurrency"].unique())

    for target, sub in agg.groupby("target"):
        fig.add_trace(go.Bar(
            x=[str(c) for c in sub["concurrency"]],
            y=sub["throughput_qps"],
            name=target,
            marker_color=TARGET_COLORS.get(target, BRAND["info"]),
            hovertemplate=(
                f"<b>{target}</b><br>"
                "Concurrency: %{x}<br>"
                "Throughput: %{y:,.0f} QPS"
                "<extra></extra>"
            ),
        ))
    fig.update_layout(
        barmode="group",
        xaxis=dict(
            title="Concurrency",
            type="category",
            categoryorder="array",
            categoryarray=[str(c) for c in concurrency_levels],
        ),
        yaxis=dict(title="Queries / sec (sum across dashboard mix Q1–Q6)"),
        height=380,
    )
    return _apply_base(
        fig,
        title="Aggregate throughput as concurrency scales",
    )


def build_query_mix_chart(df: pd.DataFrame, metric: str = "p95_ms") -> go.Figure:
    """Per-query latency comparison at highest available concurrency."""
    fig = go.Figure()
    if df.empty or metric not in df.columns:
        return _apply_base(fig, title=f"Per-query {metric} (no data)", height=380)

    top_concurrency = df["concurrency"].max()
    sub = df[df["concurrency"] == top_concurrency]

    # Tight labels (just the Q#) keep the x-axis clean even in a narrow column.
    # The full name + category shows on hover.
    short_names = {
        "fund_performance_arena":       ("Q1", "fund performance"),
        "gl_monthly_by_arena_year":     ("Q2", "GL monthly rollup"),
        "property_financials_joined":   ("Q3", "property financials + arena join"),
        "top10_properties_by_revenue":  ("Q4", "top-10 properties"),
        "pnl_rollup_multi_month":       ("Q5", "P&L rollup (heaviest)"),
        "investor_commitment_summary":  ("Q6", "investor commitments"),
        "worst_case_yoy_growth":        ("Q7", "worst-case YoY growth (DBSQL only)"),
    }
    sub = sub.copy()
    sub["query_label"] = sub["query_name"].map(lambda n: short_names.get(n, (n, ""))[0])
    sub["query_full"] = sub["query_name"].map(lambda n: short_names.get(n, ("", n))[1])

    # Stable Q-ordering so Q1..Q7 left-to-right regardless of groupby order
    order = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]

    metric_label = {"p50_ms": "P50", "p95_ms": "P95", "p99_ms": "P99"}.get(metric, metric)

    for target, s in sub.groupby("target"):
        s = s.sort_values("query_label", key=lambda col: col.map(lambda v: order.index(v) if v in order else 999))
        display_vals = [_format_ms(v) for v in s[metric]]
        fig.add_trace(go.Bar(
            x=s["query_label"],
            y=s[metric],
            name=target,
            marker_color=TARGET_COLORS.get(target, BRAND["info"]),
            customdata=list(zip(display_vals, s["query_full"])),
            hovertemplate=(
                f"<b>{target}</b><br>"
                "%{x} — %{customdata[1]}<br>"
                f"{metric_label}: %{{customdata[0]}}"
                "<extra></extra>"
            ),
        ))
    fig.update_layout(
        barmode="group",
        xaxis=dict(
            title="Query",
            type="category",
            categoryorder="array",
            categoryarray=order,
            tickangle=0,
        ),
        yaxis=dict(title=f"{metric_label} latency", type="log"),
        height=400,
        margin=dict(b=60),
    )
    return _apply_base(fig, title=f"Per-query {metric_label} at concurrency {top_concurrency}")


# ---------------------------------------------------------------------------
# Cost projection
# ---------------------------------------------------------------------------

def build_cost_projection(
    cost_per_query: float,
    queries_per_arena_per_month: int = 5000,
    arenas: Iterable[int] = (1000, 5000, 10000),
) -> go.Figure:
    """Bar: projected monthly cost by arena count at fixed query volume per arena."""
    arenas_list: List[int] = list(arenas)
    monthly_costs = [a * queries_per_arena_per_month * cost_per_query for a in arenas_list]

    # Tonal ramp through the brand — all in the same family, not 3 wild hues
    tier_colors = [BRAND["info"], BRAND["navy"], BRAND["lava"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[f"{a:,} arenas" for a in arenas_list],
        y=monthly_costs,
        marker_color=tier_colors[: len(arenas_list)],
        text=[f"${c:,.0f}/mo" for c in monthly_costs],
        textposition="outside",
        textfont=dict(family="DM Sans", color=BRAND["text"]),
    ))
    fig.update_layout(
        xaxis=dict(title="Scale tier"),
        yaxis=dict(title="Monthly cost (USD)"),
        height=380,
        showlegend=False,
    )
    return _apply_base(fig, title=f"Projected monthly cost at {queries_per_arena_per_month:,} qpm per arena")


def build_cost_breakeven(
    dbsql_cost_per_query: float,
    lakebase_monthly_fixed: float,
    max_queries_per_month: int = 100_000_000,
) -> go.Figure:
    """Line chart: DBSQL (linear) vs Lakebase (flat) monthly cost as queries/month grows.

    The crossover is where Lakebase's provisioned cost equals DBSQL's variable cost —
    below, DBSQL wins; above, Lakebase wins.
    """
    # Log-spaced x axis so small + huge volumes both readable
    import numpy as np
    xs = np.logspace(3, np.log10(max_queries_per_month), 60)
    dbsql_y = xs * dbsql_cost_per_query
    lakebase_y = [lakebase_monthly_fixed] * len(xs)

    # Crossover point
    breakeven_qpm = lakebase_monthly_fixed / dbsql_cost_per_query if dbsql_cost_per_query > 0 else 0

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=dbsql_y, mode="lines", name="DBSQL (variable)",
        line=dict(color=TARGET_COLORS["dbsql"], width=3),
        hovertemplate="DBSQL<br>Queries/mo: %{x:,.0f}<br>Cost: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=lakebase_y, mode="lines", name="Lakebase (fixed)",
        line=dict(color=TARGET_COLORS["lakebase"], width=3),
        hovertemplate="Lakebase<br>Queries/mo: %{x:,.0f}<br>Cost: $%{y:,.0f}<extra></extra>",
    ))
    if breakeven_qpm > 0:
        fig.add_vline(
            x=breakeven_qpm,
            line=dict(color=BRAND["text_muted"], width=1, dash="dot"),
            annotation_text=f"Break-even ~{breakeven_qpm:,.0f} queries/mo",
            annotation_position="top",
            annotation_font=dict(color=BRAND["text"], size=12, family="DM Sans"),
        )
    fig.update_layout(
        xaxis=dict(title="Queries per month", type="log"),
        yaxis=dict(title="Monthly cost (USD)", type="log"),
        height=420,
    )
    return _apply_base(fig, title="DBSQL vs Lakebase — where each wins")


# ---------------------------------------------------------------------------
# Sustained-mode charts (rate-paced runs, time-series visualizations)
# ---------------------------------------------------------------------------

def build_latency_timeseries(buckets_df: pd.DataFrame) -> go.Figure:
    """P50/P95 latency over time, MEASUREMENT WINDOW ONLY.

    Warmup buckets are excluded so cold-start spikes (which can be 100×+ steady-state
    on heavy queries like Q7) don't crush the y-axis scale. The cold-start ramp is
    rendered separately by `build_warmup_ramp` in a dedicated expander.

    SLO guides at 4000/5000/7000 ms.
    """
    fig = go.Figure()
    if buckets_df is None or buckets_df.empty:
        return _apply_base(fig, title="Latency over time — no data")

    meas_df = buckets_df[buckets_df["is_warmup"] == False]
    if meas_df.empty:
        return _apply_base(fig, title="Latency over time — no measurement-window data")

    # Same hue per target, two intensities per percentile so P95 (the SLO line)
    # pops while P50 (steady-state median) sits behind it.
    #   P95 → full saturation, thicker line, filled marker
    #   P50 → ~45% alpha rgba, thinner line, hollow marker
    targets = sorted(meas_df["target"].dropna().unique())
    for target in targets:
        base_hex = TARGET_COLORS.get(target, BRAND["lava"])
        for pct, label, width, alpha, marker_symbol in [
            ("p50_ms", "P50", 2, 0.45, "circle-open"),
            ("p95_ms", "P95", 3, 1.00, "circle"),
        ]:
            sub = meas_df[meas_df["target"] == target]
            if sub.empty:
                continue
            color = _hex_to_rgba(base_hex, alpha)
            fig.add_trace(go.Scatter(
                x=sub["bucket_start_offset_s"], y=sub[pct],
                mode="lines+markers",
                name=f"{target} {label}",
                line=dict(color=color, width=width),
                marker=dict(size=7, color=color, symbol=marker_symbol,
                            line=dict(width=1.5, color=base_hex)),
                legendgroup=f"{target}-{label}",
                hovertemplate=f"{target} {label}<br>t=%{{x}}s<br>%{{y:.0f}} ms<extra></extra>",
            ))

    # SLO guide lines
    for slo_ms, lbl in [(4000, "P50 SLO"), (5000, "P95 SLO"), (7000, "P99 SLO")]:
        fig.add_hline(
            y=slo_ms,
            line=dict(color=BRAND["text_muted"], width=1, dash="dot"),
            annotation_text=f"{lbl} ({slo_ms / 1000:.0f}s)",
            annotation_position="right",
            annotation_font=dict(color=BRAND["text_muted"], size=10, family="DM Sans"),
        )

    fig.update_layout(
        xaxis=dict(title="Elapsed seconds in run (post-warmup)"),
        yaxis=dict(title="Latency (ms)", rangemode="tozero"),
        height=420,
    )
    return _apply_base(fig, title="Latency over time — steady-state measurement window")


def build_qps_timeseries(buckets_df: pd.DataFrame, target_rate: Optional[float] = None) -> go.Figure:
    """Achieved QPS over time. If target_rate is provided, draws a horizontal target line.

    Coordinated-omission canary: if achieved < target, the warehouse throttled.
    """
    fig = go.Figure()
    if buckets_df is None or buckets_df.empty:
        return _apply_base(fig, title="Achieved QPS over time — no data")

    targets = sorted(buckets_df["target"].dropna().unique())
    for target in targets:
        warm = buckets_df[(buckets_df["target"] == target) & (buckets_df["is_warmup"] == True)]
        meas = buckets_df[(buckets_df["target"] == target) & (buckets_df["is_warmup"] == False)]
        if not warm.empty:
            fig.add_trace(go.Scatter(
                x=warm["bucket_start_offset_s"], y=warm["achieved_qps"],
                mode="lines+markers",
                name=f"{target} (warmup)",
                line=dict(color=TARGET_COLORS.get(target, BRAND["lava"]), width=2, dash="dash"),
                marker=dict(size=6, symbol="circle-open"),
                legendgroup=target, showlegend=False,
                hovertemplate=f"{target}<br>t=%{{x}}s<br>%{{y:.2f}} QPS<extra>warmup</extra>",
            ))
        if not meas.empty:
            fig.add_trace(go.Scatter(
                x=meas["bucket_start_offset_s"], y=meas["achieved_qps"],
                mode="lines+markers",
                name=target,
                line=dict(color=TARGET_COLORS.get(target, BRAND["lava"]), width=3),
                marker=dict(size=6),
                legendgroup=target,
                hovertemplate=f"{target}<br>t=%{{x}}s<br>%{{y:.2f}} QPS<extra></extra>",
            ))

    if target_rate is not None:
        fig.add_hline(
            y=target_rate,
            line=dict(color=BRAND["green_500"], width=2, dash="dot"),
            annotation_text=f"Target {target_rate:.0f} QPS",
            annotation_position="right",
            annotation_font=dict(color=BRAND["green_500"], size=11, family="DM Sans"),
        )

    fig.update_layout(
        xaxis=dict(title="Elapsed seconds in run"),
        yaxis=dict(title="Achieved QPS", rangemode="tozero"),
        height=380,
    )
    return _apply_base(fig, title="Achieved QPS over time — kept pace with target?")


def build_latency_cdf(samples_df: pd.DataFrame) -> go.Figure:
    """Cumulative distribution of post-warmup latencies, one line per target.

    Reveals where the tail starts. SLO guides at 4/5/7 sec.
    """
    fig = go.Figure()
    if samples_df is None or samples_df.empty:
        return _apply_base(fig, title="Latency distribution — no data")

    for target in sorted(samples_df["target"].dropna().unique()):
        sub = samples_df[samples_df["target"] == target].copy()
        sub = sub.sort_values("total_latency_ms").reset_index(drop=True)
        if sub.empty:
            continue
        n = len(sub)
        sub["cdf"] = (sub.index + 1) / n * 100
        fig.add_trace(go.Scatter(
            x=sub["total_latency_ms"], y=sub["cdf"],
            mode="lines",
            name=target,
            line=dict(color=TARGET_COLORS.get(target, BRAND["lava"]), width=3),
            hovertemplate=f"{target}<br>%{{y:.1f}}%% < %{{x:.0f}} ms<extra></extra>",
        ))

    for slo_ms, lbl in [(4000, "P50 SLO"), (5000, "P95 SLO"), (7000, "P99 SLO")]:
        fig.add_vline(
            x=slo_ms,
            line=dict(color=BRAND["text_muted"], width=1, dash="dot"),
            annotation_text=lbl,
            annotation_position="top",
            annotation_font=dict(color=BRAND["text_muted"], size=10, family="DM Sans"),
        )

    fig.update_layout(
        xaxis=dict(title="Total latency (ms)", type="log"),
        yaxis=dict(title="Cumulative % of queries", range=[0, 100]),
        height=380,
    )
    return _apply_base(fig, title="Latency CDF — where the tail begins")


def build_warmup_ramp(warmup_df: pd.DataFrame) -> go.Figure:
    """Cold-start ramp: scatter of latency vs elapsed time during warmup.

    The shape tells the IWM story — fast ramp to steady-state means predictive
    provisioning kicked in. A long flat-then-drop means autoscale lag.
    """
    fig = go.Figure()
    if warmup_df is None or warmup_df.empty:
        return _apply_base(fig, title="Cold-start ramp — no warmup data")

    for target in sorted(warmup_df["target"].dropna().unique()):
        sub = warmup_df[(warmup_df["target"] == target) & (warmup_df["success"] == True)].copy()
        if sub.empty:
            continue
        sub["elapsed_s"] = sub["actual_start_offset_ms"] / 1000.0
        fig.add_trace(go.Scatter(
            x=sub["elapsed_s"], y=sub["total_latency_ms"],
            mode="markers",
            name=target,
            marker=dict(
                color=TARGET_COLORS.get(target, BRAND["lava"]),
                size=8, opacity=0.7,
                line=dict(width=1, color=BRAND["white"]),
            ),
            hovertemplate=f"{target}<br>t=%{{x:.1f}}s<br>%{{y:.0f}} ms<extra></extra>",
        ))

    fig.update_layout(
        xaxis=dict(title="Elapsed seconds (warmup window)"),
        yaxis=dict(title="Total latency (ms)", rangemode="tozero"),
        height=320,
    )
    return _apply_base(fig, title="Cold-start ramp — IWM provisioning behavior")
