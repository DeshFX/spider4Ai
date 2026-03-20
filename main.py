"""CLI entrypoint for Spider4AI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from web3 import Web3

from agents.spider_agent import SpiderAgent
from config import ConfigError, settings
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
