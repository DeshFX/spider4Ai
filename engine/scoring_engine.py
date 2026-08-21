"""Conviction scoring engine for Spider4AI opportunities."""

from __future__ import annotations


class ScoringEngine:
    """Weighted 0-100 conviction score composer.

    Each market-cap tier (alpha/low/mid/big) gets its own weight profile so
    early-stage tokens favour momentum/accumulation while established tokens
    favour stability.
    """

    weights = {
        "narrative_strength": 25,
        "volume_momentum": 20,
        "liquidity_health": 20,
        "accumulation_signals": 20,
        "market_stability": 15,
    }

    tier_weights = {
        "alpha": {
            "narrative_strength": 20,
            "volume_momentum": 35,
            "liquidity_health": 20,
            "accumulation_signals": 20,
            "market_stability": 5,
        },
        "low": {
            "narrative_strength": 20,
            "volume_momentum": 25,
            "liquidity_health": 25,
            "accumulation_signals": 20,
            "market_stability": 10,
        },
        "mid": {
            "narrative_strength": 25,
            "volume_momentum": 20,
            "liquidity_health": 20,
            "accumulation_signals": 20,
            "market_stability": 15,
        },
        "big": {
            "narrative_strength": 15,
            "volume_momentum": 15,
            "liquidity_health": 20,
            "accumulation_signals": 10,
            "market_stability": 40,
        },
    }

    def score(
        self,
        narrative_confidence: float,
        volume_momentum: float,
        liquidity_health: float,
        accumulation_score: float,
        market_stability: float,
        tier: str = "mid",
    ) -> float:
        weights = self.tier_weights.get(tier, self.weights)
        total = (
            narrative_confidence * weights["narrative_strength"]
            + volume_momentum * weights["volume_momentum"]
            + liquidity_health * weights["liquidity_health"]
            + accumulation_score * weights["accumulation_signals"]
            + market_stability * weights["market_stability"]
        )
        return round(max(0, min(100, total)), 2)
