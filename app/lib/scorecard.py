"""7-pillar maturity scorecard for the Juniper Square platform evaluation.

Each pillar has 4 maturity stages (POC -> MVP -> Scale -> Mature) with
- current_stage: our soft assessment of where Juniper is today (labeled "correct me" in UI)
- databricks_stage: where Databricks puts them on day 1
- proof_point: one-sentence capability statement
- has_live_drill_in: whether the app has a live-data drill-in page for this pillar
"""

from dataclasses import dataclass, field
from typing import Dict, List


STAGES = ["POC", "MVP", "Scale", "Mature"]

# Numeric value per stage for radar chart radial axis (1-4)
STAGE_VALUE: Dict[str, int] = {"POC": 1, "MVP": 2, "Scale": 3, "Mature": 4}

# Stage color palette (also used for pillar-card chips)
STAGE_COLORS: Dict[str, str] = {
    "POC": "#E74C3C",      # red
    "MVP": "#F39C12",      # orange
    "Scale": "#3498DB",    # blue
    "Mature": "#2ECC71",   # green
}


@dataclass
class Pillar:
    key: str
    name: str
    icon: str
    stage_descriptions: Dict[str, str]
    databricks_stage: str          # end-state Databricks lands you at on day 1
    proof_point: str               # what the demo shows
    path_to_mature: str            # how to reach Mature for pillars that land at Scale; sustain story for pillars already at Mature
    path_to_mature_title: str      # short label for the path (e.g. "Path to Mature" or "Sustaining Mature")
    has_live_drill_in: bool
    drill_page: str = ""           # streamlit page key for the drill-in


