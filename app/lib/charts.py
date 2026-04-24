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

def build_latency_vs_concurrency(df: pd.DataFrame, metric: str = "p95_ms") -> go.Figure:
    """Line chart: latency (ms) vs concurrency, grouped by target, averaged across queries."""
    fig = go.Figure()
    if df.empty or metric not in df.columns:
        return _apply_base(fig, title=f"{metric} vs concurrency (no data)", height=420)

    agg = (
        df.groupby(["target", "concurrency"])[metric]
        .mean()
        .reset_index()
        .sort_values(["target", "concurrency"])
    )

    metric_label = {"p50_ms": "P50", "p95_ms": "P95", "p99_ms": "P99"}.get(metric, metric)

    for target, sub in agg.groupby("target"):
        display_vals = [_format_ms(v) for v in sub[metric]]
        fig.add_trace(go.Scatter(
            x=sub["concurrency"],
            y=sub[metric],
            mode="lines+markers",
            name=target,
            line=dict(color=TARGET_COLORS.get(target, BRAND["info"]), width=3),
            marker=dict(size=8),
            customdata=display_vals,
            hovertemplate=(
                f"<b>{target}</b><br>"
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

    fig.update_layout(
        xaxis=dict(title="Concurrency (parallel clients)"),
        yaxis=dict(title=f"{metric_label} latency", type="log"),
        height=420,
    )
    return _apply_base(fig, title=f"{metric_label} vs concurrency")


def build_throughput_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty or "throughput_qps" not in df.columns:
        return _apply_base(fig, title="Throughput (no data)", height=380)

    # Sum QPS across queries at each concurrency level (each query runs in its own
    # lane, so the aggregate is what the system actually sustains).
    agg = (
        df.groupby(["target", "concurrency"])["throughput_qps"]
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
        yaxis=dict(title="Queries / sec (total across all 6 query types)"),
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
    }
    sub = sub.copy()
    sub["query_label"] = sub["query_name"].map(lambda n: short_names.get(n, (n, ""))[0])
    sub["query_full"] = sub["query_name"].map(lambda n: short_names.get(n, ("", n))[1])

    # Stable Q-ordering so Q1..Q6 left-to-right regardless of groupby order
    order = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]

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
