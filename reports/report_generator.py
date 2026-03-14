"""Daily opportunity report generator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from storage.database import Database


class ReportGenerator:
    """Builds markdown reports from stored opportunities."""

    def __init__(self) -> None:
        self.db = Database()
        self.output_dir = Path("reports")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(self) -> str:
        top = self.db.top_opportunities(limit=5)
        date = datetime.utcnow().strftime("%Y-%m-%d")
        path = self.output_dir / f"daily_report_{date}.md"

        lines = [
            f"# Spider4AI Daily Report ({date})",
            "",
            "## Top 5 Highest Conviction Coins",
            "",
        ]

        if not top:
            lines.append("No opportunities available yet. Run `python main.py scan` first.")
        else:
            for i, coin in enumerate(top, start=1):
                lines.extend(
                    [
                        f"### {i}. {coin['symbol']} (Score: {coin['score']})",
                        f"- Narrative: {coin['narrative']}",
                        f"- Reasoning Summary: {coin['reason']}",
                        f"- Volume 24h: {coin['volume_24h']}",
                        f"- Liquidity: {coin['liquidity']}",
                        "",
                    ]
                )

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
