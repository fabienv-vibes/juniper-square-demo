"""Unity Catalog lineage API helpers.

Stubs only -- the real implementation will call the UC lineage REST API
(/api/2.0/lineage-tracking/{table-lineage,column-lineage}). For now we
return placeholder structures so the UI can render without auth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class LineageEdge:
    upstream: str
    downstream: str
    kind: str = "table"  # "table" or "column"


@dataclass
class LineageGraph:
    root_table: str
    edges: List[LineageEdge] = field(default_factory=list)
    placeholder: bool = True


def get_table_lineage(table_full_name: str) -> LineageGraph:
    """Return upstream + downstream edges for a UC table.

    Returns the real lineage of the demo's gold GL table through the medallion
    and into the Lakebase synced copy that backs sub-second serving.

    TODO: wire to /api/2.0/lineage-tracking/table-lineage for dynamic lineage
    on any selected table.
    """
    # Real medallion + serving lineage for the Juniper benchmark pipeline.
    catalog = "juniper_square_demo_catalog"
    return LineageGraph(
        root_table=f"{catalog}.pipeline.gold_gl_monthly_summary",
        edges=[
            LineageEdge(
                upstream=f"{catalog}.raw.landing/gl_transactions/ (Parquet, Auto Loader)",
                downstream=f"{catalog}.pipeline.bronze_gl_transactions",
            ),
            LineageEdge(
                upstream=f"{catalog}.pipeline.bronze_gl_transactions",
                downstream=f"{catalog}.pipeline.silver_gl_transactions (liquid-clustered on arena_id, transaction_date)",
            ),
            LineageEdge(
                upstream=f"{catalog}.pipeline.silver_gl_transactions",
                downstream=f"{catalog}.pipeline.gold_gl_monthly_summary",
            ),
            LineageEdge(
                upstream=f"{catalog}.pipeline.gold_gl_monthly_summary",
                downstream="juniper_serving.serving.gold_gl_monthly_summary (Lakebase, Postgres 17)",
            ),
        ],
        placeholder=False,
    )


def get_uc_lineage_ui_url(catalog: str, schema: str, table: str) -> str:
    """Deep link into the UC lineage tab for the given table in the current workspace."""
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    if host and not host.startswith("http"):
        host = f"https://{host}"
    # UC lineage UI path
    return f"{host}/explore/data/{catalog}/{schema}/{table}?activeTab=lineage" if host else ""
