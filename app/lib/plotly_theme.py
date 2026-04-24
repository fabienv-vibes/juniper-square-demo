"""Plotly template matching the Databricks Light brand.

Registers a `databricks` template and exposes the colorway. Figures built
via `lib.charts` apply the template; stage-transition colors (red/green on
the radar) are intentional category encoding and bypass the colorway.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio


# Brand palette — Lava first, Navy second, then supporting neutrals.
# Use sparingly — the 10% Lava rule still applies across the whole viewport.
BRAND_COLORWAY = [
    "#FF3621",  # Lava 600 — primary accent
    "#0B2026",  # Navy 900 — text, secondary series
    "#4462C9",  # Info blue
    "#00A972",  # Success green
    "#FBB300",  # Warning amber
    "#8E44AD",  # Deep purple
    "#E74C3C",  # Tomato (stage POC)
    "#16A085",  # Teal
]


# Semantic colors exposed for components that need brand-specific hues
# (e.g., the scorecard radar current-state vs Databricks trace).
BRAND = {
    "lava":        "#FF3621",
    "navy":        "#0B2026",
    "oat_light":   "#F9F7F4",
    "oat_medium":  "#EEEDE9",
    "white":       "#FFFFFF",
    "text":        "#0B2026",
    "text_muted":  "#4A5568",
    "border":      "#C4CCD6",
    "border_hair": "#E8E4DE",
    "tomato_500":  "#E74C3C",
    "green_500":   "#00A972",
    "info":        "#4462C9",
    "amber":       "#FBB300",
}


def get_brand_template() -> go.layout.Template:
    """Return the Databricks Light Plotly template."""
    return go.layout.Template(
        layout=dict(
            font=dict(
                family="DM Sans, system-ui, -apple-system, sans-serif",
                color=BRAND["text"],
                size=13,
            ),
            paper_bgcolor=BRAND["oat_light"],
            plot_bgcolor=BRAND["oat_light"],
            colorway=BRAND_COLORWAY,
            title=dict(
                font=dict(size=16, color=BRAND["text"], family="DM Sans, sans-serif"),
                x=0.02,
                xanchor="left",
                pad=dict(l=0, t=8, b=8),
            ),
            xaxis=dict(
                gridcolor=BRAND["border_hair"],
                linecolor=BRAND["border_hair"],
                zerolinecolor=BRAND["border_hair"],
                tickfont=dict(color=BRAND["text_muted"]),
                title=dict(font=dict(color=BRAND["text_muted"], size=12)),
            ),
            yaxis=dict(
                gridcolor=BRAND["border_hair"],
                linecolor=BRAND["border_hair"],
                zerolinecolor=BRAND["border_hair"],
                tickfont=dict(color=BRAND["text_muted"]),
                title=dict(font=dict(color=BRAND["text_muted"], size=12)),
            ),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                font=dict(color=BRAND["text"], size=12),
                borderwidth=0,
            ),
            margin=dict(l=48, r=24, t=56, b=48),
            hoverlabel=dict(
                bgcolor=BRAND["navy"],
                font=dict(color=BRAND["white"], family="DM Sans, sans-serif", size=12),
                bordercolor=BRAND["navy"],
            ),
        )
    )


def register_template(name: str = "databricks") -> str:
    """Register the template in Plotly's template store and set as default."""
    pio.templates[name] = get_brand_template()
    pio.templates.default = name
    return name
