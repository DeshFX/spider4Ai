"""Spider4AI autonomous loop orchestration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from data.cambrian_client import CambrianClient
from data.narrative_detector import NarrativeDetector
from engine.accumulation_detector import AccumulationDetector
from engine.risk_filter import RiskFilter
from engine.rug_checker import RugChecker
from engine.scoring_engine import ScoringEngine
from execution.sepolia_executor import SepoliaExecutor
from execution.trade_manager import TradeManager
from genlayer.service import GenLayerService
from notifications.telegram import send_telegram_message
from storage.database import Database
from structured_logging import log_json

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST = ["BTC", "ETH", "SOL", "PEPE", "WIF", "BONK"]


class SpiderAgent:
    """Runs full detection pipeline and persists ranked opportunities."""

    def __init__(self, decision_service: GenLayerService | None = None, db: Database | None = None) -> None:
        self.db = db or Database()
        self.cambrian = CambrianClient()
        self.narrative = NarrativeDetector()
        self.accumulation = AccumulationDetector()
        self.scorer = ScoringEngine()
        self.risk = RiskFilter()
        self.rug_checker = RugChecker()
        self.decision_service = decision_service or GenLayerService()
        self.trade_manager = TradeManager(self.db)
        self.watchlist = [s.strip().upper() for s in settings.watchlist.split(",") if s.strip()] or DEFAULT_WATCHLIST
        self._budget_warning_sent = False
        self.event_sink = None

    def _emit(self, event: str, message: str) -> None:
        """Push a progress event to an optional UI sink (web dashboard)."""
        sink = self.event_sink
        if not sink:
            return
        try:
            sink(event, message)
        except Exception:
            pass

    def _budget_savings_enabled(self) -> bool:
        """True when the monthly Cambrian budget (x safety margin) is nearly exhausted.

        When enabled, per-token enrichment is skipped and a one-time Telegram
        warning is sent so the operator knows the bot is in budget-saving mode.
        """
        usage = self.db.get_api_usage()
        threshold = settings.cambrian_monthly_budget * settings.cambrian_safety_margin
        if usage["month_calls"] < threshold:
            return False
        if not self._budget_warning_sent:
            self._budget_warning_sent = True
            send_telegram_message(
                f"WARNING: Cambrian API usage {usage['month_calls']}/{settings.cambrian_monthly_budget} "
                f"reached {settings.cambrian_safety_margin:.0%} of monthly budget. "
                "Switching to budget-saving mode: per-token enrichment skipped."
            )
            log_json(
                logger,
                logging.WARNING,
                "cambrian_budget_saving",
                month_calls=usage["month_calls"],
                budget=settings.cambrian_monthly_budget,
                margin=settings.cambrian_safety_margin,
            )
        return True

    def _collect_tracked_wallets(self, opportunity: dict[str, Any]) -> tuple[str | None, list[str]]:
        """Track the dev/whale wallets to watch for sell-outs while holding.

        The top holder (>= RUGCHECK_TOP_HOLDER_PCT of supply) is used as the
        dev proxy and all qualifying holders are tracked for dump detection.
        """
        token_address = opportunity.get("coin_id")
        holders: list[str] = []
        if token_address:
            try:
                for holder in self.cambrian.get_holders(token_address, limit=10):
                    if float(holder.get("pct") or 0) >= settings.rugcheck_top_holder_pct:
                        address = holder.get("address")
                        if address:
                            holders.append(str(address))
            except Exception:
                logger.debug("Could not fetch tracked wallets for %s", token_address, exc_info=True)
        return (holders[0] if holders else None), holders

    def build_decision_payload(
        self,
        coin: dict[str, Any],
        opportunity: dict[str, Any],
        risk_flags: list[str],
        market_stability: float,
        recent_trend: str,
        onchain_context: str = "",
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
            "source": "cambrian",
            "recent_trend": recent_trend,
            "price": opportunity.get("price"),
            "score": opportunity.get("score"),
            "market_cap": coin.get("market_cap"),
            "volume_24h": opportunity.get("volume_24h"),
            "liquidity": opportunity.get("liquidity"),
            "narrative": opportunity.get("narrative"),
            "accumulation_score": opportunity.get("accumulation_score"),
            "market_stability": market_stability,
            "tier": opportunity.get("tier", "mid"),
            "onchain_context": onchain_context,
            "reason": opportunity.get("reason"),
        }

    @staticmethod
    def _build_onchain_context(rug: dict[str, Any]) -> str:
        if not rug.get("flags"):
            return "No rugpull flags detected."
        return (
            "Rug checker flags: "
            + "; ".join(f"{flag} ({rug.get('reason', '')})" for flag in rug.get("flags", []))
        )

    def _apply_genlayer_decision(self, opportunity: dict[str, Any], payload: dict[str, Any]) -> None:
        result = self.decision_service.send_decision(payload)
        opportunity["genlayer_status"] = result.get("status", "unknown")
        opportunity["genlayer_tx_hash"] = result.get("transaction_hash")
        opportunity["decision_source"] = result.get("decision_source", "unknown")
        decision = result.get("decision") or {}
        if decision:
            opportunity["genlayer_decision"] = decision.get("final_decision", "WAIT")
            opportunity["genlayer_confidence"] = decision.get("confidence", 0.0)
            opportunity["genlayer_reasoning"] = decision.get(
                "reasoning", result.get("reason", "No decision returned")
            )
            opportunity["genlayer_votes"] = decision.get("votes", [])
            opportunity["genlayer_disagreement"] = decision.get("disagreement", 0.0)
            self._emit(
                "genlayer",
                f"{opportunity.get('symbol')} -> {opportunity['genlayer_decision']} @ "
                f"{float(opportunity.get('genlayer_confidence') or 0):.2f} ({opportunity.get('decision_source')})",
            )
        else:
            # GenLayer-only policy: no fallback engine exists anymore.
            opportunity["genlayer_decision"] = "NONE"
            opportunity["genlayer_confidence"] = 0.0
            opportunity["genlayer_reasoning"] = result.get(
                "reason", "GenLayer unavailable - no fallback configured"
            )
            opportunity["genlayer_votes"] = []
            opportunity["genlayer_disagreement"] = 0.0
            self._emit(
                "genlayer",
                f"{opportunity.get('symbol')} -> TANPA KEPUTUSAN "
                f"({opportunity.get('decision_source')}): {result.get('reason', '')[:120]}",
            )
        log_json(
            logger,
            logging.INFO,
            "decision_applied",
            symbol=opportunity.get("symbol"),
            decision=decision,
            source=opportunity.get("decision_source"),
        )

    def _execute_decision(self, opportunity: dict[str, Any]) -> None:
        decision = opportunity.get("genlayer_decision")
        if decision in ("", "NONE"):
            opportunity["execution_status"] = "no_decision"
            self._emit(
                "exec",
                f"{opportunity.get('symbol')} dilewati: tidak ada keputusan GenLayer",
            )
            return
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
            self._emit("risk", f"{opportunity.get('symbol')} BLACKLISTED: {opportunity.get('genlayer_reasoning')}")
            log_json(
                logger,
                logging.WARNING,
                "token_blacklisted",
                symbol=opportunity.get("symbol"),
                reason=opportunity.get("genlayer_reasoning"),
                source=opportunity.get("decision_source"),
            )
            return
        if decision != "BUY":
            opportunity["execution_status"] = "deferred" if decision == "WAIT" else "skipped"
            self._emit("exec", f"{opportunity.get('symbol')} {opportunity['execution_status']} (decision {decision})")
            return

        approved, reason = self.trade_manager.should_open_position(opportunity)
        if not approved:
            opportunity["execution_status"] = f"blocked:{reason}"
            self.db.record_trade_event(opportunity.get("symbol", ""), "BLOCKED", {"reason": reason})
            log_json(logger, logging.INFO, "execution_blocked", symbol=opportunity.get("symbol"), reason=reason)
            self._emit("exec", f"{opportunity.get('symbol')} diblokir trade manager: {reason}")
            return

        plan = self.trade_manager.compute_position_size(opportunity)
        tx_hash = None
        if settings.dry_run:
            opportunity["execution_status"] = "dry_run"
        elif settings.sepolia_rpc_url and settings.wallet_private_key:
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
                return
        else:
            opportunity["execution_status"] = "paper_position_opened"
        opportunity["execution_tx_hash"] = tx_hash
        self._emit(
            "exec",
            f"{opportunity.get('symbol')} posisi dibuka [{opportunity.get('execution_status')}] size ${plan.size_usd:.2f}",
        )
        opportunity["position_size_pct"] = plan.size_pct
        opportunity["position_size_usd"] = plan.size_usd
        opportunity["take_profit_price"] = plan.take_profit_price
        opportunity["stop_loss_price"] = plan.stop_loss_price
        opportunity["trailing_stop_pct"] = plan.trailing_stop_pct
        opportunity["tracked_dev_wallet"], opportunity["tracked_top_holder_wallets"] = self._collect_tracked_wallets(opportunity)
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

    def run_cycle(self, force_alpha: bool = False) -> list[dict[str, Any]]:
        """Execute one full market-intelligence cycle and return ranked opportunities.

        ``force_alpha`` runs this cycle through the alpha/meme hunter source
        (social momentum + alpha tweets, FDV band filter) regardless of the
        ``alpha_hunter_enabled`` setting.
        """
        markets = self._fetch_markets(force_alpha=force_alpha)
        if not markets:
            logger.warning("No market data returned from Cambrian; skipping cycle")
            self._emit("scan", "Tidak ada data market dari Cambrian — siklus dilewati")
            return []
        self._emit("scan", f"Mulai siklus: {len(markets)} token trending diambil")

        self.trade_manager.monitor_positions(markets)
        self.db.insert_market_data(markets)

        budget_mode = self._budget_savings_enabled()
        if budget_mode:
            self._emit("scan", "Budget-saving mode aktif — per-token enrichment dilewati")
        dex_batch: list[dict[str, Any]] = []
        if not budget_mode:
            for coin in markets:
                liquidity = self.cambrian.get_liquidity_volume(coin.get("id") or coin.get("address", ""))
                if liquidity:
                    dex_batch.append(liquidity)
        self.db.insert_dex_data(dex_batch)
        dex_map = {row.get("symbol", "").upper(): row for row in dex_batch if row.get("symbol")}

        opportunities: list[dict[str, Any]] = []
        for coin in markets:
            try:
                symbol = coin.get("symbol", "").upper()
                token_address = coin.get("id") or coin.get("address", "")
                if self.db.is_blacklisted(coin.get("id"), symbol):
                    log_json(logger, logging.INFO, "evaluation_skipped", symbol=symbol, reason="blacklisted")
                    continue

                rug = self.rug_checker.check(
                    token_address,
                    pool_address=dex_map.get(symbol, {}).get("pair_address", ""),
                )
                if rug.get("blocked"):
                    log_json(
                        logger,
                        logging.WARNING,
                        "rug_blocked",
                        symbol=symbol,
                        reason=rug.get("reason"),
                        flags=rug.get("flags"),
                    )
                    self._emit("risk", f"{symbol} diblokir rug checker: {rug.get('reason')}")
                    self.db.record_trade_event(symbol, "BLOCKED", {"reason": rug.get("reason")})
                    continue

                narrative, narrative_conf, reasoning = self.narrative.classify(coin)
                dex_data = dex_map.get(symbol) or self.cambrian.get_liquidity_volume(token_address)
                accumulation_score = self.accumulation.score(coin, dex_data)

                volume_momentum = min((coin.get("total_volume", 0) / max(coin.get("market_cap", 1), 1)) * 5, 1)
                liquidity_raw = dex_data.get("liquidity", 0) or (coin.get("total_volume", 0) / max(settings.cambrian_liquidity_volume_divisor, 0.001))
                liquidity_health = min((liquidity_raw / 2_000_000), 1)
                market_stability = max(0.0, 1 - abs((coin.get("price_change_percentage_24h") or 0) / 25))
                social = self.cambrian.get_social_sentiment(symbol) if not budget_mode else {}
                social_score = float(social.get("score", 0.5) or 0.5)
                social_boost = social_score * 0.05

                tier = settings.classify_tier(coin.get("market_cap", 0) or 0)
                score = self.scorer.score(
                    narrative_confidence=min(1.0, narrative_conf + social_boost),
                    volume_momentum=min(1.0, volume_momentum),
                    liquidity_health=min(1.0, liquidity_health),
                    accumulation_score=accumulation_score,
                    market_stability=min(1.0, market_stability),
                    tier=tier,
                )

                safe, risk_reason = self.risk.is_safe(coin, dex_data, tier)
                if not safe:
                    continue
                self._emit(
                    "score",
                    f"{symbol} score {score:.1f}/100 · tier {tier} · narrative {narrative}",
                )

                risk_flags = self._build_risk_flags(coin, dex_data, market_stability)
                wallet_activity = (
                    self.cambrian.get_wallet_activity(coin.get("address") or symbol) if not budget_mode else {}
                )
                if wallet_activity.get("suspicious"):
                    risk_flags.append("suspicious_wallet_activity")
                recent_trend = self._build_recent_trend(coin)
                onchain_context = self._build_onchain_context(rug)
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
                    "price_change_percentage_24h": coin.get("price_change_percentage_24h", 0),
                    "tier": tier,
                    "fdv": float(coin.get("market_cap") or 0),
                    "market_cap": float(coin.get("market_cap") or 0),
                }
                payload = self.build_decision_payload(
                    coin,
                    opportunity,
                    risk_flags,
                    market_stability,
                    recent_trend,
                    onchain_context=onchain_context,
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
                self._apply_genlayer_decision(opportunity, payload)
                self._execute_decision(opportunity)
                opportunities.append(opportunity)
            except Exception as exc:
                logger.exception("Failed processing coin=%s due to %s", coin.get("symbol"), exc)

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        self.db.insert_opportunities(opportunities)
        self._emit("scan", f"Siklus selesai: {len(opportunities)} opportunity tersimpan")
        return opportunities

    def _fetch_markets(self, force_alpha: bool = False) -> list[dict[str, Any]]:
        """Fetch active Solana tokens from Cambrian.

        Uses ``/solana/trending-tokens`` (returns token addresses) instead of
        resolving watchlist symbols, since Cambrian's price/security endpoints
        require token mint addresses. When ``alpha_hunter_enabled`` is set, the
        alpha/meme hunter mode is used instead: social momentum + alpha tweets
        are resolved to addresses and filtered to the alpha FDV band.

        In budget-saving mode, per-token detail enrichment is skipped to keep
        API calls to the single trending-tokens request.
        """
        budget_mode = self._budget_savings_enabled()
        if (settings.alpha_hunter_enabled or force_alpha) and not budget_mode:
            return self._fetch_alpha_markets()
        markets: list[dict[str, Any]] = []
        for coin in self.cambrian.get_trending_tokens(order_by="volume_usd_24h", limit=10):
            if budget_mode:
                markets.append(coin)
                continue
            details = self.cambrian.get_token_details(coin["id"])
            if details:
                coin["name"] = details.get("name") or coin.get("name", "")
                coin["market_cap"] = details.get("fdv", 0.0)
                coin["total_volume"] = details.get("volume_24h") or coin.get("total_volume", 0)
            markets.append(coin)
        return markets

    def _fetch_alpha_markets(self) -> list[dict[str, Any]]:
        """Alpha/meme hunter: X momentum ∩ active trending -> FDV band -> anti-rug gates.

        Symbols hot on X are cross-matched against the trending top-100 (which
        carries mint addresses) so most candidates need zero registry calls;
        the cached registry resolver is only a fallback.
        """
        candidates: dict[str, dict[str, Any]] = {}

        for item in self.cambrian.get_social_momentum(limit=settings.alpha_hunter_limit):
            candidates[item["symbol"].upper()] = {
                "symbol": item["symbol"].upper(),
                "momentum_score": item.get("momentum_score", 0),
            }

        for item in self.cambrian.get_alpha_tweets(limit=settings.alpha_hunter_limit):
            symbol = item["symbol"].upper()
            if symbol in candidates:
                candidates[symbol]["alpha_score"] = item.get("alpha", 0)
            else:
                candidates[symbol] = {
                    "symbol": symbol,
                    "momentum_score": 0,
                    "alpha_score": item.get("alpha", 0),
                }

        if not candidates:
            self._emit(
                "meme",
                "Sumber sosial kosong (momentum/tweets tidak mengembalikan data) "
                "- kemungkinan rate limit API atau pasar sedang sepi",
            )
            return []

        trending_map: dict[str, str] = {}
        try:
            for row in self.cambrian.get_trending_tokens(
                order_by="price_change_percentage", limit=100
            ):
                if row.get("id"):
                    trending_map[str(row.get("symbol", "")).upper()] = str(row["id"])
        except Exception:
            pass

        markets: list[dict[str, Any]] = []
        for symbol, meta in candidates.items():
            address = trending_map.get(symbol)
            source = "trending-cross-match" if address else "registry-cache"
            if not address:
                address = self.cambrian.resolve_symbol_to_address(symbol)
            if not address:
                self._emit("meme", f"{symbol} skip: address tidak ditemukan")
                continue
            details = self.cambrian.get_token_details(address)
            if not details:
                continue
            fdv = details.get("fdv", 0)
            if fdv < settings.alpha_min_fdv or fdv > settings.tier_alpha_max_fdv:
                continue
            self._emit(
                "meme",
                f"{symbol} lolos FDV band ({float(fdv):,.0f}) via {source}",
            )

            security = self.cambrian.get_token_security(address)
            volume = float(details.get("volume_24h") or 0)
            passed, gate_reason = self._alpha_gates(fdv, volume, security)
            if not passed:
                self._emit("risk", f"{symbol} gagal gerbang meme: {gate_reason}")
                continue
            self._emit("meme", f"{symbol} lolos semua gerbang anti-rug")

            markets.append(
                {
                    "id": address,
                    "address": address,
                    "symbol": symbol,
                    "name": details.get("name") or symbol,
                    "current_price": details.get("current_price", 0),
                    "price_usd": details.get("current_price", 0),
                    "market_cap": fdv,
                    "total_volume": details.get("volume_24h", 0),
                    "volume_24h": details.get("volume_24h", 0),
                    "price_change_percentage_24h": 0,
                    "stability": 0.5,
                    "trend": "neutral",
                    "momentum_score": meta.get("momentum_score", 0),
                    "alpha_score": meta.get("alpha_score", 0),
                }
            )
            if len(markets) >= settings.alpha_hunter_limit:
                break
        return markets

    @staticmethod
    def _alpha_gates(fdv: float, volume_24h: float, security: dict[str, Any]) -> tuple[bool, str]:
        """Deterministic anti-wash/anti-rug gates before GenLayer evaluation."""
        top10 = float(security.get("top10_pct") or 0)
        holders = int(security.get("holder_count") or 0)
        uniqueness = float(security.get("tx_uniqueness_ratio") or 1)
        ratio = volume_24h / max(float(fdv), 1)

        failed: list[str] = []
        if top10 > settings.alpha_gate_top10_max_pct:
            failed.append(f"top10 {top10:.1f}% > {settings.alpha_gate_top10_max_pct}%")
        if holders < settings.alpha_gate_min_holders:
            failed.append(f"holders {holders} < {settings.alpha_gate_min_holders}")
        if ratio > settings.alpha_gate_max_volume_fdv_ratio:
            failed.append(
                f"vol/fdv {ratio:.0f}x > {settings.alpha_gate_max_volume_fdv_ratio:.0f}x (curiga wash trade)"
            )
        if uniqueness < settings.alpha_gate_min_tx_uniqueness:
            failed.append(
                f"tx uniqueness {uniqueness:.2f} < {settings.alpha_gate_min_tx_uniqueness} (dompet sama berulang)"
            )
        return (not failed), "; ".join(failed)

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
