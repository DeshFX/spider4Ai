"""CLI entrypoint for Spider4AI."""

from __future__ import annotations

import typer

from agents.spider_agent import SpiderAgent
from execution.sepolia_executor import SepoliaExecutor
from reports.report_generator import ReportGenerator
from ui.dashboard import run_dashboard

app = typer.Typer(help="Spider4AI autonomous crypto market hunter (dashboard-first)")


@app.callback(invoke_without_command=True)
def entrypoint(ctx: typer.Context) -> None:
    """Default entrypoint: launch dashboard when no subcommand is supplied (recommended mode)."""
    if ctx.invoked_subcommand is None:
        run_dashboard()


@app.command("scan")
def scan_command() -> None:
    """Run one complete market scan cycle."""
    agent = SpiderAgent()
    opportunities = agent.run_cycle()
    typer.echo(f"Scan complete: {len(opportunities)} opportunities generated.")


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
def test_trade_command() -> None:
    """Simulate Sepolia testnet transaction flow."""
    execute = typer.confirm("Execute test transaction?", default=False)
    if not execute:
        typer.echo("Transaction simulation cancelled.")
        return

    executor = SepoliaExecutor()
    tx_hash = executor.simulate_test_transaction()
    typer.echo(f"Test transaction submitted: {tx_hash}")


if __name__ == "__main__":
    app()
