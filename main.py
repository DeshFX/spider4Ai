"""CLI entrypoint for Spider4AI."""

from __future__ import annotations

import calendar
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from apscheduler.schedulers.background import BackgroundScheduler
from web3 import Web3

from agents.spider_agent import SpiderAgent
from config import ConfigError, settings
from data.cambrian_client import CambrianClient
from execution.dex_swap import swap_eth_to_token
from execution.sepolia_executor import SepoliaExecutor
from genlayer.service import GenLayerService
from reports.report_generator import ReportGenerator
from storage.database import Database
from ui.dashboard import run_dashboard

app = typer.Typer(help="Spider4AI autonomous crypto market hunter (dashboard-first)")


DEFAULT_SWAP_TEST_TOKEN = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
DEFAULT_SWAP_TEST_CONFIDENCE = 0.82


def _validate_startup() -> None:
    try:
        settings.validate_startup()
    except ConfigError as exc:
        typer.secho(f"[CONFIG ERROR] {exc}", fg=typer.colors.YELLOW)


def _system_health() -> dict[str, str]:
    health = settings.health_snapshot()
    rpc_status = "unconfigured"
    wallet_status = "missing"
    if settings.sepolia_rpc_url:
        rpc_status = "connected" if Web3(Web3.HTTPProvider(settings.sepolia_rpc_url)).is_connected() else "unreachable"
    if settings.wallet_private_key:
        try:
            addr = Web3().eth.account.from_key(settings.wallet_private_key).address
            wallet_status = f"loaded:{addr[:10]}..."
        except Exception:
            wallet_status = "invalid"
    health.update({"rpc_status": rpc_status, "wallet_status": wallet_status})
    return health


@app.callback(invoke_without_command=True)
def entrypoint(ctx: typer.Context) -> None:
    """Default entrypoint: launch dashboard when no subcommand is supplied (recommended mode)."""
    _validate_startup()
    if ctx.invoked_subcommand is None:
        run_dashboard()


@app.command("scan")
def scan_command() -> None:
    """Run one complete market scan cycle."""
    agent = SpiderAgent()
    opportunities = agent.run_cycle()
    typer.echo(f"Scan complete: {len(opportunities)} opportunities generated.")


@app.command("agent-run")
def agent_run_command() -> None:
    """Run the full pipeline (scan + GenLayer + decision + execution bridge)."""
    opportunities = SpiderAgent().run_cycle()
    typer.echo(f"Agent pipeline complete: {len(opportunities)} opportunities processed.")


@app.command("genlayer-test")
def genlayer_test_command() -> None:
    """Send a dummy payload to GenLayer and print the returned decision."""
    payload = {
        "token": "SPIDER",
        "summary": "Test payload from CLI",
        "signal_strength": 0.82,
        "risk_flags": ["thin_liquidity"],
        "market_context": "Synthetic CLI smoke test",
        "source": "cli",
        "recent_trend": "Mixed short-term price action",
    }
    result = GenLayerService().send_decision(payload)
    typer.echo(result)


@app.command("db-check")
def db_check_command() -> None:
    """Print the last 10 opportunities with decision source and confidence."""
    rows = Database().get_latest_opportunities(limit=10)
    for row in rows:
        typer.echo(
            f"{row['symbol']:>8} | source={row.get('decision_source','n/a'):<10} | "
            f"decision={row.get('genlayer_decision','n/a'):<5} | conf={float(row.get('genlayer_confidence',0) or 0):.2f}"
        )


@app.command("status")
def status_command() -> None:
    """Show system health (RPC, wallet, GenLayer)."""
    for key, value in _system_health().items():
        typer.echo(f"{key}: {value}")


def _check_cambrian(client: CambrianClient | None = None) -> str:
    client = client or CambrianClient()
    if not client.api_key:
        return "unconfigured (CAMBRIAN_API_KEY missing)"
    return "connected" if client.ping() else "unreachable"


def _check_genlayer(service: GenLayerService | None = None, cfg: Any = None) -> str:
    cfg = cfg or settings
    if service is not None:
        return "configured" if service.enabled else "disabled"
    if not cfg.genlayer_enabled:
        return "disabled (SPIDER4AI_GENLAYER_ENABLED=false)"
    if not cfg.genlayer_contract_address:
        return "misconfigured (contract address missing)"
    try:
        from genlayer.client import get_client

        get_client()
        return "configured"
    except Exception as exc:
        return f"sdk_error ({exc})"


def _check_database(database: Any = None) -> str:
    database = database or Database()
    try:
        status = database.get_scan_status()
        return f"ok ({status['coins_scanned']} coins scanned, {status['open_positions']} open)"
    except Exception as exc:
        return f"error ({exc})"


