# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""GenLayer intelligent contract for Spider4AI trade evaluation.

Calldata on GenLayer does not support float, so all public write inputs and
view outputs are JSON strings. Floats travel inside the JSON text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from genlayer import *

ROLE_WEIGHTS = {
    "BULL_ANALYST": 1.0,
    "BEAR_ANALYST": 1.35,
    "NEUTRAL_ANALYST": 1.15,
}
ROLES = tuple(ROLE_WEIGHTS.keys())
MAX_HISTORY = 10
VALID_DECISIONS = ("BUY", "WAIT", "SKIP", "SCAM")
DISAGREEMENT_THRESHOLD = 0.45

_EMPTY_DECISION = {
    "final_decision": "WAIT",
    "confidence": 0.0,
    "votes": [],
    "reasoning": "No decision yet",
    "disagreement": 0.0,
}


@allow_storage
@dataclass
class EvaluationRecord:
    symbol: str
    final_decision: str
    confidence: float
    disagreement: float


class SpiderTradeDecision(gl.Contract):
    """Evaluates Spider4AI opportunities with role-based validator perspectives."""

    last_decision: str
    decision_history: DynArray[str]
    confidence_history: DynArray[float]
    recent_evaluations: DynArray[EvaluationRecord]
    evaluation_count: u256
    buy_count: u256
    wait_count: u256
    skip_count: u256
    scam_count: u256

    def __init__(self) -> None:
        self.last_decision = json.dumps(_EMPTY_DECISION)

    @gl.public.write
    def evaluate_trade(self, payload_json: str) -> None:
        payload = json.loads(payload_json)
        symbol = str(payload.get("token") or payload.get("symbol") or "").upper()
        if not symbol:
            raise gl.UserError("Payload must include token or symbol")

        votes: list[dict] = []
        for role in ROLES:
            response = gl.eq_principle.prompt_non_comparative(
                lambda prompt=self._build_prompt(payload, role): prompt,
                task="Return strict JSON with decision, confidence, reasoning",
                criteria=(
                    "decision must be BUY, WAIT, SKIP, or SCAM; confidence must be a float "
                    "between 0 and 1; reasoning must explain the role's perspective"
                ),
            )
            parsed_vote = response if isinstance(response, dict) else json.loads(str(response))
            votes.append(self._normalize_vote(role, parsed_vote))

        aggregate = self._aggregate_votes(payload, votes)
        self.last_decision = json.dumps(aggregate)
        self._store_history(symbol, aggregate)

    @gl.public.view
    def get_last_decision(self) -> str:
        return self.last_decision

    @gl.public.view
    def get_decision_history(self) -> str:
        return json.dumps(list(self.decision_history))

    @gl.public.view
    def get_confidence_history(self) -> str:
        return json.dumps(list(self.confidence_history))

    @gl.public.view
    def get_recent_evaluations(self) -> str:
        records = [
            {
                "symbol": record.symbol,
                "final_decision": record.final_decision,
                "confidence": record.confidence,
                "disagreement": record.disagreement,
            }
            for record in self.recent_evaluations
        ]
        return json.dumps(records)

    @gl.public.view
    def get_metrics(self) -> str:
        return json.dumps(
            {
                "evaluation_count": int(self.evaluation_count),
                "buy_count": int(self.buy_count),
                "wait_count": int(self.wait_count),
                "skip_count": int(self.skip_count),
                "scam_count": int(self.scam_count),
            }
        )

    def _build_prompt(self, payload: dict, role: str) -> str:
        role_frame = {
            "BULL_ANALYST": "You are the BULL ANALYST. Look for asymmetric upside and momentum continuation, but remain factual.",
            "BEAR_ANALYST": "You are the BEAR ANALYST. Focus on downside, scam probability, manipulative order flow, and capital preservation.",
            "NEUTRAL_ANALYST": "You are the NEUTRAL ANALYST. Balance upside vs downside and favor patience when evidence conflicts.",
        }[role]
        return (
            f"{role_frame}\n"
            "Evaluate the token and return strict JSON with keys decision, confidence, reasoning.\n"
            "Allowed decisions: BUY, WAIT, SKIP, SCAM.\n"
            "Confidence must be a float from 0 to 1.\n"
            f"token: {payload.get('token', payload.get('symbol', ''))}\n"
            f"summary: {payload.get('summary', '')}\n"
            f"signal_strength: {payload.get('signal_strength')}\n"
            f"risk_flags: {payload.get('risk_flags', [])}\n"
            f"market_context: {payload.get('market_context', '')}\n"
            f"recent_trend: {payload.get('recent_trend', '')}\n"
            f"source: {payload.get('source', '')}\n"
            f"tier: {payload.get('tier', '')}\n"
            f"onchain_context: {payload.get('onchain_context', '')}\n"
            "If onchain_context shows supply minting, extreme holder concentration, "
            "or liquidity removal, the risk of SCAM is high.\n"
        )

    def _normalize_vote(self, role: str, vote: dict) -> dict:
        if not isinstance(vote, dict):
            raise gl.UserError("Vote must be a dict")
        decision = str(vote.get("decision", "SKIP")).upper()
        if decision not in VALID_DECISIONS:
            raise gl.UserError(f"Invalid decision: {decision}")
        confidence = float(vote.get("confidence", 0))
        if confidence < 0 or confidence > 1:
            raise gl.UserError("confidence must be between 0 and 1")
        return {
            "role": role,
            "decision": decision,
            "confidence": confidence,
            "reasoning": str(vote.get("reasoning", "")),
            "weight": ROLE_WEIGHTS[role],
        }

    def _aggregate_votes(self, payload: dict, votes: list[dict]) -> dict:
        weighted_scores = {decision: 0.0 for decision in VALID_DECISIONS}
        weighted_confidence_sum = 0.0
        total_weight = 0.0
        for vote in votes:
            vote_weight = float(vote["weight"])
            vote_score = vote_weight * float(vote["confidence"])
            weighted_scores[vote["decision"]] += vote_score
            weighted_confidence_sum += vote_score
            total_weight += vote_weight

        winning_decision = "WAIT"
        winning_score = -1.0
        for decision in VALID_DECISIONS:
            if weighted_scores[decision] > winning_score:
                winning_decision = decision
                winning_score = weighted_scores[decision]

        disagreement = 1 - (winning_score / max(weighted_confidence_sum, 1e-9))
        if disagreement >= DISAGREEMENT_THRESHOLD:
            winning_decision = "WAIT"

        ai_confidence = weighted_confidence_sum / max(total_weight, 1e-9)
        signal_strength = float(payload.get("signal_strength", 0))
        risk_penalty = min(len(payload.get("risk_flags", [])) * 0.08, 0.4)
        final_confidence = (ai_confidence * 0.6) + (signal_strength * 0.4) - risk_penalty
        if disagreement >= DISAGREEMENT_THRESHOLD:
            final_confidence *= 0.75
        final_confidence = max(0.0, min(1.0, final_confidence))

        return {
            "final_decision": winning_decision,
            "confidence": final_confidence,
            "votes": votes,
            "reasoning": f"Weighted decision {winning_decision} with disagreement {disagreement:.4f}",
            "disagreement": disagreement,
        }

    def _store_history(self, symbol: str, aggregate: dict) -> None:
        decision = str(aggregate.get("final_decision", "SKIP"))
        confidence = float(aggregate.get("confidence", 0))
        disagreement = float(aggregate.get("disagreement", 0))
        self.decision_history.append(decision)
        self.confidence_history.append(confidence)
        self.recent_evaluations.append(
            EvaluationRecord(symbol=symbol, final_decision=decision, confidence=confidence, disagreement=disagreement)
        )
        self.decision_history = self.decision_history[-MAX_HISTORY:]
        self.confidence_history = self.confidence_history[-MAX_HISTORY:]
        self.recent_evaluations = self.recent_evaluations[-MAX_HISTORY:]
        self.evaluation_count = u256(int(self.evaluation_count) + 1)
        if decision == "BUY":
            self.buy_count = u256(int(self.buy_count) + 1)
        elif decision == "WAIT":
            self.wait_count = u256(int(self.wait_count) + 1)
        elif decision == "SKIP":
            self.skip_count = u256(int(self.skip_count) + 1)
        elif decision == "SCAM":
            self.scam_count = u256(int(self.scam_count) + 1)
