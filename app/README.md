# Juniper Square × Databricks Benchmark App

Streamlit-on-Databricks-Apps scorecard app for the Shaz Khan meeting.

Frames Databricks benchmark results inside Juniper's 7-pillar data-maturity scorecard:
**Data Latency · Keeping the Lights On · Data Lineage · Data Provenance · Data Security · Cost · Auditability**.

## Layout

```
juniper-benchmark-app/
├── app.py              # Streamlit entrypoint -- sidebar nav, 8 pages
├── app.yaml            # Databricks Apps manifest (serverless warehouse resource)
├── requirements.txt
├── lib/
│   ├── scorecard.py    # 7-pillar definitions, stages, colors
│   ├── queries.py      # SQL against juniper_square_demo_catalog.pipeline + system.*
│   ├── charts.py       # Plotly builders (radar, latency, throughput, cost)
│   └── lineage.py      # UC lineage API stubs
└── mock_data/          # CSV fallbacks used when Delta tables are empty
```

## Local dev

```bash
cd juniper-benchmark-app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional -- set these to hit the real warehouse instead of mock CSVs
export DATABRICKS_HOST="<workspace-host>"
export DATABRICKS_TOKEN="<pat>"            # or use DATABRICKS_CLIENT_ID/SECRET
export DATABRICKS_WAREHOUSE_ID="133b52f9331b883d"
export DATABRICKS_CATALOG="juniper_square_demo_catalog"
export DATABRICKS_SCHEMA="pipeline"

streamlit run app.py
```

Without those env vars the app runs in **preview mode** — every page renders from CSVs in
`mock_data/` and a banner calls that out.

## Deploy

```bash
databricks apps create juniper-benchmark --profile juniper-square-demo
databricks sync . /Users/<you>/juniper-benchmark \
  --exclude .venv --exclude __pycache__ --exclude .git \
  --profile juniper-square-demo
databricks apps deploy juniper-benchmark \
  --source-code-path /Workspace/Users/<you>/juniper-benchmark \
  --profile juniper-square-demo
```

Then in the UI, attach the serverless warehouse (`133b52f9331b883d`) as an app resource
with `CAN_USE` permission — redeploy to pick it up.

## Tables expected in `juniper_square_demo_catalog.pipeline`

- `benchmark_runs` — (run_id, run_ts, arena_count, dataset_size_tb, target, notes)
- `benchmark_summary` — (target, concurrency, query_name, p50_ms, p95_ms, p99_ms, throughput_qps, error_rate)
- `benchmark_raw` — (target, concurrency, query_name, run_ts, latency_ms)

Plus system tables: `system.lakeflow.pipelines`, `system.access.audit`,
`system.billing.usage`.