def collect_health(
    cambrian_client: CambrianClient | None = None,
    genlayer_service: GenLayerService | None = None,
    database: Any = None,
    settings_obj: Any = None,
) -> dict[str, str]:
    """Gather a full health report: Cambrian API, GenLayer, database, and .env."""
    cfg = settings_obj or settings
    checks: dict[str, str] = {
        "cambrian_api": _check_cambrian(cambrian_client),
        "genlayer": _check_genlayer(genlayer_service, cfg),
        "database": _check_database(database),
    }
    for key, value in cfg.health_snapshot().items():
        checks[f"env.{key}"] = str(value)
    return checks


@app.command("healthcheck")
def healthcheck_command() -> None:
    """Check Cambrian API, GenLayer connection, database access, and .env status."""
    for key, value in collect_health().items():
        typer.echo(f"{key}: {value}")


@app.command("cambrian-usage")
def cambrian_usage_command() -> None:
    """Show Cambrian API usage: today, this month, budget, and exhaustion projection."""
    usage = Database().get_api_usage()
    budget = settings.cambrian_monthly_budget
    margin = settings.cambrian_safety_margin
    threshold = int(budget * margin)
    now = datetime.utcnow()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    month_days_elapsed = max(now.day, 1)
    projected_month = (usage["month_calls"] / month_days_elapsed) * days_in_month
    if usage["today_calls"] > 0:
        days_to_limit = max(0.0, (budget - usage["month_calls"]) / usage["today_calls"])
        exhaustion_date = (now + timedelta(days=days_to_limit)).date().isoformat()
    else:
        exhaustion_date = "never (no calls today)"

    typer.echo("Cambrian API usage")
    typer.echo(f"  today calls     : {usage['today_calls']}")
    typer.echo(f"  month calls     : {usage['month_calls']}")
    typer.echo(f"  total calls     : {usage['total_calls']}")
    typer.echo(f"  monthly budget  : {budget}")
    typer.echo(f"  safety margin   : {margin:.0%} (threshold: {threshold})")
    typer.echo(f"  projected month : {projected_month:.0f} calls")
    typer.echo(f"  budget exhausted: {exhaustion_date}")
    if usage["month_calls"] >= threshold:
        typer.echo("  STATUS          : BUDGET-SAVING MODE ACTIVE")
    else:
        typer.echo(f"  STATUS          : OK ({budget - usage['month_calls']} calls remaining)")


@app.command("reset-db")
def reset_db_command(yes: bool = typer.Option(False, "--yes", help="Delete the SQLite DB without confirmation.")) -> None:
    """Reset the local SQLite database."""
    db_path = Path(settings.db_path)
    if not db_path.exists():
        typer.echo(f"Database not found: {db_path}")
        return
    if not yes and not typer.confirm(f"Delete database at {db_path}?", default=False):
        typer.echo("Reset cancelled.")
        return
    db_path.unlink()
    typer.echo(f"Deleted database: {db_path}")


@app.command("swap-test")
def swap_test_command() -> None:
    """Preview an isolated Sepolia ETH -> token swap test without broadcasting."""
    tx_hash = swap_eth_to_token(DEFAULT_SWAP_TEST_TOKEN, DEFAULT_SWAP_TEST_CONFIDENCE)
    if tx_hash is None:
        typer.echo("[SWAP PREVIEW] Preview complete; no transaction was broadcast")
    else:
        typer.echo(f"Unexpected preview return value: {tx_hash}")


@app.command("dashboard")
def dashboard_command() -> None:
    """Start the real-time terminal dashboard."""
    run_dashboard()


@app.command("report")
def report_command() -> None:
    """Generate and save the daily report."""
    generator = ReportGenerator()
    path = generator.generate_daily_report()
    typer.echo(f"Report generated at: {path}")


def _run_daily_report() -> str:
    generator = ReportGenerator()
    return generator.generate_daily_report()


@app.command("daily-report")
def daily_report_command(
    schedule: bool = typer.Option(
        False,
        "--schedule",
        help="Start a 24h scheduler instead of generating the report once.",
    ),
) -> None:
    """Generate the daily report markdown, or run an automatic daily scheduler."""
    if schedule:
        scheduler = BackgroundScheduler()
        scheduler.add_job(_run_daily_report, "interval", hours=24, next_run_time=datetime.now())
        scheduler.start()
        typer.echo("Daily report scheduler started (runs every 24h). Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            scheduler.shutdown()
            typer.echo("Daily report scheduler stopped.")
        return
    path = _run_daily_report()
    typer.echo(f"Report generated at: {path}")


@app.command("testtrade")
def test_trade_command(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation and execute test transaction immediately.",
    )
) -> None:
    """Simulate Sepolia testnet transaction flow."""
    settings.validate_execution()
    if not yes:
        execute = typer.confirm("Execute test transaction?", default=False)
        if not execute:
            typer.echo("Transaction simulation cancelled.")
            return

    executor = SepoliaExecutor()
    tx_hash = executor.simulate_test_transaction()
    typer.echo(f"Test transaction submitted: {tx_hash}")


if __name__ == "__main__":
    app()
