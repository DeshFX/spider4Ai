"""Execution safety, position sizing, cooldowns, and exit management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config import settings
from storage.database import Database
from structured_logging import log_json

logger = logging.getLogger(__name__)
CRITICAL_RISK_FLAGS = {"blacklisted_token", "scam_flag", "rug_risk", "honeypot_risk"}


def calculate_position_size(confidence: float) -> float:
    """Calculate a safe ETH position size from model confidence."""
    normalized_confidence = min(max(float(confidence or 0.0), 0.0), 1.0)
    base_size = 0.0003
    raw_size = base_size + (normalized_confidence * base_size)
    final_size = max(settings.min_trade_size_eth, min(raw_size, settings.max_trade_size_eth))
    return round(final_size, 8)


@dataclass
class TradePlan:
    size_pct: float
    size_usd: float
    take_profit_price: float
    stop_loss_price: float
    trailing_stop_pct: float


class TradeManager:
    """Protects execution with guardrails, position sizing, and exit logic."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()

    def should_open_position(self, opportunity: dict[str, Any]) -> tuple[bool, str]:
        if self.db.is_blacklisted(opportunity.get("coin_id"), opportunity.get("symbol")):
            return False, "token_blacklisted"
        if str(opportunity.get("genlayer_decision") or "").upper() == "SCAM":
            return False, "scam_flagged"
        if float(opportunity.get("genlayer_confidence") or 0) < max(0.7, settings.min_trade_confidence):
            return False, "confidence_below_threshold"
        if any(flag in CRITICAL_RISK_FLAGS for flag in opportunity.get("risk_flags", [])):
            return False, "critical_risk_flag"
        if float(opportunity.get("genlayer_disagreement") or 0) >= settings.max_validator_disagreement:
            return False, "validator_disagreement"
        if self.db.in_global_cooldown(settings.global_cooldown_seconds):
            return False, "global_cooldown"
        if self.db.in_token_cooldown(opportunity.get("symbol", ""), settings.token_cooldown_seconds):
            return False, "token_cooldown"
        return True, "approved"

    def compute_position_size(self, opportunity: dict[str, Any]) -> TradePlan:
        confidence = float(opportunity.get("genlayer_confidence") or 0)
        disagreement = float(opportunity.get("genlayer_disagreement") or 0)
        market_stability = float(opportunity.get("market_stability") or 0)
        min_pct = settings.min_position_pct
        max_pct = settings.max_position_pct
        pct_range = max_pct - min_pct

        normalized_conf = min(
            max(
                (confidence - settings.min_trade_confidence)
                / max(1e-6, 1 - settings.min_trade_confidence),
                0.0,
            ),
            1.0,
        )
        base_pct = min_pct + (pct_range * normalized_conf)
        if market_stability < 0.45:
            base_pct *= 0.75
        if disagreement < 0.2:
            base_pct *= 1.1
        if disagreement > 0.35:
            base_pct *= 0.8

        size_pct = min(max(base_pct, min_pct), max_pct)
        capital = settings.paper_capital_usd
        size_usd = min(capital * size_pct, settings.max_trade_size_usd)
        entry_price = max(float(opportunity.get("price") or 0), 1e-9)
        tp_multiplier = 1 + settings.take_profit_pct + (max(0.0, confidence - 0.7) * 0.1)
        sl_multiplier = 1 - settings.stop_loss_pct
        return TradePlan(
            size_pct=round(size_pct, 4),
            size_usd=round(size_usd, 2),
            take_profit_price=round(entry_price * tp_multiplier, 8),
            stop_loss_price=round(entry_price * sl_multiplier, 8),
            trailing_stop_pct=settings.trailing_stop_pct,
        )

    def record_open_position(self, opportunity: dict[str, Any], plan: TradePlan, tx_hash: str | None) -> int:
        position_id = self.db.insert_position(
            {
                "coin_id": opportunity.get("coin_id"),
                "symbol": opportunity.get("symbol"),
                "decision_source": opportunity.get("decision_source"),
                "entry_price": opportunity.get("price"),
                "size_usd": plan.size_usd,
                "size_pct": plan.size_pct,
                "take_profit_price": plan.take_profit_price,
                "stop_loss_price": plan.stop_loss_price,
                "trailing_stop_pct": plan.trailing_stop_pct,
                "status": "OPEN",
                "execution_tx_hash": tx_hash,
            }
        )
        self.db.record_trade_event(
            opportunity.get("symbol", ""),
            "ENTRY",
            {"plan": plan.__dict__, "tx_hash": tx_hash},
        )
        log_json(
            logger,
            logging.INFO,
            "position_opened",
            symbol=opportunity.get("symbol"),
            plan=plan.__dict__,
            tx_hash=tx_hash,
        )
        return position_id

    def evaluate_exit(self, position: dict[str, Any], current_price: float) -> tuple[str, float]:
        entry_price = max(float(position.get("entry_price") or 0), 1e-9)
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        if current_price <= float(position.get("stop_loss_price") or 0):
            return "STOP_LOSS", pnl_pct
        if current_price >= float(position.get("take_profit_price") or 0):
            return "TAKE_PROFIT", pnl_pct
        peak_price = max(float(position.get("peak_price") or entry_price), current_price)
        trailing_floor = peak_price * (
            1 - float(position.get("trailing_stop_pct") or settings.trailing_stop_pct)
        )
        if current_price < trailing_floor and peak_price > entry_price:
            return "TRAILING_STOP", pnl_pct
        return "HOLD", pnl_pct

    def monitor_positions(self, markets: list[dict[str, Any]]) -> None:
        price_map = {
            str(item.get("symbol", "")).upper(): float(item.get("current_price") or 0)
            for item in markets
        }
        for position in self.db.get_open_positions():
            current_price = price_map.get(position.get("symbol", "").upper())
            if current_price is None:
                continue
            exit_reason, pnl_pct = self.evaluate_exit(position, current_price)
            self.db.update_position_peak(position["id"], current_price)
            if exit_reason == "HOLD":
                continue
            self.db.close_position(position["id"], current_price, exit_reason, pnl_pct)
            self.db.record_trade_event(
                position.get("symbol", ""),
                exit_reason,
                {"pnl_pct": pnl_pct, "current_price": current_price},
            )
            log_json(
                logger,
                logging.INFO,
                "position_closed",
                symbol=position.get("symbol"),
                exit_reason=exit_reason,
                pnl_pct=round(pnl_pct, 4),
                current_price=current_price,
            )
