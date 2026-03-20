"""Spider4AI autonomous loop orchestration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from data.coingecko_fetcher import CoinGeckoFetcher
from data.dexscreener_fetcher import DexscreenerFetcher
from data.narrative_detector import NarrativeDetector
from data.social_scanner import SocialScanner
from engine.accumulation_detector import AccumulationDetector
from engine.risk_filter import RiskFilter
from engine.scoring_engine import ScoringEngine
from execution.sepolia_executor import SepoliaExecutor
from execution.trade_manager import TradeManager
from genlayer.service import GenLayerService
from storage.database import Database
from structured_logging import log_json

logger = logging.getLogger(__name__)


class SpiderAgent:
    def __init__(self, decision_service: GenLayerService | None = None, db: Database | None = None) -> None:
        self.db = db or Database()
        self.coingecko = CoinGeckoFetcher()
        self.dex = DexscreenerFetcher()
        self.narrative = NarrativeDetector()
        self.social = SocialScanner()
        self.accumulation = AccumulationDetector()
        self.scorer = ScoringEngine()
        self.risk = RiskFilter()
        self.decision_service = decision_service or GenLayerService()
        self.trade_manager = TradeManager(self.db)

    def build_decision_payload(
        self,
        coin: dict[str, Any],
        opportunity: dict[str, Any],
        risk_flags: list[str],
        market_stability: float,
        recent_trend: str,
    ) -> dict[str, Any]:
        summary = (
            f"{coin.get('name')} ({opportunity.get('symbol')}) scores {opportunity.get('score'):.2f} / 100, "
            f"narrative {opportunity.get('narrative')}, accumulation {opportunity.get('accumulation_score'):.2f}, "
            f"liquidity ${opportunity.get('liquidity'):.2f}, volume ${opportunity.get('volume_24h'):.2f}."
        )
        market_context = (
            f"Market cap ${coin.get('market_cap', 0):.2f}, price ${opportunity.get('price', 0):.6f}, "
            f"24h change {coin.get('price_change_percentage_24h', 0):.2f}%, stability {market_stability:.2f}."
        )
        return {
            "coin_id": coin.get("id"),
            "token": opportunity.get("symbol"),
            "symbol": opportunity.get("symbol"),
            "summary": summary,
            "signal_strength": min(max(opportunity.get("score", 0) / 100, 0.0), 1.0),
            "risk_flags": risk_flags,
            "market_context": market_context,
            "source": "coingecko+dexscreener+social",
            "recent_trend": recent_trend,
            "price": opportunity.get("price"),
            "score": opportunity.get("score"),
            "market_cap": coin.get("market_cap"),
            "volume_24h": opportunity.get("volume_24h"),
            "liquidity": opportunity.get("liquidity"),
            "narrative": opportunity.get("narrative"),
            "accumulation_score": opportunity.get("accumulation_score"),
            "market_stability": market_stability,
            "reason": opportunity.get("reason"),
        }

    def _apply_genlayer_decision(self, opportunity: dict[str, Any], payload: dict[str, Any]) -> None:
        result = self.decision_service.send_decision(payload)
        opportunity["genlayer_status"] = result.get("status", "unknown")
        opportunity["genlayer_tx_hash"] = result.get("transaction_hash")
        opportunity["decision_source"] = result.get("decision_source", "unknown")
        decision = result.get("decision") or {}
        opportunity["genlayer_decision"] = decision.get("final_decision", "WAIT")
        opportunity["genlayer_confidence"] = decision.get("confidence", 0.0)
        opportunity["genlayer_reasoning"] = decision.get("reasoning", result.get("reason", "No decision returned"))
        opportunity["genlayer_votes"] = decision.get("votes", [])
        opportunity["genlayer_disagreement"] = decision.get("disagreement", 0.0)
        log_json(
            logger,
            logging.INFO,
            "decision_applied",
            symbol=opportunity.get("symbol"),
            decision=decision,
            source=opportunity.get("decision_source"),
        )
        log_json(logger, logging.INFO, "decision_applied", symbol=opportunity.get("symbol"), decision=decision, source=opportunity.get("decision_source"))

    def _execute_decision(self, opportunity: dict[str, Any]) -> None:
        decision = opportunity.get("genlayer_decision")
        if decision == "SCAM":
            opportunity["execution_status"] = "blacklisted"
            opportunity["risk_flags"] = list(
                set(opportunity.get("risk_flags", [])) | {"blacklisted_token"}
            )
            self.db.blacklist_token(
                opportunity.get("coin_id"),
                opportunity.get("symbol", ""),
                opportunity.get("genlayer_reasoning", "SCAM decision"),
                opportunity.get("decision_source", "unknown"),
            )
            self.db.record_trade_event(
                opportunity.get("symbol", ""),
                "SCAM",
                {"reason": opportunity.get("genlayer_reasoning")},
            )
            log_json(
                logger,
                logging.WARNING,
                "token_blacklisted",
                symbol=opportunity.get("symbol"),
                reason=opportunity.get("genlayer_reasoning"),
                source=opportunity.get("decision_source"),
            )
            opportunity["risk_flags"] = list(set(opportunity.get("risk_flags", [])) | {"blacklisted_token"})
            self.db.blacklist_token(opportunity.get("coin_id"), opportunity.get("symbol", ""), opportunity.get("genlayer_reasoning", "SCAM decision"), opportunity.get("decision_source", "unknown"))
            self.db.record_trade_event(opportunity.get("symbol", ""), "SCAM", {"reason": opportunity.get("genlayer_reasoning")})
            log_json(logger, logging.WARNING, "token_blacklisted", symbol=opportunity.get("symbol"), reason=opportunity.get("genlayer_reasoning"), source=opportunity.get("decision_source"))
            return
        if decision != "BUY":
            opportunity["execution_status"] = "deferred" if decision == "WAIT" else "skipped"
            return

        approved, reason = self.trade_manager.should_open_position(opportunity)
        if not approved:
            opportunity["execution_status"] = f"blocked:{reason}"
            self.db.record_trade_event(opportunity.get("symbol", ""), "BLOCKED", {"reason": reason})
            log_json(logger, logging.INFO, "execution_blocked", symbol=opportunity.get("symbol"), reason=reason)
            return

        plan = self.trade_manager.compute_position_size(opportunity)
        tx_hash = None
        if settings.dry_run:
            opportunity["execution_status"] = "dry_run"
        elif settings.sepolia_rpc_url and settings.wallet_private_key:
        if settings.sepolia_rpc_url and settings.wallet_private_key:
            try:
                tx_hash = SepoliaExecutor().simulate_test_transaction()
                opportunity["execution_status"] = "submitted"
            except Exception as exc:
                opportunity["execution_status"] = f"failed:{exc}"
                log_json(
                    logger,
                    logging.ERROR,
                    "execution_failed",
                    symbol=opportunity.get("symbol"),
                    error=str(exc),
                )
                log_json(logger, logging.ERROR, "execution_failed", symbol=opportunity.get("symbol"), error=str(exc))
                return
        else:
            opportunity["execution_status"] = "paper_position_opened"
        opportunity["execution_tx_hash"] = tx_hash
        opportunity["position_size_pct"] = plan.size_pct
        opportunity["position_size_usd"] = plan.size_usd
        opportunity["take_profit_price"] = plan.take_profit_price
        opportunity["stop_loss_price"] = plan.stop_loss_price
        opportunity["trailing_stop_pct"] = plan.trailing_stop_pct
        self.trade_manager.record_open_position(opportunity, plan, tx_hash)
        log_json(
            logger,
            logging.INFO,
            "execution_result",
            symbol=opportunity.get("symbol"),
            status=opportunity.get("execution_status"),
            tx_hash=tx_hash,
            size_usd=plan.size_usd,
        )
        log_json(logger, logging.INFO, "execution_result", symbol=opportunity.get("symbol"), status=opportunity.get("execution_status"), tx_hash=tx_hash, size_usd=plan.size_usd)

    def run_cycle(self) -> list[dict[str, Any]]:
        markets = self.coingecko.fetch_mid_cap_markets(200)
        if not markets:
            logger.warning("No market data returned from CoinGecko; skipping cycle")
            return []
        self.trade_manager.monitor_positions(markets)
        self.db.insert_market_data(markets)
        dex_batch = self.dex.fetch_trending_pairs()
        self.db.insert_dex_data(dex_batch)
        dex_map = {row.get("symbol", "").upper(): row for row in dex_batch if row.get("symbol")}

        opportunities: list[dict[str, Any]] = []
        for coin in markets:
            try:
                symbol = coin.get("symbol", "").upper()
                if self.db.is_blacklisted(coin.get("id"), symbol):
                    log_json(logger, logging.INFO, "evaluation_skipped", symbol=symbol, reason="blacklisted")
                    continue
                narrative, narrative_conf, reasoning = self.narrative.classify(coin)
                dex_data = dex_map.get(symbol) or self.dex.pair_lookup(symbol)
                accumulation_score = self.accumulation.score(coin, dex_data)
                volume_momentum = min((coin.get("total_volume", 0) / max(coin.get("market_cap", 1), 1)) * 5, 1)
                liquidity_health = min((dex_data.get("liquidity", 0) / 2_000_000), 1)
                market_stability = max(0.0, 1 - abs((coin.get("price_change_percentage_24h") or 0) / 25))
                social_boost = self.social.fetch_social_score(coin) * 0.05
                score = self.scorer.score(
                    narrative_confidence=min(1.0, narrative_conf + social_boost),
                    volume_momentum=min(1.0, volume_momentum),
                    liquidity_health=min(1.0, liquidity_health),
                    accumulation_score=accumulation_score,
                    market_stability=min(1.0, market_stability),
                )
                safe, risk_reason = self.risk.is_safe(coin, dex_data)
                if not safe:
                    continue
                risk_flags = self._build_risk_flags(coin, dex_data, market_stability)
                recent_trend = self._build_recent_trend(coin)
                opportunity = {
                    "coin_id": coin.get("id"),
                    "symbol": symbol,
                    "narrative": narrative,
                    "score": score,
                    "accumulation_score": accumulation_score,
                    "volume_24h": coin.get("total_volume", 0),
                    "liquidity": dex_data.get("liquidity", 0),
                    "price": coin.get("current_price", 0),
                    "reason": f"{reasoning}; {risk_reason}",
                    "market_stability": market_stability,
                }
                payload = self.build_decision_payload(
                    coin,
                    opportunity,
                    risk_flags,
                    market_stability,
                    recent_trend,
                )
                opportunity.update(
                    {
                        "summary": payload["summary"],
                        "risk_flags": payload["risk_flags"],
                        "signal_strength": payload["signal_strength"],
                        "source": payload["source"],
                        "market_context": payload["market_context"],
                        "recent_trend": payload["recent_trend"],
                    }
                )
                    "coin_id": coin.get("id"), "symbol": symbol, "narrative": narrative, "score": score,
                    "accumulation_score": accumulation_score, "volume_24h": coin.get("total_volume", 0),
                    "liquidity": dex_data.get("liquidity", 0), "price": coin.get("current_price", 0),
                    "reason": f"{reasoning}; {risk_reason}", "market_stability": market_stability,
                }
                payload = self.build_decision_payload(coin, opportunity, risk_flags, market_stability, recent_trend)
                opportunity.update({
                    "summary": payload["summary"], "risk_flags": payload["risk_flags"], "signal_strength": payload["signal_strength"],
                    "source": payload["source"], "market_context": payload["market_context"], "recent_trend": payload["recent_trend"],
                })
                self._apply_genlayer_decision(opportunity, payload)
                self._execute_decision(opportunity)
                opportunities.append(opportunity)
            except Exception as exc:
                logger.exception("Failed processing coin=%s due to %s", coin.get("symbol"), exc)
        opportunities.sort(key=lambda x: x["score"], reverse=True)
        self.db.insert_opportunities(opportunities)
        return opportunities

    def _build_recent_trend(self, coin: dict[str, Any]) -> str:
        change = float(coin.get("price_change_percentage_24h") or 0)
        if change >= 10:
            return "Strong upside momentum in the last 24h."
        if change <= -10:
            return "Sharp downside move in the last 24h."
        return "Mixed short-term price action with no extreme trend."

    def _build_risk_flags(
        self,
        coin: dict[str, Any],
        dex_data: dict[str, Any],
        market_stability: float,
    ) -> list[str]:
    def _build_risk_flags(self, coin: dict[str, Any], dex_data: dict[str, Any], market_stability: float) -> list[str]:
        flags: list[str] = []
        if market_stability < 0.35:
            flags.append("high_volatility")
        if float(dex_data.get("liquidity", 0) or 0) < 150_000:
            flags.append("thin_liquidity")
        if float(coin.get("total_volume", 0) or 0) < 500_000:
            flags.append("weak_volume")
        if abs(float(coin.get("price_change_percentage_24h") or 0)) > 20:
            flags.append("elevated_price_swing")
        if float(dex_data.get("liquidity", 0) or 0) > 0 and float(coin.get("total_volume", 0) or 0) / max(float(dex_data.get("liquidity", 0) or 1), 1) > 25:
            flags.append("orderflow_imbalance")
        return flags

    def start_scheduler(self) -> BackgroundScheduler:
        scheduler = BackgroundScheduler()
        scheduler.add_job(self.run_cycle, "interval", minutes=settings.scheduler_minutes)
        scheduler.start()
        return scheduler

    @staticmethod
    def cycle_timestamp() -> str:
        return datetime.utcnow().isoformat()
