"""Global theme injection for the Juniper Square benchmark app.

Loads DM Sans + DM Mono from Google Fonts, applies Databricks brand tokens
(Lava 600, Navy 900, Oat Light), and overrides Streamlit defaults so the
page renders on the locked Light theme. Call `inject_theme()` once, as
early as possible in the entrypoint.
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Brand tokens + CSS
# ---------------------------------------------------------------------------

BRAND_CSS = """
<style>
  /* ----------------------------------------------------------------------
     Databricks brand tokens (source: brand.databricks.com)
     ---------------------------------------------------------------------- */
  @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,wght@0,400;0,500;0,700;1,400&family=DM+Mono:wght@400;500&display=swap');

  :root {
    --db-lava-600:     #FF3621;
    --db-tomato-500:   #E74C3C;
    --db-green-500:    #00A972;
    --db-navy-900:     #0B2026;
    --db-oat-medium:   #EEEDE9;
    --db-oat-light:    #F9F7F4;
    --db-white:        #FFFFFF;

    --db-success:      #00A972;
    --db-warning:      #FBB300;
    --db-error:        #FF3621;
    --db-info:         #4462C9;

    --db-neutral-900:  #0B2026;
    --db-neutral-700:  #4A5568;
    --db-neutral-500:  #A0AEC0;
    --db-neutral-300:  #CBD5E0;
    --db-neutral-100:  #EEEDE9;
    --db-neutral-50:   #F9F7F4;
    --db-border:       #C4CCD6;
    --db-border-hair:  #E8E4DE;

    --db-bg:           var(--db-oat-light);
    --db-bg-subtle:    var(--db-oat-medium);
    --db-surface:      var(--db-white);
    --db-text-primary: var(--db-navy-900);
    --db-text-secondary:#4A5568;

    --db-font-primary: 'DM Sans', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    --db-font-mono:    'DM Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;

    --db-text-h1:      3rem;
    --db-text-h2:      2.25rem;
    --db-text-h3:      1.5rem;
    --db-text-h4:      1.125rem;
    --db-text-body:    1rem;
    --db-text-small:   0.875rem;

    --db-space-xs:     4px;
    --db-space-sm:     8px;
    --db-space-md:     16px;
    --db-space-lg:     24px;
    --db-space-xl:     32px;
    --db-space-2xl:    48px;
    --db-space-3xl:    64px;

    --db-radius-sm:    4px;
    --db-radius-md:    8px;
    --db-radius-lg:    12px;
    --db-radius-card:  12px;
    --db-radius-full:  9999px;

    --db-shadow-sm:    0 1px 2px 0 rgba(11, 32, 38, 0.05);
    --db-shadow-md:    0 4px 6px -1px rgba(11, 32, 38, 0.08);
    --db-shadow-lg:    0 10px 15px -3px rgba(11, 32, 38, 0.08);

    --db-duration-fast:  150ms;
    --db-duration-state: 250ms;
    --db-ease-quart:     cubic-bezier(0.25, 1, 0.5, 1);
  }

  /* ----------------------------------------------------------------------
     Font application — catch Streamlit's many generated class names
     ---------------------------------------------------------------------- */
  html, body, [class*="css"], [class*="st-"] {
    font-family: var(--db-font-primary) !important;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }

  /* Material Symbols icons (sidebar collapse, dropdown chevrons, etc.) must
     keep their icon font; the global rule above clobbers them otherwise and
     they render as literal text like "keyboard_double_arrow_left". */
  [data-testid="stIconMaterial"],
  span[data-testid="stIconMaterial"] {
    font-family: "Material Symbols Rounded" !important;
  }

  /* ----------------------------------------------------------------------
     App shell — Light theme
     ---------------------------------------------------------------------- */
  .stApp, [data-testid="stAppViewContainer"] {
    background-color: var(--db-bg) !important;
    color: var(--db-text-primary);
  }

  [data-testid="stHeader"] {
    background-color: transparent;
  }

  .main .block-container,
  [data-testid="stMainBlockContainer"] {
    padding-top: var(--db-space-lg);
    padding-bottom: var(--db-space-3xl);
    max-width: 1400px;
  }

  /* ----------------------------------------------------------------------
     Sidebar
     ---------------------------------------------------------------------- */
  [data-testid="stSidebar"] {
    background-color: var(--db-white) !important;
    border-right: 1px solid var(--db-border-hair);
  }
  [data-testid="stSidebar"] * {
    color: var(--db-text-primary);
  }
  [data-testid="stSidebar"] code {
    background: var(--db-oat-medium);
    color: var(--db-navy-900);
    padding: 1px 6px;
    border-radius: var(--db-radius-sm);
    font-family: var(--db-font-mono);
  }

  /* ----------------------------------------------------------------------
     Typography
     ---------------------------------------------------------------------- */
  h1, h2, h3, h4, h5, h6,
  [data-testid="stMarkdownContainer"] h1,
  [data-testid="stMarkdownContainer"] h2,
  [data-testid="stMarkdownContainer"] h3,
  [data-testid="stMarkdownContainer"] h4 {
    color: var(--db-text-primary) !important;
    font-family: var(--db-font-primary) !important;
    letter-spacing: -0.01em;
  }
  h1, [data-testid="stMarkdownContainer"] h1 { font-weight: 700; font-size: var(--db-text-h2); margin-top: var(--db-space-md); }
  h2, [data-testid="stMarkdownContainer"] h2 {
    font-weight: 700;
    font-size: var(--db-text-h3);
    margin-top: var(--db-space-2xl);
    margin-bottom: var(--db-space-md);
  }
  h3, [data-testid="stMarkdownContainer"] h3 {
    font-weight: 700;
    font-size: 1.25rem;
    margin-top: var(--db-space-xl);
    margin-bottom: var(--db-space-sm);
  }

  p, li, span, label {
    color: var(--db-text-primary);
  }

  a {
    color: var(--db-lava-600);
    text-decoration: none;
    transition: color var(--db-duration-fast) var(--db-ease-quart);
  }
  a:hover { text-decoration: underline; }
  a:focus-visible {
    outline: 2px solid var(--db-lava-600);
    outline-offset: 2px;
    border-radius: var(--db-radius-sm);
  }

  code, pre, kbd, samp {
    font-family: var(--db-font-mono) !important;
    font-size: 0.875rem;
  }

  /* ----------------------------------------------------------------------
     Tabular numerals on metrics + dataframes
     ---------------------------------------------------------------------- */
  [data-testid="stMetricValue"],
  [data-testid="stMetricDelta"],
  [data-testid="stDataFrame"] td,
  [data-testid="stTable"] td,
  .stNumberInput input {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum";
  }

  /* Streamlit metric polish */
  [data-testid="stMetric"] {
    background: var(--db-white);
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-md);
    padding: var(--db-space-md);
    box-shadow: var(--db-shadow-sm);
  }
  [data-testid="stMetricLabel"] {
    color: var(--db-text-secondary) !important;
    font-size: var(--db-text-small);
    font-weight: 500;
  }
  [data-testid="stMetricValue"] {
    color: var(--db-text-primary) !important;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  /* ----------------------------------------------------------------------
     Dataframes + tables
     ---------------------------------------------------------------------- */
  [data-testid="stDataFrame"] {
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-md);
    overflow: hidden;
  }
  [data-testid="stDataFrame"] thead tr th {
    background-color: var(--db-oat-medium) !important;
    color: var(--db-text-primary) !important;
    font-weight: 500;
  }

  /* ----------------------------------------------------------------------
     Inputs, selects, radios, sliders
     ---------------------------------------------------------------------- */
  [data-testid="stRadio"] label,
  [data-testid="stSelectbox"] label,
  [data-testid="stSlider"] label,
  [data-testid="stNumberInput"] label,
  [data-testid="stTextInput"] label {
    color: var(--db-text-primary) !important;
    font-weight: 500;
    font-size: var(--db-text-small);
  }

  .stRadio > div { gap: var(--db-space-xs); }
  .stRadio label {
    padding: var(--db-space-sm) var(--db-space-md);
    border-radius: var(--db-radius-md);
    transition: background var(--db-duration-fast) var(--db-ease-quart);
  }
  .stRadio label:hover { background: var(--db-oat-medium); }

  button, .stButton > button, .stDownloadButton > button {
    font-family: var(--db-font-primary) !important;
    border-radius: var(--db-radius-md);
    font-weight: 500;
    transition: all var(--db-duration-fast) var(--db-ease-quart);
  }

  /* Focus ring — accessible, 2px offset */
  button:focus-visible,
  input:focus-visible,
  select:focus-visible,
  [role="radiogroup"] label:focus-within,
  [data-testid="stRadio"] label:focus-within {
    outline: 2px solid var(--db-lava-600);
    outline-offset: 2px;
  }

  /* ----------------------------------------------------------------------
     Expanders + info boxes
     ---------------------------------------------------------------------- */
  [data-testid="stExpander"] {
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-md);
    background: var(--db-white);
  }
  [data-testid="stExpander"] summary {
    color: var(--db-text-primary);
    font-weight: 500;
  }

  /* Info / alert boxes — keep them restrained */
  [data-testid="stAlert"] {
    border-radius: var(--db-radius-md);
    border: 1px solid var(--db-border-hair);
  }

  /* ----------------------------------------------------------------------
     Pillar cards + stage chips + preview banner
     ---------------------------------------------------------------------- */
  .db-header {
    display: flex;
    align-items: center;
    gap: var(--db-space-md);
    margin-bottom: var(--db-space-sm);
  }
  .db-header img { display: block; }

  .pillar-card {
    background: var(--db-white);
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-card);
    padding: var(--db-space-lg);
    min-height: 200px;
    display: flex;
    flex-direction: column;
    gap: var(--db-space-sm);
    box-shadow: var(--db-shadow-sm);
    transition: box-shadow var(--db-duration-state) var(--db-ease-quart);
  }
  .pillar-card:hover { box-shadow: var(--db-shadow-md); }
  .pillar-card h4 {
    margin: 0;
    color: var(--db-text-primary);
    font-size: var(--db-text-h4);
    font-weight: 700;
  }
  .pillar-card .proof {
    color: var(--db-text-secondary);
    font-size: var(--db-text-small);
    line-height: 1.5;
    margin: 0;
  }
  .pillar-card .path-to-mature {
    color: var(--db-text-secondary);
    font-size: calc(var(--db-text-small) * 0.92);
    line-height: 1.5;
    margin: 0;
    padding-top: var(--db-space-sm);
    border-top: 1px dashed var(--db-border-hair);
    flex: 1;
  }
  .pillar-card .path-to-mature .path-label {
    color: var(--db-text-primary);
    font-weight: 600;
    margin-right: 4px;
  }
  .pillar-card {
    min-height: 260px;
  }
  /* Path-to-Mature callout — full hairline border + tinted bg, no side-stripe */
  .path-callout {
    background: rgba(255, 54, 33, 0.04);
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-card);
    padding: var(--db-space-md) var(--db-space-lg);
    margin: var(--db-space-md) 0 var(--db-space-lg) 0;
    font-size: var(--db-text-small);
    line-height: 1.55;
    color: var(--db-text-secondary);
  }
  .path-callout-label {
    color: var(--db-text-primary);
    font-weight: 600;
    margin-right: 4px;
  }
  /* Success callout — tinted green bg + hairline border, same structure as .path-callout */
  .db-callout {
    border: 1px solid var(--db-border-hair);
    border-radius: var(--db-radius-card);
    padding: var(--db-space-md) var(--db-space-lg);
    margin: var(--db-space-md) 0 var(--db-space-lg) 0;
    font-size: var(--db-text-small);
    line-height: 1.55;
    color: var(--db-text-secondary);
  }
  .db-callout strong {
    color: var(--db-text-primary);
  }
  .db-callout--success {
    background: rgba(0, 169, 114, 0.06);
  }
  .pillar-card .drill {
    margin-top: var(--db-space-sm);
    font-size: var(--db-text-small);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .pillar-card .drill a {
    color: var(--db-lava-600);
    font-weight: 500;
  }
  .pillar-card .live-dot {
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--db-success);
    margin-right: var(--db-space-xs);
    vertical-align: middle;
  }

  /* Stage chips — category encoding, exempt from 10% Lava rule */
  .stage-chip {
    display: inline-block;
    padding: 2px 10px;
    border-radius: var(--db-radius-full);
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--db-navy-900);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.01em;
  }
  .stage-chip.small { font-size: 0.7rem; padding: 2px 8px; }
  .stage-arrow {
    color: var(--db-text-secondary);
    font-weight: 500;
    margin: 0 var(--db-space-xs);
  }

  /* Preview-mode banner — restrained amber, not red */
  .preview-banner {
    background: rgba(251, 179, 0, 0.08);
    border: 1px solid rgba(251, 179, 0, 0.35);
    color: var(--db-navy-900);
    padding: var(--db-space-sm) var(--db-space-md);
    border-radius: var(--db-radius-md);
    font-size: var(--db-text-small);
    margin-bottom: var(--db-space-md);
    display: flex;
    align-items: center;
    gap: var(--db-space-sm);
  }
  .preview-banner b { color: var(--db-navy-900); }
  .preview-banner .muted { color: var(--db-text-secondary); }

  /* Secondary text */
  .muted {
    color: var(--db-text-secondary);
    font-size: var(--db-text-small);
  }
  .dbx-accent { color: var(--db-lava-600); font-weight: 600; }

  /* Subtle hairline divider in place of <hr> inside cards */
  .db-hair {
    border: none;
    height: 1px;
    background: var(--db-border-hair);
    margin: var(--db-space-xl) 0;
  }

  /* Prefers-reduced-motion */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>
"""


def inject_theme(
    page_title: str = "Juniper Square x Databricks",
    page_icon: str = "assets/databricks.svg",
    layout: str = "wide",
) -> None:
    """Set page config + inject the Databricks brand CSS.

    Call this once, before any other st.* call that emits markup.
    """
    st.set_page_config(
        page_title=page_title,
        page_icon=page_icon,
        layout=layout,
        initial_sidebar_state="expanded",
    )
    st.markdown(BRAND_CSS, unsafe_allow_html=True)
