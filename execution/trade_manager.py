"""Execution safety, position sizing, cooldowns, and exit management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from config import settings
from engine.confidence_scorer import ConfidenceScorer
from execution.sepolia_executor import SepoliaExecutor
from notifications.telegram import send_telegram_message
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

    def __init__(self, db: Database | None = None, cambrian: Any | None = None) -> None:
        self.db = db or Database()
        self.cambrian = cambrian

    def should_open_position(self, opportunity: dict[str, Any]) -> tuple[bool, str]:
        if self.db.is_blacklisted(opportunity.get("coin_id"), opportunity.get("symbol")):
            return False, "token_blacklisted"
        if str(opportunity.get("genlayer_decision") or "").upper() == "SCAM":
            return False, "scam_flagged"
        if settings.circuit_breaker_enabled and self.db.is_circuit_breaker_paused(settings.circuit_breaker_pause_minutes):
            return False, "circuit_breaker_paused"
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
        composite = ConfidenceScorer().score(opportunity)
        opportunity["confidence_score"] = composite
        confidence = composite / 100.0
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
                "tracked_dev_wallet": opportunity.get("tracked_dev_wallet"),
                "tracked_top_holder_wallets": opportunity.get("tracked_top_holder_wallets") or [],
            }
        )
        self.db.record_trade_event(
            opportunity.get("symbol", ""),
            "ENTRY",
            {"plan": plan.__dict__, "tx_hash": tx_hash},
        )
        self._send_open_notification(opportunity, plan)
        log_json(
            logger,
            logging.INFO,
            "position_opened",
            symbol=opportunity.get("symbol"),
            plan=plan.__dict__,
            tx_hash=tx_hash,
        )
        return position_id

    def check_circuit_breaker(self) -> bool:
        """Pause trading after N consecutive losses; returns True when triggered."""
        if not settings.circuit_breaker_enabled:
            return False
        losses = self.db.count_consecutive_losses()
        if losses < settings.circuit_breaker_max_losses:
            return False
        if self.db.is_circuit_breaker_paused(settings.circuit_breaker_pause_minutes):
            return False
        self.db.record_trade_event(
            "SYSTEM",
            "CIRCUIT_BREAKER",
            {
                "reason": f"{losses} consecutive losses",
                "consecutive_losses": losses,
                "pause_minutes": settings.circuit_breaker_pause_minutes,
            },
        )
        log_json(
            logger,
            logging.WARNING,
            "circuit_breaker_triggered",
            consecutive_losses=losses,
            pause_minutes=settings.circuit_breaker_pause_minutes,
        )
        return True

    def evaluate_exit(self, position: dict[str, Any], current_price: float, partial_mode: bool = False) -> tuple[str, float]:
        entry_price = max(float(position.get("entry_price") or 0), 1e-9)
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        if current_price <= float(position.get("stop_loss_price") or 0):
            return "STOP_LOSS", pnl_pct
        if not partial_mode and current_price >= float(position.get("take_profit_price") or 0):
            return "TAKE_PROFIT", pnl_pct
        peak_price = max(float(position.get("peak_price") or entry_price), current_price)
        trailing_floor = peak_price * (
            1 - float(position.get("trailing_stop_pct") or settings.trailing_stop_pct)
        )
        if current_price < trailing_floor and peak_price > entry_price:
            return "TRAILING_STOP", pnl_pct
        return "HOLD", pnl_pct

    def check_exit_conditions(self, position: dict[str, Any], current_price: float, now: datetime | None = None) -> dict[str, Any]:
        """Run the three auto-exit triggers for an OPEN position.

        Priority order (checked each monitoring cycle):
          1. CRASH_EXIT    — price crashed X% within Y minutes
          2. DEV_WHALE_EXIT — tracked dev/top-holder dumped > Y% of holding
          3. PARTIAL_SELL  — price >= 2x entry, first time only (moonbag)

        Actions are applied to the DB here; callers only log/notify.
        """
        now = now or datetime.utcnow()

        crash = self._check_crash_exit(position, current_price, now)
        if crash:
            self._apply_full_exit(position, current_price, "CRASH_EXIT", crash)
            return {"action": "CRASH_EXIT", "reason": crash, "price": current_price}

        dev = self._check_dev_whale_exit(position)
        if dev:
            self._apply_full_exit(position, current_price, "DEV_WHALE_EXIT", dev)
            return {"action": "DEV_WHALE_EXIT", "reason": dev, "price": current_price}

        partial = self._check_take_profit_partial(position, current_price)
        if partial:
            self._apply_partial_sell(position, current_price, now)
            return {"action": "PARTIAL_SELL", "reason": partial, "price": current_price}

        self.db.update_position_peak(position["id"], current_price)
        self.db.update_position_last_price(position["id"], current_price, now.isoformat())
        return {"action": "HOLD", "reason": "", "price": current_price}

    def _check_crash_exit(self, position: dict[str, Any], current_price: float, now: datetime) -> str | None:
        """Trigger when price dropped >= X% from the last cycle within Y minutes."""
        drop_pct = settings.exit_crash_drop_pct
        window_minutes = settings.exit_crash_window_minutes
        last_price = float(position.get("last_price") or position.get("entry_price") or 0)
        last_at = position.get("last_price_at") or position.get("opened_at")
        if last_at:
            last_dt = self._parse_iso(last_at)
            if last_dt is not None and (now - last_dt).total_seconds() > window_minutes * 60:
                return None
        if last_price > 0 and current_price <= last_price * (1 - drop_pct / 100.0):
            drop = (1 - current_price / last_price) * 100
            return (
                f"{position.get('symbol')} dropped {drop:.1f}% "
                f"(>={drop_pct}%) within {window_minutes} minutes"
            )
        return None

    def _check_take_profit_partial(self, position: dict[str, Any], current_price: float) -> str | None:
        """Trigger once when price reaches the partial-TP multiplier (2x default)."""
        if int(position.get("partial_sell_done") or 0):
            return None
        entry = float(position.get("entry_price") or 0)
        if entry <= 0:
            return None
        target = entry * settings.exit_take_profit_partial_multiplier
        if current_price >= target:
            return (
                f"{position.get('symbol')} hit {settings.exit_take_profit_partial_multiplier:.1f}x entry; "
                f"sell {settings.exit_take_profit_partial_sell_pct:.0f}% (moonbag for the rest)"
            )
        return None

    def _check_dev_whale_exit(self, position: dict[str, Any]) -> str | None:
        """Trigger when a tracked dev/top-holder dumped > Y% of their holding."""
        wallets = self._tracked_wallets(position)
        if not wallets:
            return None
        cambrian = self.cambrian
        if cambrian is None:
            from data.cambrian_client import get_cambrian_client

            cambrian = get_cambrian_client()
        sell_pct = settings.exit_dev_whale_sell_pct
        for wallet in wallets:
            try:
                activity = cambrian.get_wallet_activity(wallet, include_history=True)
            except Exception:
                logger.debug("Could not fetch wallet activity for %s", wallet, exc_info=True)
                continue
            drop = float(activity.get("balance_drop_pct") or 0)
            if drop >= sell_pct:
                return (
                    f"{position.get('symbol')} tracked wallet {wallet[:10]}... "
                    f"sold {drop:.1f}% of holding (>={sell_pct:.0f}%)"
                )
        return None

    @staticmethod
    def _tracked_wallets(position: dict[str, Any]) -> list[str]:
        wallets: list[str] = []
        dev = position.get("tracked_dev_wallet")
        if dev:
            wallets.append(str(dev))
        try:
            holders = json.loads(position.get("tracked_top_holder_wallets") or "[]")
        except (TypeError, ValueError):
            holders = []
        for holder in holders:
            if isinstance(holder, dict):
                address = holder.get("address")
            else:
                address = holder
            if address and str(address) not in wallets:
                wallets.append(str(address))
        return wallets

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def _apply_full_exit(self, position: dict[str, Any], current_price: float, exit_reason: str, detail: str) -> None:
        entry = max(float(position.get("entry_price") or 0), 1e-9)
        pnl_pct = ((current_price - entry) / entry) * 100
        self.db.close_position(position["id"], current_price, exit_reason, pnl_pct)
        self.db.record_trade_event(
            position.get("symbol", ""),
            exit_reason,
            {"reason": detail, "pnl_pct": pnl_pct, "current_price": current_price},
        )
        if exit_reason == "CRASH_EXIT":
            self.check_circuit_breaker()
        self._send_exit_notification(
            position,
            exit_reason,
            detail,
            float(position.get("size_usd") or 0),
        )
        self._broadcast_sell(position, current_price, exit_reason)
        log_json(
            logger,
            logging.INFO,
            "position_exited",
            symbol=position.get("symbol"),
            exit_reason=exit_reason,
            pnl_pct=round(pnl_pct, 4),
            current_price=current_price,
            detail=detail,
        )

    def _apply_partial_sell(self, position: dict[str, Any], current_price: float, now: datetime) -> None:
        self.db.mark_partial_sell(position["id"])
        self.db.update_position_last_price(position["id"], current_price, now.isoformat())
        sell_pct = settings.exit_take_profit_partial_sell_pct
        detail = (
            f"{position.get('symbol')} reached {settings.exit_take_profit_partial_multiplier:.1f}x entry; "
            f"sold {sell_pct:.0f}%, remaining is moonbag"
        )
        self.db.record_trade_event(
            position.get("symbol", ""),
            "PARTIAL_SELL",
            {"current_price": current_price, "sell_pct": sell_pct, "reason": detail},
        )
        self._send_exit_notification(
            position,
            "TAKE_PROFIT",
            detail,
            float(position.get("size_usd") or 0) * sell_pct / 100.0,
        )
        self._broadcast_sell(position, current_price, "PARTIAL_SELL")
        log_json(
            logger,
            logging.INFO,
            "position_partial_sold",
            symbol=position.get("symbol"),
            sell_pct=sell_pct,
            current_price=current_price,
        )

    @staticmethod
    def _notification_text(position: dict[str, Any], action: str, amount_usd: float, detail: str = "") -> str:
        """Build a detailed Telegram message: ticker, contract address, amount."""
        symbol = position.get("symbol", "")
        coin_id = position.get("coin_id") or ""
        price = float(
            position.get("last_price")
            or position.get("entry_price")
            or position.get("price")
            or 0
        )
        lines = [action]
        if symbol:
            lines.append(f"Ticker: {symbol}")
        if coin_id:
            lines.append(f"CA: {coin_id}")
        lines.append(f"Amount: ${amount_usd:.2f}")
        if price:
            lines.append(f"Price: ${price:.8g}")
        if detail:
            lines.append(f"Reason: {detail}")
        return "\n".join(lines)

    def _send_open_notification(self, opportunity: dict[str, Any], plan: TradePlan) -> None:
        send_telegram_message(
            self._notification_text(opportunity, "BUY", float(plan.size_usd or 0))
        )

    @staticmethod
    def _send_exit_notification(position: dict[str, Any], action: str, detail: str, amount_usd: float) -> None:
        send_telegram_message(TradeManager._notification_text(position, action, amount_usd, detail))

    @staticmethod
    def _broadcast_sell(position: dict[str, Any], current_price: float, reason: str) -> None:
        """Broadcast a real exit transaction; no-op in DRY_RUN mode.

        The Sepolia executor only simulates a testnet self-transfer (matching
        the existing paper-execution bridge), so a live dex sell is out of
        scope — DRY_RUN always stays simulated.
        """
        if settings.dry_run:
            return
        if settings.sepolia_rpc_url and settings.wallet_private_key:
            try:
                SepoliaExecutor().simulate_test_transaction()
                log_json(
                    logger,
                    logging.INFO,
                    "exit_broadcast_simulated",
                    symbol=position.get("symbol"),
                    reason=reason,
                )
            except Exception as exc:
                log_json(
                    logger,
                    logging.ERROR,
                    "exit_broadcast_failed",
                    symbol=position.get("symbol"),
                    reason=reason,
                    error=str(exc),
                )

    def monitor_positions(self, markets: list[dict[str, Any]]) -> None:
        price_map = {
            str(item.get("symbol", "")).upper(): float(item.get("current_price") or 0)
            for item in markets
        }
        for position in self.db.get_open_positions():
            current_price = price_map.get(position.get("symbol", "").upper())
            if current_price is None:
                continue
            action = self.check_exit_conditions(position, current_price)
            if action["action"] != "HOLD":
                continue
            exit_reason, pnl_pct = self.evaluate_exit(
                position,
                current_price,
                partial_mode=bool(position.get("partial_sell_done")),
            )
            if exit_reason == "HOLD":
                continue
            self.db.close_position(position["id"], current_price, exit_reason, pnl_pct)
            self.db.record_trade_event(
                position.get("symbol", ""),
                exit_reason,
                {"pnl_pct": pnl_pct, "current_price": current_price},
            )
            self.check_circuit_breaker()
            log_json(
                logger,
                logging.INFO,
                "position_closed",
                symbol=position.get("symbol"),
                exit_reason=exit_reason,
                pnl_pct=round(pnl_pct, 4),
                current_price=current_price,
            )