PILLARS: List[Pillar] = [
    Pillar(
        key="latency",
        name="Data Latency",
        icon="zap",
        stage_descriptions={
            "POC": ">2 days to load from source",
            "MVP": "1-2 days (daily batch)",
            "Scale": "2-24 hours (hourly / stream micro-batches)",
            "Mature": "<2 hours (real-time, CDC)",
        },
        databricks_stage="Scale",
        proof_point=(
            "Medallion architecture turns the 500-line fund roll-up into a "
            "25-line gold SELECT (22× faster), with micro-batch ingest in minutes."
        ),
        path_to_mature_title="Path to Mature",
        path_to_mature=(
            "Real-time CDC via Lakeflow Connect (Postgres, Oracle, MySQL source "
            "connectors) and Structured Streaming on Kafka or Kinesis. The 1-minute "
            "microbatch SDP pipeline behind this demo is the same streaming primitive."
        ),
        has_live_drill_in=True,
        drill_page="latency",
    ),
    Pillar(
        key="klo",
        name="Keeping the Lights On",
        icon="tool",
        stage_descriptions={
            "POC": ">35% dev hrs/mo on maintenance",
            "MVP": "20-35% dev hrs/mo",
            "Scale": "10-20% dev hrs/mo",
            "Mature": "<10% (self-healing pipelines)",
        },
        databricks_stage="Scale",
        proof_point=(
            "Serverless autoscale + Spark Declarative Pipelines cut pipeline "
            "ops work: no cluster sizing, no node-pool babysitting."
        ),
        path_to_mature_title="Path to Mature",
        path_to_mature=(
            "Predictive Optimization auto-tunes VACUUM and clustering; system.lakeflow.* "
            "system tables plus Databricks Alerts catch drift before a pager fires. "
            "This demo already runs serverless across SDP and DBSQL."
        ),
        has_live_drill_in=True,
        drill_page="klo",
    ),
    Pillar(
        key="lineage",
        name="Data Lineage",
        icon="share-2",
        stage_descriptions={
            "POC": "No lineage",
            "MVP": "Spreadsheet / wiki docs",
            "Scale": "Automated table + column tracking, impact analysis",
            "Mature": "Full E2E with snapshots, time-travel, customer-facing portals",
        },
        databricks_stage="Scale",
        proof_point=(
            "Unity Catalog captures table + column lineage automatically "
            "on every query, with time-travel snapshots via Delta."
        ),
        path_to_mature_title="Path to Mature",
        path_to_mature=(
            "Customer-facing lineage portals build on the Unity Catalog Lineage REST "
            "API; Delta Time Travel provides historical views. Extending the UC graph "
            "in this demo to an investor-facing portal is small application work."
        ),
        has_live_drill_in=True,
        drill_page="lineage",
    ),
    Pillar(
        key="provenance",
        name="Data Provenance",
        icon="clipboard",
        stage_descriptions={
            "POC": "No provenance tracking",
            "MVP": "Basic source labels + owners",
            "Scale": "Detailed metadata + PII tags + automated capture",
            "Mature": "Full chain of custody, GDPR/CCPA automation",
        },
        databricks_stage="Scale",
        proof_point=(
            "UC tags + column comments + system.access.audit give a "
            "queryable chain-of-custody without custom tooling."
        ),
        path_to_mature_title="Path to Mature",
        path_to_mature=(
            "UC Tags encode PII, GDPR, and CCPA classifications; Dynamic Views "
            "automate compliance masking; the Delta transaction log is a cryptographic "
            "chain-of-custody. The patterns are available today; workflow automation "
            "is the glue layer on top."
        ),
        has_live_drill_in=True,
        drill_page="provenance",
    ),
    Pillar(
        key="security",
        name="Data Security",
        icon="shield",
        stage_descriptions={
            "POC": "Shared admin pw, no encryption",
            "MVP": "Individual accounts + encryption",
            "Scale": "RBAC + SIEM + MFA",
            "Mature": "SOC 2 Type II + zero-trust + DLP + ISO 27001",
        },
        databricks_stage="Mature",
        proof_point=(
            "Databricks is SOC 2 Type II + ISO 27001, with UC-enforced "
            "row/column-level security and customer-managed keys."
        ),
        path_to_mature_title="Sustaining Mature",
        path_to_mature=(
            "Databricks runs annual pen tests, CVE scans, and third-party audits on "
            "your behalf. Clean Rooms enables zero-copy cross-org data sharing. "
            "Anomaly detection on system.access.audit surfaces suspicious access patterns."
        ),
        has_live_drill_in=True,
        drill_page="security",
    ),
    Pillar(
        key="cost",
        name="Cost of Doing Business",
        icon="dollar-sign",
        stage_descriptions={
            "POC": "Very High ($$$$)",
            "MVP": "High ($$$)",
            "Scale": "Moderate ($$)",
            "Mature": "Low ($)",
        },
        databricks_stage="Scale",
        proof_point=(
            "Serverless pricing is tied to query-seconds, not provisioned capacity. "
            "You only pay for what a query actually consumes, with no idle cluster cost."
        ),
        path_to_mature_title="Path to Mature",
        path_to_mature=(
            "Predictive Optimization plus Liquid Clustering auto-tune storage layout; "
            "Photon reduces CPU cost 3-5x on typical SQL. Serverless SKUs across SDP "
            "and DBSQL - already in this demo - are the Mature-tier compute model."
        ),
        has_live_drill_in=True,
        drill_page="cost",
    ),
    Pillar(
        key="audit",
        name="Auditability",
        icon="file-text",
        stage_descriptions={
            "POC": "No logs",
            "MVP": "Basic DB / app logs",
            "Scale": "Comprehensive + automated + immutable",
            "Mature": "SOX / GDPR / HIPAA automation + self-service auditor queries",
        },
        databricks_stage="Mature",
        proof_point=(
            "system.access.audit captures every query, grant, and API "
            "call as immutable Delta rows, queryable with SQL."
        ),
        path_to_mature_title="Sustaining Mature",
        path_to_mature=(
            "AI/BI dashboards on system.access.audit produce automated SOX, GDPR, and "
            "HIPAA compliance reports. Databricks Alerts fire on anomalous query patterns. "
            "The Delta transaction log makes every data change auditable."
        ),
        has_live_drill_in=True,
        drill_page="audit",
    ),
]


def get_pillar(key: str) -> Pillar:
    for p in PILLARS:
        if p.key == key:
            return p
    raise KeyError(f"Unknown pillar key: {key}")


def stage_value(stage: str) -> int:
    """Map 'Scale' -> 3. Handles 'Moderate/Scale'-style compound labels by
    taking the last recognizable token."""
    for token in reversed(stage.replace("/", " ").split()):
        if token in STAGE_VALUE:
            return STAGE_VALUE[token]
    return 0
