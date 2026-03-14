"""Risk guards to suppress low-quality or suspicious setups."""

from __future__ import annotations

from typing import Any


class RiskFilter:
    """Apply sanity checks to remove unsafe candidates."""

    def is_safe(self, market: dict[str, Any], dex: dict[str, Any]) -> tuple[bool, str]:
        price_change = abs(float(market.get("price_change_percentage_24h") or 0))
        liquidity = float(dex.get("liquidity") or 0)
        volume = float(market.get("total_volume") or 0)

        if price_change > 35:
            return False, "Extreme pump spike"
        if liquidity < 50_000:
            return False, "Very low liquidity"
        if volume > 0 and liquidity > 0 and (volume / liquidity) > 40:
            return False, "Suspicious abnormal volume"
        return True, "Passed risk filters"
