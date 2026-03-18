"""Off-chain service layer for Spider4AI -> GenLayer intelligent contract calls."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

import requests

from config import settings
from genlayer.contracts import get_contract_at
from structured_logging import log_json

logger = logging.getLogger(__name__)
ALLOWED_DECISIONS = {"BUY", "WAIT", "SKIP", "SCAM"}


class GenLayerError(RuntimeError):
    """Raised when a GenLayer request cannot be completed successfully."""


@dataclass
class GenLayerContract:
    client: Any
    address: str
    wait_status: str = "FINALIZED"

    def evaluate_trade(self, data: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        transaction_hash = _call_with_timeout(
            lambda: self.client.write_contract(
                address=self.address,
                function_name="evaluate_trade",
                args=[data],
                value=0,
            ),
            timeout_seconds=timeout_seconds,
        )
        receipt = _call_with_timeout(
            lambda: self.client.wait_for_transaction_receipt(
                transaction_hash=transaction_hash,
                status=self.wait_status,
            ),
            timeout_seconds=timeout_seconds,
        )
        decision = _call_with_timeout(
            lambda: self.client.read_contract(
                address=self.address,
                function_name="get_last_decision",
                args=[],
            ),
            timeout_seconds=timeout_seconds,
        )
        normalized = normalize_decision_payload(decision)
        return {"decision": normalized, "transaction_hash": transaction_hash, "receipt": receipt}


class LocalFallbackDecisionEngine:
    def __init__(self) -> None:
        self.ollama_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout_seconds = settings.genlayer_fallback_timeout_seconds

    def decide(self, payload: dict[str, Any], reason: str) -> dict[str, Any]:
        prompt = build_decision_prompt(payload)
        llm_result = self._ollama_decision(prompt)
        if llm_result:
            log_json(logger, logging.WARNING, "decision_fallback", path="local_ai", token=payload.get("token"), reason=reason, decision=llm_result)
            return {"status": "fallback", "decision": llm_result, "decision_source": "local_ai", "reason": reason}
        heuristic = self._heuristic_decision(payload)
        log_json(logger, logging.WARNING, "decision_fallback", path="heuristic", token=payload.get("token"), reason=reason, decision=heuristic)
        return {"status": "fallback", "decision": heuristic, "decision_source": "heuristic", "reason": reason}

    def _ollama_decision(self, prompt: str) -> dict[str, Any] | None:
        try:
            response = requests.post(
                f"{self.ollama_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return normalize_decision_payload(json.loads(payload.get("response", "{}")))
        except Exception:
            return None

    def _heuristic_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        risk_flags = [str(flag).lower() for flag in payload.get("risk_flags", [])]
        signal_strength = float(payload.get("signal_strength") or 0)
        if any("scam" in flag or "rug" in flag or "honeypot" in flag for flag in risk_flags):
            return {"final_decision": "SCAM", "confidence": 0.9, "votes": [], "reasoning": "Heuristic scam protection triggered.", "disagreement": 0.0}
        if len(risk_flags) >= 3:
            return {"final_decision": "SKIP", "confidence": 0.65, "votes": [], "reasoning": "Heuristic risk filter rejected trade.", "disagreement": 0.15}
        if signal_strength >= 0.75:
            return {"final_decision": "BUY", "confidence": 0.62, "votes": [], "reasoning": "Heuristic momentum and conviction threshold passed.", "disagreement": 0.1}
        if signal_strength >= 0.5:
            return {"final_decision": "WAIT", "confidence": 0.55, "votes": [], "reasoning": "Heuristic prefers more confirmation.", "disagreement": 0.2}
        return {"final_decision": "SKIP", "confidence": 0.58, "votes": [], "reasoning": "Heuristic found insufficient edge.", "disagreement": 0.12}


class GenLayerService:
    def __init__(self, enabled: bool | None = None, retries: int | None = None, timeout_seconds: float | None = None, fallback_engine: LocalFallbackDecisionEngine | None = None) -> None:
        self.enabled = settings.genlayer_enabled if enabled is None else enabled
        self.retries = retries if retries is not None else settings.genlayer_max_retries
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.genlayer_timeout_seconds
        self.fallback_engine = fallback_engine or LocalFallbackDecisionEngine()

    def send_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = normalize_trade_payload(payload)
        log_json(logger, logging.INFO, "decision_payload", payload=normalized_payload)
        if not self.enabled:
            return {"status": "disabled", "reason": "GenLayer integration disabled via configuration.", "decision_source": "disabled", "payload": normalized_payload}

        errors: list[str] = []
        for attempt in range(1, self.retries + 1):
            try:
                contract = get_contract_at()
                result = contract.evaluate_trade(normalized_payload, timeout_seconds=self.timeout_seconds)
                log_json(logger, logging.INFO, "decision_result", path="genlayer", attempt=attempt, contract_address=contract.address, decision=result.get("decision"), tx_hash=result.get("transaction_hash"))
                return {"status": "submitted", "contract_address": contract.address, "decision_source": "genlayer", "attempt": attempt, **result}
            except Exception as exc:
                errors.append(f"attempt {attempt}: {exc}")
                log_json(logger, logging.WARNING, "decision_retry", attempt=attempt, error=str(exc), token=normalized_payload.get("token"))

        fallback_reason = "; ".join(errors) if errors else "unknown GenLayer failure"
        fallback_result = self.fallback_engine.decide(normalized_payload, fallback_reason)
        fallback_result["errors"] = errors
        return fallback_result


def send_decision(payload: dict[str, Any]) -> dict[str, Any]:
    return GenLayerService().send_decision(payload)


def build_decision_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are the Spider4AI fallback analyst. Return strict JSON with decision, confidence, reasoning.\n"
        "Use BUY, WAIT, SKIP, or SCAM only.\n"
        f"token: {payload.get('token', '')}\n"
        f"summary: {payload.get('summary', '')}\n"
        f"signal_strength: {payload.get('signal_strength')}\n"
        f"risk_flags: {payload.get('risk_flags', [])}\n"
        f"market_context: {payload.get('market_context', '')}\n"
        f"recent_trend: {payload.get('recent_trend', '')}\n"
        f"source: {payload.get('source', '')}\n"
    )


def normalize_trade_payload(payload: dict[str, Any]) -> dict[str, Any]:
    token = str(payload.get("token") or payload.get("symbol") or "").upper()
    if not token:
        raise GenLayerError("Decision payload is missing token/symbol.")
    risk_flags = payload.get("risk_flags") or []
    if not isinstance(risk_flags, list):
        raise GenLayerError("risk_flags must be a list of strings.")
    return {
        "coin_id": payload.get("coin_id"),
        "token": token,
        "symbol": token,
        "summary": str(payload.get("summary") or ""),
        "signal_strength": float(payload.get("signal_strength") or 0),
        "risk_flags": [str(flag) for flag in risk_flags],
        "market_context": str(payload.get("market_context") or ""),
        "source": str(payload.get("source") or "unknown"),
        "recent_trend": str(payload.get("recent_trend") or ""),
        "price": float(payload.get("price") or 0),
        "market_cap": float(payload.get("market_cap") or 0),
        "volume_24h": float(payload.get("volume_24h") or 0),
        "liquidity": float(payload.get("liquidity") or 0),
        "narrative": str(payload.get("narrative") or "Unknown"),
        "accumulation_score": float(payload.get("accumulation_score") or 0),
        "market_stability": float(payload.get("market_stability") or 0),
        "reason": str(payload.get("reason") or ""),
    }


def normalize_decision_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GenLayerError(f"Expected decision payload dict, got {type(payload)}")
    decision = str(payload.get("final_decision") or payload.get("decision") or "SKIP").upper()
    if decision not in ALLOWED_DECISIONS:
        raise GenLayerError(f"Unsupported decision '{decision}'.")
    confidence = float(payload.get("confidence") or 0)
    if not 0 <= confidence <= 1:
        raise GenLayerError("confidence must be between 0 and 1.")
    disagreement = float(payload.get("disagreement") or 0)
    if not 0 <= disagreement <= 1:
        raise GenLayerError("disagreement must be between 0 and 1.")
    votes = payload.get("votes") or []
    if not isinstance(votes, list):
        raise GenLayerError("votes must be a list.")
    reasoning = str(payload.get("reasoning") or "")
    return {"final_decision": decision, "confidence": round(confidence, 4), "votes": votes, "reasoning": reasoning, "disagreement": round(disagreement, 4)}


def _call_with_timeout(callable_obj: Any, timeout_seconds: float) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(callable_obj)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise GenLayerError(f"Timed out after {timeout_seconds} seconds") from exc
