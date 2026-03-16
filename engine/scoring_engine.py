"""Conviction scoring engine for Spider4AI opportunities."""

from __future__ import annotations


class ScoringEngine:
    """Weighted 0-100 conviction score composer."""

    weights = {
        "narrative_strength": 25,
        "volume_momentum": 20,
        "liquidity_health": 20,
        "accumulation_signals": 20,
        "market_stability": 15,
    }

    def score(
        self,
        narrative_confidence: float,
        volume_momentum: float,
        liquidity_health: float,
        accumulation_score: float,
        market_stability: float,
    ) -> float:
        total = (
            narrative_confidence * self.weights["narrative_strength"]
            + volume_momentum * self.weights["volume_momentum"]
            + liquidity_health * self.weights["liquidity_health"]
            + accumulation_score * self.weights["accumulation_signals"]
            + market_stability * self.weights["market_stability"]
        )
        return round(max(0, min(100, total)), 2)
