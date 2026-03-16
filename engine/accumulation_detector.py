"""Detect accumulation patterns from market and DEX metrics."""

from __future__ import annotations

from typing import Any


class AccumulationDetector:
    """Score accumulation signals between 0 and 1."""

    def score(self, market: dict[str, Any], dex: dict[str, Any]) -> float:
        volume = float(market.get("total_volume") or 0)
        market_cap = float(market.get("market_cap") or 1)
        change = abs(float(market.get("price_change_percentage_24h") or 0))
        liquidity = float(dex.get("liquidity") or 0)

        volume_acceleration = min((volume / max(market_cap, 1)) * 4, 1.0)
        liquidity_growth = min(liquidity / 5_000_000, 1.0)
        price_consolidation = max(0.0, 1 - (change / 20))
        market_cap_expansion = min(market_cap / 10_000_000_000, 1.0)

        return round(
            (volume_acceleration + liquidity_growth + price_consolidation + market_cap_expansion)
            / 4,
            4,
        )
