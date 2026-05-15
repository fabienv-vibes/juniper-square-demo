"""Unity Catalog column lineage via REST API + UI deep-link helper.

Uses the `databricks-sdk` WorkspaceClient to hit
`/api/2.0/lineage-tracking/column-lineage`. The API returns column-level
upstreams that UC observes from real queries — anything shown here is derived
from the benchmark + SDP pipeline runs against this workspace.

Table-level lineage is rendered from a live UC screenshot on the Lineage page,
so there's no table-lineage API helper here anymore.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ColumnLineageEdge:
    upstream_table: str
    upstream_column: str
    downstream_table: str
    downstream_column: str


@dataclass
class ColumnLineage:
    root_table: str
    root_column: str
    upstreams: List[ColumnLineageEdge] = field(default_factory=list)
    source: str = "live"
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _workspace_client():
    """Return an authenticated WorkspaceClient, or None if unavailable.

    In the Databricks Apps runtime, DATABRICKS_HOST + CLIENT_ID + CLIENT_SECRET
    are injected. Locally, DATABRICKS_HOST + DATABRICKS_TOKEN works. If neither
    path yields an authenticated client, we fall back to the hardcoded graph.
    """
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    if not host:
        return None
    try:
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Column lineage
# ---------------------------------------------------------------------------

def get_column_lineage(table_full_name: str, column_name: str) -> ColumnLineage:
    """Return upstream column contributors for a specific column."""
    w = _workspace_client()
    if w is None:
        return ColumnLineage(
            root_table=table_full_name,
            root_column=column_name,
            source="fallback",
            error="no workspace client",
        )

    try:
        resp = w.api_client.do(
            "GET",
            "/api/2.0/lineage-tracking/column-lineage",
            query={
                "table_name": table_full_name,
                "column_name": column_name,
            },
        )
    except Exception as e:
        return ColumnLineage(
            root_table=table_full_name,
            root_column=column_name,
            source="fallback",
            error=str(e),
        )

    upstreams: List[ColumnLineageEdge] = []
    for up in (resp.get("upstream_cols") or resp.get("upstreams") or []):
        up_tbl = up.get("catalog_name", "") + "." + up.get("schema_name", "") + "." + up.get("table_name", "")
        up_tbl = up_tbl.strip(".")
        up_col = up.get("name") or up.get("column_name") or ""
        if up_tbl and up_col:
            upstreams.append(ColumnLineageEdge(
                upstream_table=up_tbl,
                upstream_column=up_col,
                downstream_table=table_full_name,
                downstream_column=column_name,
            ))

    return ColumnLineage(
        root_table=table_full_name,
        root_column=column_name,
        upstreams=upstreams,
        source="live" if upstreams else "fallback",
        error=None if upstreams else "empty live column lineage",
    )


# ---------------------------------------------------------------------------
# Deep-link helper
# ---------------------------------------------------------------------------

def get_uc_lineage_ui_url(catalog: str, schema: str, table: str) -> str:
    """Deep link into the UC lineage tab for the given table in the current workspace."""
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    if host and not host.startswith("http"):
        host = f"https://{host}"
    return f"{host}/explore/data/{catalog}/{schema}/{table}?activeTab=lineage" if host else ""
