#!/usr/bin/env python3
"""
Generate benchmark config with live connection details.

Pulls DBSQL token and Lakebase OAuth credentials from the Databricks CLI profile,
so you don't have to manually paste expiring tokens into config.yaml.

Usage:
    python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark

    # Skip arena-pool query (use hardcoded IDs from config.yaml):
    python3 gen_config.py --profile juniper-square-demo --project juniper-sq-benchmark --no-arena-pool-from-workspace

    # Then run benchmark with the generated config:
    python3 concurrency_benchmark.py --config config_live.yaml --target both
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml


def run_cli(args: list[str]) -> dict:
    """Run a databricks CLI command and return parsed JSON output."""
    result = subprocess.run(
        ["databricks"] + args + ["-o", "json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"CLI error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def fetch_arena_ids_from_workspace(profile: str, warehouse_id: str, hostname: str, token: str) -> list[str] | None:
    """
    Fetch up to 500 arena IDs from silver_arenas via DBSQL.

    Tries the silver table first; falls back to reading the landed JSON Volume
    via read_files() if the silver table doesn't exist yet (SDP still running).

    Returns a list of arena ID strings, or None on failure (caller uses fallback).
    """
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementState

        w = WorkspaceClient(host=f"https://{hostname}", token=token)

        silver_sql = (
            "SELECT arena_id "
            "FROM juniper_square_demo_catalog.pipeline.silver_arenas "
            "ORDER BY arena_id "
            "LIMIT 500"
        )
        volume_sql = (
            "SELECT arena_id "
            "FROM read_files("
            "  '/Volumes/juniper_square_demo_catalog/raw/landing/arenas/arenas.json',"
            "  format => 'json'"
            ") "
            "ORDER BY arena_id "
            "LIMIT 500"
        )

        for label, sql in [("silver_arenas table", silver_sql), ("Volume JSON fallback", volume_sql)]:
            print(f"  Querying arena pool from {label}...")
            try:
                result = w.statement_execution.execute_statement(
                    statement=sql,
                    warehouse_id=warehouse_id,
                    wait_timeout="30s",
                )
                if result.status and result.status.state == StatementState.SUCCEEDED:
                    rows = result.result.data_array or []
                    ids = [row[0] for row in rows if row and row[0]]
                    if ids:
                        print(f"  Arena pool: fetched {len(ids)} IDs from {label}")
                        return ids
                    print(f"  {label} returned 0 rows, trying next...")
                else:
                    state = result.status.state if result.status else "UNKNOWN"
                    err = result.status.error.message if (result.status and result.status.error) else ""
                    print(f"  {label} query {state}: {err} — trying next...")
            except Exception as e:
                print(f"  {label} error: {e} — trying next...")

        return None

    except ImportError:
        print("  databricks-sdk not installed — skipping workspace arena query", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Unexpected error fetching arena pool: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Generate live benchmark config")
    parser.add_argument("--profile", required=True, help="Databricks CLI profile")
    parser.add_argument("--project", required=True, help="Lakebase project ID")
    parser.add_argument("--branch", default="production", help="Lakebase branch")
    parser.add_argument("--endpoint", default="primary", help="Lakebase endpoint")
    parser.add_argument("--warehouse-id", default=None, help="Override warehouse ID")
    parser.add_argument("--base-config", default="config.yaml", help="Base config to merge queries from")
    parser.add_argument("--output", default="config_live.yaml", help="Output config path")
    parser.add_argument(
        "--arena-pool-from-workspace",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Query workspace for real arena IDs (default: True). Use --no-arena-pool-from-workspace to skip.",
    )
    args = parser.parse_args()

    profile = args.profile
    project = args.project
    branch = args.branch
    endpoint_id = args.endpoint

    print(f"Generating live config for profile={profile}, project={project}...")

    # Get workspace host from CLI config
    cli_config_result = subprocess.run(
        ["databricks", "auth", "env", "--profile", profile],
        capture_output=True, text=True,
    )
    workspace_host = None
    try:
        env_data = json.loads(cli_config_result.stdout)
        workspace_host = env_data.get("env", {}).get("DATABRICKS_HOST", "").rstrip("/")
    except (json.JSONDecodeError, KeyError):
        pass

    if not workspace_host:
        # Fallback: parse from .databrickscfg
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(Path.home() / ".databrickscfg")
        workspace_host = cfg.get(profile, "host", fallback="").rstrip("/")

    hostname = workspace_host.replace("https://", "").replace("http://", "")
    print(f"  Workspace: {workspace_host}")

    # Get DBSQL token
    token_result = subprocess.run(
        ["databricks", "auth", "token", "--profile", profile],
        capture_output=True, text=True,
    )
    token_data = json.loads(token_result.stdout)
    dbsql_token = token_data["access_token"]
    print(f"  DBSQL token: ...{dbsql_token[-8:]}")

    # Get warehouse ID
    if args.warehouse_id:
        warehouse_id = args.warehouse_id
    else:
        warehouses = run_cli(["warehouses", "list", "-p", profile])
        if not warehouses:
            print("ERROR: No warehouses found", file=sys.stderr)
            sys.exit(1)
        # Prefer running warehouse
        running = [w for w in warehouses if w.get("state") == "RUNNING"]
        wh = running[0] if running else warehouses[0]
        warehouse_id = wh["id"]
        print(f"  Warehouse: {wh['name']} ({warehouse_id}, {wh.get('state', 'UNKNOWN')})")

    # Get Lakebase endpoint host
    branch_path = f"projects/{project}/branches/{branch}"
    endpoint_path = f"{branch_path}/endpoints/{endpoint_id}"

    endpoints = run_cli(["postgres", "list-endpoints", branch_path, "-p", profile])
    if not endpoints:
        print(f"ERROR: No endpoints found for {branch_path}", file=sys.stderr)
        sys.exit(1)
    lb_host = endpoints[0]["status"]["hosts"]["host"]
    print(f"  Lakebase host: {lb_host}")

    # Generate Lakebase OAuth token
    lb_cred = run_cli(["postgres", "generate-database-credential", endpoint_path, "-p", profile])
    lb_token = lb_cred["token"]
    print(f"  Lakebase token: ...{lb_token[-8:]}")

    # Get user email
    user = run_cli(["current-user", "me", "-p", profile])
    email = user["userName"]
    print(f"  User: {email}")

    # Load base config for queries and benchmark params
    base_config_path = Path(args.base_config)
    if base_config_path.exists():
        with open(base_config_path) as f:
            base = yaml.safe_load(f)
    else:
        base = {"benchmark": {"levels": [1, 2, 4, 8, 16, 32], "iterations": 5, "warmup": 1},
                "queries": [{"name": "smoke_test", "sql": "SELECT 1"}]}

    # Resolve arena ID pool
    hardcoded_pool = base.get("arena_id_pool", [])
    arena_id_pool = hardcoded_pool  # default: use whatever is in config.yaml

    if args.arena_pool_from_workspace:
        live_ids = fetch_arena_ids_from_workspace(
            profile=profile,
            warehouse_id=warehouse_id,
            hostname=hostname,
            token=dbsql_token,
        )
        if live_ids:
            arena_id_pool = live_ids
            print(f"  Using live arena pool ({len(arena_id_pool)} IDs)")
        else:
            print(f"  Falling back to {len(hardcoded_pool)} hardcoded arena IDs from {args.base_config}")
    else:
        print(f"  --no-arena-pool-from-workspace: using {len(hardcoded_pool)} hardcoded arena IDs")

    # Build live config
    live_config = {
        "dbsql": {
            "hostname": hostname,
            "http_path": f"/sql/1.0/warehouses/{warehouse_id}",
            "token": dbsql_token,
        },
        "lakebase": {
            "host": lb_host,
            "port": 5432,
            "database": "juniper_serving",
            "user": email,
            "password": lb_token,
            "sslmode": "require",
        },
        "benchmark": base.get("benchmark", {}),
        "sustained_scenarios": base.get("sustained_scenarios", {}),
        "delta_persistence": base.get("delta_persistence", {}),
        "arena_id_pool": arena_id_pool,
        "fund_id_pool": base.get("fund_id_pool", []),
        "queries": base.get("queries", []),
    }

    output_path = Path(args.output)
    with open(output_path, "w") as f:
        yaml.dump(live_config, f, default_flow_style=False, sort_keys=False)

    print(f"\nConfig written to {output_path}")
    first5 = arena_id_pool[:5]
    last5 = arena_id_pool[-5:]
    print(f"  Arena pool preview: {first5} ... {last5}")
    print(f"  Total arena IDs: {len(arena_id_pool)}")
    fund_pool = base.get("fund_id_pool", [])
    if fund_pool:
        print(f"  Fund pool: {len(fund_pool)} IDs (e.g. {fund_pool[0]})")
    print(f"Note: Lakebase OAuth tokens expire after ~1 hour. Re-run this script to refresh.")


if __name__ == "__main__":
    main()
