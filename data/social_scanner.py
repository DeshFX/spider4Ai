"""Social scanner stub for future sentiment integrations."""

from __future__ import annotations

from typing import Any


class SocialScanner:
    """Stubbed social signal provider to keep architecture extensible."""

    def fetch_social_score(self, coin: dict[str, Any]) -> float:
        """Return neutral score; replace with X/Reddit/Telegram connectors later."""
        symbol = (coin.get("symbol") or "").lower()
        if symbol in {"wif", "pepe", "bonk"}:
            return 0.8
        return 0.5
