"""Off-chain rugpull/scam hard-block layer for Spider4AI.

Layer 1 of the two-layer rug protection:
  - Layer 1 (this module): deterministic, objective on-chain checks that hard
    block a token without waiting for GenLayer validators.
  - Layer 2: nuanced on-chain context is forwarded to GenLayer for reasoning.

Signals used:
  - Post-launch supply minting (rug dilution)
  - Extreme top-holder concentration
  - Large liquidity removal
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from data.cambrian_client import (
    get_holders,
    get_mint_burn,
    get_pool_transactions,
    get_token_security,
)
from structured_logging import log_json

logger = logging.getLogger(__name__)


class RugChecker:
    """Deterministic off-chain checks for rugpull / scam signals."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def check(self, token_address: str, pool_address: str = "") -> dict[str, Any]:
        """Return an assessment dict; ``blocked`` True means hard reject."""
        if not token_address:
            return {"blocked": True, "reason": "missing token address", "flags": ["missing_address"]}

        security = self._security(token_address)
        flags: list[str] = []
        reasons: list[str] = []

        top_holder_pct = self._top_holder_pct(token_address)
        if top_holder_pct >= settings.rugcheck_top_holder_pct:
            flags.append("top_holder_concentration")
            reasons.append(f"top holder holds {top_holder_pct:.1f}%")

        if security.get("top10_pct", 0) >= settings.rugcheck_top10_holder_pct:
            flags.append("top10_concentration")
            reasons.append(f"top-10 holders hold {security.get('top10_pct', 0):.1f}%")

        mint_events = self._mint_events(token_address)
        if mint_events:
            flags.append("post_launch_mint")
            reasons.append(f"{len(mint_events)} recent supply mint event(s)")

        if pool_address:
            liquidity_removals = self._liquidity_removals(pool_address)
            if liquidity_removals:
                flags.append("liquidity_removal")
                reasons.append(f"{len(liquidity_removals)} recent liquidity removal(s)")

        blocked = len(flags) > 0
        log_json(
            logger,
            logging.INFO,
            "rug_check",
            token=token_address,
            blocked=blocked,
            flags=flags,
            reason="; ".join(reasons),
        )
        return {
            "blocked": blocked,
            "reason": "; ".join(reasons) or "no rug flags",
            "flags": flags,
            "top_holder_pct": round(top_holder_pct, 4),
            "top10_pct": round(security.get("top10_pct", 0), 4),
            "mint_events": len(mint_events),
            "liquidity_removals": len(liquidity_removals) if pool_address else 0,
        }

    def _security(self, token_address: str) -> dict[str, Any]:
        if self.client is not None:
            return self.client.get_token_security(token_address)
        return get_token_security(token_address)

    def _top_holder_pct(self, token_address: str) -> float:
        holders = get_holders(token_address, limit=1) if self.client is None else self.client.get_holders(token_address, limit=1)
        if not holders:
            return 0.0
        pct = holders[0].get("pct")
        if pct:
            return max(0.0, min(100.0, float(pct)))
        balance = float(holders[0].get("balance") or 0)
        if balance <= 0:
            return 0.0
        total = float(self._security(token_address).get("total_supply") or 0)
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, balance / total * 100.0))

    def _mint_events(self, token_address: str) -> list[dict[str, Any]]:
        events = get_mint_burn(token_address, limit=20) if self.client is None else self.client.get_mint_burn(token_address, limit=20)
        return [event for event in events if event.get("type") in ("mint", "minted", "supply_mint")]

    def _liquidity_removals(self, pool_address: str) -> list[dict[str, Any]]:
        txs = get_pool_transactions(pool_address, limit=20) if self.client is None else self.client.get_pool_transactions(pool_address, limit=20)
        return [
            tx
            for tx in txs
            if any(k in str(tx.get("type")).lower() for k in ("remove_liquidity", "remove", "burn", "withdraw"))
        ]
