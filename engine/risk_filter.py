"""Risk guards to suppress low-quality or suspicious setups."""

from __future__ import annotations

from typing import Any

from data.cambrian_client import get_risk_score


class RiskFilter:
    """Apply sanity checks to remove unsafe candidates."""

    RISK_SCORE_THRESHOLD = 0.75

    tier_price_change_limit = {
        "alpha": 45,
        "low": 40,
        "mid": 35,
        "big": 35,
    }
    tier_min_liquidity = {
        "alpha": 20_000,
        "low": 30_000,
        "mid": 50_000,
        "big": 50_000,
    }
    tier_volume_liquidity_ratio = {
        "alpha": 60,
        "low": 50,
        "mid": 40,
        "big": 40,
    }

    def is_safe(self, market: dict[str, Any], dex: dict[str, Any], tier: str = "mid") -> tuple[bool, str]:
        price_change = abs(float(market.get("price_change_percentage_24h") or 0))
        liquidity = float(dex.get("liquidity") or 0)
        volume = float(market.get("total_volume") or 0)

        price_limit = self.tier_price_change_limit.get(tier, 35)
        if price_change > price_limit:
            return False, f"Extreme pump spike (tier {tier})"
        min_liquidity = self.tier_min_liquidity.get(tier, 50_000)
        if liquidity > 0 and liquidity < min_liquidity:
            return False, f"Very low liquidity (tier {tier})"
        ratio_limit = self.tier_volume_liquidity_ratio.get(tier, 40)
        if volume > 0 and liquidity > 0 and (volume / liquidity) > ratio_limit:
            return False, "Suspicious abnormal volume"

        cambrian_risk = get_risk_score(market.get("id") or market.get("address") or market.get("symbol") or "")
        if cambrian_risk and float(cambrian_risk.get("score") or 0) >= self.RISK_SCORE_THRESHOLD:
            return False, "High Cambrian risk score"
        return True, "Passed risk filters"
