"""Textual dashboard for Spider4AI live monitoring."""

from __future__ import annotations

from datetime import datetime

from rich.table import Table
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Header, Footer, Static

from storage.database import Database


class SpiderDashboard(App[None]):
    """Terminal dashboard that refreshes opportunities and system status."""

    CSS = """
    Screen { layout: vertical; }
    #body { layout: horizontal; }
    .panel { width: 1fr; padding: 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.db = Database()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="body"):
            yield Static(id="top_opps", classes="panel")
            yield Static(id="watchlist", classes="panel")
            yield Static(id="status", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Spider4AI Dashboard"
        self.set_interval(8, self.refresh_panels)
        self.refresh_panels()

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
        status_table.add_row("Local time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        self.query_one("#top_opps", Static).update(top_table)
        self.query_one("#watchlist", Static).update(watch_table)
        self.query_one("#status", Static).update(status_table)


def run_dashboard() -> None:
    """Run Textual dashboard app."""
    SpiderDashboard().run()
