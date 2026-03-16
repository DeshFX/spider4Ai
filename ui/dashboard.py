"""Textual dashboard for Spider4AI live monitoring and operations."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static

from agents.spider_agent import SpiderAgent
from execution.sepolia_executor import SepoliaExecutor
from reports.report_generator import ReportGenerator
from storage.database import Database


class SpiderDashboard(App[None]):
    """Terminal dashboard that centralizes monitoring and operational actions."""

    CSS = """
    Screen { layout: vertical; }
    #body { layout: horizontal; }
    .panel { width: 1fr; padding: 1; }
    #log_panel { height: 9; }
    """

    BINDINGS = [
        ("s", "scan_now", "Scan now"),
        ("a", "toggle_auto_scan", "Auto scan"),
        ("r", "generate_report", "Generate report"),
        ("t", "test_trade", "Test trade"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.agent = SpiderAgent()
        self.scheduler = None
        self.auto_scan_enabled = False
        self.last_action = "Ready (dashboard-first mode)"
        self.last_scan_result = "Not run"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="body"):
            yield Static(id="top_opps", classes="panel")
            yield Static(id="watchlist", classes="panel")
            yield Static(id="status", classes="panel")
        yield Static(id="log_panel", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Spider4AI Dashboard"
        self.set_interval(8, self.refresh_panels)
        self.refresh_panels()

    async def action_scan_now(self) -> None:
        self.last_action = "Running market scan..."
        self.refresh_panels()
        try:
            opportunities = await asyncio.to_thread(self.agent.run_cycle)
            self.last_scan_result = f"{len(opportunities)} opportunities"
            self.last_action = "Scan completed"
        except Exception as exc:
            self.last_action = f"Scan failed: {exc}"
        self.refresh_panels()

    def action_toggle_auto_scan(self) -> None:
        if self.auto_scan_enabled and self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
            self.auto_scan_enabled = False
            self.last_action = "Auto scan disabled"
        else:
            self.scheduler = self.agent.start_scheduler()
            self.auto_scan_enabled = True
            self.last_action = "Auto scan enabled (every 10 minutes by config)"
        self.refresh_panels()

    async def action_generate_report(self) -> None:
        self.last_action = "Generating report..."
        self.refresh_panels()
        try:
            path = await asyncio.to_thread(ReportGenerator().generate_daily_report)
            self.last_action = f"Report generated: {path}"
        except Exception as exc:
            self.last_action = f"Report failed: {exc}"
        self.refresh_panels()

    async def action_test_trade(self) -> None:
        self.last_action = "Submitting Sepolia test transaction..."
        self.refresh_panels()
        try:
            tx_hash = await asyncio.to_thread(self._run_test_trade)
            self.last_action = f"Test tx sent: {tx_hash[:16]}..."
        except Exception as exc:
            self.last_action = f"Test trade failed: {exc}"
        self.refresh_panels()

    @staticmethod
    def _run_test_trade() -> str:
        executor = SepoliaExecutor()
        return executor.simulate_test_transaction()

    def refresh_panels(self) -> None:
        top = self.db.get_latest_opportunities(10)
        watch = self.db.get_watchlist()
        status = self.db.get_scan_status()

        top_table = Table(title="Top Opportunities", expand=True)
        top_table.add_column("Coin", style="cyan")
        top_table.add_column("Score", style="green")
        top_table.add_column("Narrative", style="magenta")
        top_table.add_column("Volume", justify="right")
        top_table.add_column("Price", justify="right")
        for coin in top:
            top_table.add_row(
                coin["symbol"],
                f"{coin['score']:.2f}",
                coin.get("narrative", "N/A"),
                f"{coin.get('volume_24h', 0):,.0f}",
                f"${coin.get('price', 0):,.4f}",
            )

        watch_table = Table(title="Watchlist (60-70)", expand=True)
        watch_table.add_column("Coin", style="yellow")
        watch_table.add_column("Score", style="green")
        watch_table.add_column("Narrative", style="magenta")
        for coin in watch:
            watch_table.add_row(
                coin["symbol"], f"{coin['score']:.2f}", coin.get("narrative", "N/A")
            )

        status_table = Table(title="System Status", expand=True)
        status_table.add_column("Metric")
        status_table.add_column("Value", style="bold green")
        status_table.add_row("Coins scanned", str(status["coins_scanned"]))
        status_table.add_row("Narratives detected", str(status["narratives_detected"]))
        status_table.add_row("Last update", str(status["last_update"]))
        status_table.add_row("Auto scan", "ON" if self.auto_scan_enabled else "OFF")
        status_table.add_row("Last scan", self.last_scan_result)
        status_table.add_row("Local time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        log_table = Table(title="Control Center (Single-UI Ops)", expand=True)
        log_table.add_column("Action")
        log_table.add_column("Status")
        log_table.add_row("Hotkeys", "[S] Scan [A] Auto [R] Report [T] TestTrade [Q] Quit")
        log_table.add_row("Last action", self.last_action)

        self.query_one("#top_opps", Static).update(top_table)
        self.query_one("#watchlist", Static).update(watch_table)
        self.query_one("#status", Static).update(status_table)
        self.query_one("#log_panel", Static).update(log_table)


def run_dashboard() -> None:
    """Run Textual dashboard app."""
    SpiderDashboard().run()
