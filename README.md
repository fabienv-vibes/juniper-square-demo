# Juniper Square — Databricks Custom Demo

Custom demo for Juniper Square demonstrating performance and cost against their 7-pillar platform evaluation (Data Latency, Cost, Keeping the Lights On, Lineage, Provenance, Security, Auditability).

Workload modeled on Juniper's stated scale: 10K customers ("arenas"), 10TB Delta, 5 QPS sustained ad-hoc + 100 dashboards/day + streaming microbatch ETL.

## Layout

| Dir | What's inside |
|---|---|
| `app/` | Streamlit-on-Databricks-Apps dashboard (`juniper-benchmark-viewer`). Landing page is the 7-pillar overview; each drill-in runs live SQL against the demo workspace. |
| `data/` | 10TB data generator + Spark Declarative Pipelines (bronze → silver → gold) + Lakebase sync. Seeds `juniper_square_demo_catalog.pipeline.*`. |
| `benchmark/` | Concurrency harness (`concurrency_benchmark.py`) for DBSQL Serverless vs. Lakebase. Weighted 6-query mix, configurable concurrency levels. Writes results to `benchmark_*` Delta tables. |

## Workspace

- **Workspace:** `fevm-juniper-square-demo`, CLI profile `juniper-square-demo`
- **Catalog:** `juniper_square_demo_catalog` — schemas `raw`, `pipeline`, `serving`
- **Warehouse:** Serverless Starter Warehouse (Small/Pro), id `133b52f9331b883d`
- **Lakebase:** project `juniper-sq-benchmark`, branch `production`, 1 CU autoscaling

## Redeploy from scratch

```bash
# 1. Generate data (if seeding fresh)
cd data/data_gen
python generate_data_spark.py  # runs on serverless

# 2. Run SDP pipeline (from workspace UI or CLI)
cd ../sdp_pipeline
# → deploy as Spark Declarative Pipeline in workspace

# 3. Run benchmark
cd ../../benchmark/benchmark
./run_benchmark.sh

# 4. Deploy app
cd ../../app
databricks sync . /Workspace/Users/<you>/juniper-benchmark-app --profile juniper-square-demo
databricks apps deploy juniper-benchmark-viewer \
  --source-code-path /Workspace/Users/<you>/juniper-benchmark-app \
  --profile juniper-square-demo
```

See each subdir's own README / notebooks for detail.
