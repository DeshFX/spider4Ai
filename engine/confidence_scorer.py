"""Composite confidence score merging engine score, risk filter, and decision.

Combines the outputs of:
  - engine/scoring_engine.py   (0-100 opportunity score)
  - engine/risk_filter.py      (risk guards incl. Cambrian risk score)
  - genlayer/service.py        (GenLayer decision or heuristic fallback)

Produces a single 0-100 confidence value used by the trade manager before sizing.
"""

from __future__ import annotations

from typing import Any

from engine.risk_filter import RiskFilter
from engine.scoring_engine import ScoringEngine

WEIGHTS = {"engine": 0.50, "decision": 0.35, "risk": 0.15}


class ConfidenceScorer:
    """Merge opportunity score, risk, and decision into one 0-100 confidence."""

    def __init__(
        self,
        scoring_engine: ScoringEngine | None = None,
        risk_filter: RiskFilter | None = None,
    ) -> None:
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.risk_filter = risk_filter or RiskFilter()

    def score(
        self,
        opportunity: dict[str, Any],
        market: dict[str, Any] | None = None,
        dex: dict[str, Any] | None = None,
    ) -> float:
        """Return a composite 0-100 confidence for a single opportunity."""
        decision = str(opportunity.get("genlayer_decision") or "WAIT").upper()
        if decision == "SCAM":
            return 0.0
        if decision == "SKIP":
            return round(min(30.0, max(0.0, float(opportunity.get("score") or 0) * 0.30)), 2)

        if market is None or dex is None:
            market, dex = self._market_dex(opportunity)
        tier = str(opportunity.get("tier") or "mid")
        safe, _ = self.risk_filter.is_safe(market, dex, tier)

        engine = max(0.0, min(100.0, float(opportunity.get("score") or 0)))
        decision_conf = max(0.0, min(1.0, float(opportunity.get("genlayer_confidence") or 0)))
        disagreement = max(0.0, min(1.0, float(opportunity.get("genlayer_disagreement") or 0)))

        if not safe:
            engine *= 0.5
        if decision == "WAIT":
            decision_conf *= 0.75

        decision_component = max(0.0, decision_conf - disagreement * 0.4)
        risk_component = self._risk_component(opportunity, safe)

        composite = (
            (engine / 100.0) * WEIGHTS["engine"]
            + decision_component * WEIGHTS["decision"]
            + risk_component * WEIGHTS["risk"]
        )
        return round(max(0.0, min(100.0, composite * 100.0)), 2)

    def _risk_component(self, opportunity: dict[str, Any], safe: bool) -> float:
        if not safe:
            return 0.0
        flags = [str(flag).lower() for flag in opportunity.get("risk_flags") or []]
        joined = " ".join(flags)
        if any(keyword in joined for keyword in ("scam", "rug", "honeypot")):
            return 0.4
        return max(0.0, min(1.0, 1.0 - 0.1 * len(flags)))

    @staticmethod
    def _market_dex(opportunity: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        market = {
            "symbol": opportunity.get("symbol"),
            "price_change_percentage_24h": opportunity.get("price_change_percentage_24h") or 0,
            "total_volume": opportunity.get("volume_24h") or 0,
        }
        dex = {"liquidity": opportunity.get("liquidity") or 0}
        return market, dex
