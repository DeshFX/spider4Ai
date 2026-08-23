"""Off-chain service layer for Spider4AI -> GenLayer intelligent contract calls.

Decision policy: GenLayer is the ONLY decision source. When the contract is
unreachable or fails after all retries, send_decision returns a ``status``
of ``"disabled"``/``"error"`` with NO decision attached - callers must treat
the opportunity as undecided and skip execution. There is deliberately no
local AI / heuristic fallback.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

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
    account: Any = None
    wait_status: str = "ACCEPTED"

    def evaluate_trade(self, data: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        transaction_hash = _call_with_timeout(
            lambda: self.client.write_contract(
                address=self.address,
                function_name="evaluate_trade",
                account=self.account,
                args=[json.dumps(data)],
                value=0,
            ),
            timeout_seconds=timeout_seconds,
        )
        receipt = _wait_for_receipt(
            client=self.client,
            transaction_hash=transaction_hash,
            status_enum=_status_enum(self.wait_status),
            timeout_seconds=timeout_seconds,
        )
        decision = _call_with_timeout(
            lambda: _read_contract_text(
                client=self.client,
                address=self.address,
                function_name="get_last_decision",
                account=self.account,
            ),
            timeout_seconds=timeout_seconds,
        )
        normalized = normalize_decision_payload(json.loads(decision))
        return {"decision": normalized, "transaction_hash": transaction_hash, "receipt": receipt}


class GenLayerService:
    """GenLayer-only decision service.

    ``fallback_engine`` was removed by design: a failed/timeout GenLayer call
    now yields ``status="error"`` with no decision instead of delegating to a
    local model or heuristic rules.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        retries: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.enabled = settings.genlayer_enabled if enabled is None else enabled
        self.retries = retries if retries is not None else settings.genlayer_max_retries
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.genlayer_timeout_seconds

    def send_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = normalize_trade_payload(payload)
        log_json(logger, logging.INFO, "decision_payload", payload=normalized_payload)
        if not self.enabled:
            _debug_banner("[GENLAYER DISABLED]")
            return {
                "status": "disabled",
                "reason": "GenLayer integration disabled via configuration.",
                "decision_source": "disabled",
                "payload": normalized_payload,
            }

        errors: list[str] = []
        for attempt in range(1, self.retries + 1):
            try:
                contract = get_contract_at()
                result = contract.evaluate_trade(
                    normalized_payload,
                    timeout_seconds=self.timeout_seconds,
                )
                _debug_banner("[GENLAYER ACTIVE]")
                log_json(
                    logger,
                    logging.INFO,
                    "decision_result",
                    path="genlayer",
                    attempt=attempt,
                    contract_address=contract.address,
                    decision=result.get("decision"),
                    tx_hash=result.get("transaction_hash"),
                )
                return {
                    "status": "submitted",
                    "contract_address": contract.address,
                    "decision_source": "genlayer",
                    "attempt": attempt,
                    **result,
                }
            except Exception as exc:
                errors.append(f"attempt {attempt}: {exc}")
                log_json(
                    logger,
                    logging.WARNING,
                    "decision_retry",
                    attempt=attempt,
                    error=str(exc),
                    token=normalized_payload.get("token"),
                )

        # All retries exhausted: fail WITHOUT inventing a decision.
        reason = "; ".join(errors) if errors else "unknown GenLayer failure"
        log_json(
            logger,
            logging.ERROR,
            "decision_unavailable",
            token=normalized_payload.get("token"),
            reason=reason,
        )
        return {
            "status": "error",
            "decision_source": "unavailable",
            "reason": f"GenLayer unavailable, no fallback configured: {reason}",
            "errors": errors,
            "payload": normalized_payload,
        }


def send_decision(payload: dict[str, Any]) -> dict[str, Any]:
    return GenLayerService().send_decision(payload)


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
        "onchain_context": str(payload.get("onchain_context") or ""),
        "tier": str(payload.get("tier") or "mid"),
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
    return {
        "final_decision": decision,
        "confidence": round(confidence, 4),
        "votes": votes,
        "reasoning": reasoning,
        "disagreement": round(disagreement, 4),
    }


def _call_with_timeout(callable_obj: Any, timeout_seconds: float) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(callable_obj)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise GenLayerError(f"Timed out after {timeout_seconds} seconds") from exc


def _wait_for_receipt(
    client: Any,
    transaction_hash: str,
    status_enum: Any,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for a receipt, tolerating transitional statuses the SDK cannot decode.

    genlayer_py crashes (KeyError) when a transaction passes through a status
    number missing from its TRANSACTION_STATUS_NUMBER_TO_NAME map (e.g. '14'
    observed on Bradbury during appeal/finalization transitions). Such states
    are transient, so poll again until the deadline instead of failing.
    """
    import time as _time

    deadline = _time.time() + max(timeout_seconds, 5)
    last_error: Exception | None = None
    while _time.time() < deadline:
        try:
            return client.wait_for_transaction_receipt(
                transaction_hash=transaction_hash,
                status=status_enum,
                interval=5000,
                retries=6,
            )
        except Exception as exc:  # noqa: BLE001 - any SDK decode/poll failure is retryable here
            last_error = exc
            _time.sleep(3)
    raise GenLayerError(f"Timed out waiting for receipt {transaction_hash}: {last_error}")


def _debug_banner(message: str) -> None:
    print(message)


def _status_enum(value: Any) -> Any:
    """Coerce a status name string into the SDK TransactionStatus enum."""
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    try:
        from genlayer_py.types import TransactionStatus

        return TransactionStatus(value)
    except Exception:
        return value


def _read_contract_text(
    client: Any,
    address: str,
    function_name: str,
    account: Any,
) -> str:
    """Read a view returning a calldata-encoded string, tolerating SDK response shapes.

    genlayer_py 0.18.0's read_contract expects a plain hex result on localnet, but
    Bradbury's gen_call returns {"data": <hex>}. The returned bytes are the calldata
    encoding of a string, which this helper decodes back into the text value.
    """
    from genlayer_py.abi import calldata
    from genlayer_py.abi.calldata.encoder import encode as calldata_encode
    from genlayer_py.consensus.consensus_main.encoder import serialize
    from genlayer_py.contracts.utils import make_calldata_object

    encoded_args = calldata_encode(
        make_calldata_object(method=function_name, args=[], kwargs=None)
    )
    serialized_data = serialize([encoded_args, b"\x00"])
    request_params = {
        "type": "read",
        "to": address,
        "from": account.address,
        "data": serialized_data,
        "transaction_hash_variant": "latest-nonfinal",
    }
    result = client.provider.make_request(method="gen_call", params=[request_params])["result"]
    hex_data = result["data"] if isinstance(result, dict) else result
    decoded = calldata.decode(bytes.fromhex(hex_data))
    if isinstance(decoded, str):
        return decoded
    return json.dumps(decoded, default=str)