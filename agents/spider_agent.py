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
from storage.database import Database

logger = logging.getLogger(__name__)


class SpiderAgent:
    """Runs full detection pipeline and persists ranked opportunities."""

    def __init__(self) -> None:
        self.db = Database()
        self.coingecko = CoinGeckoFetcher()
        self.dex = DexscreenerFetcher()
        self.narrative = NarrativeDetector()
        self.social = SocialScanner()
        self.accumulation = AccumulationDetector()
        self.scorer = ScoringEngine()
        self.risk = RiskFilter()

    def run_cycle(self) -> list[dict[str, Any]]:
        """Execute one full market-intelligence cycle and return ranked opportunities."""
        markets = self.coingecko.fetch_mid_cap_markets(200)
        if not markets:
            logger.warning("No market data returned from CoinGecko; skipping cycle")
            return []

        self.db.insert_market_data(markets)

        dex_batch = self.dex.fetch_trending_pairs()
        self.db.insert_dex_data(dex_batch)
        dex_map = {row.get("symbol", "").upper(): row for row in dex_batch if row.get("symbol")}

        opportunities: list[dict[str, Any]] = []
        for coin in markets:
            try:
                narrative, narrative_conf, reasoning = self.narrative.classify(coin)
                symbol = coin.get("symbol", "").upper()
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

                opportunities.append(
                    {
                        "coin_id": coin.get("id"),
                        "symbol": symbol,
                        "narrative": narrative,
                        "score": score,
                        "accumulation_score": accumulation_score,
                        "volume_24h": coin.get("total_volume", 0),
                        "liquidity": dex_data.get("liquidity", 0),
                        "price": coin.get("current_price", 0),
                        "reason": f"{reasoning}; {risk_reason}",
                    }
                )
            except Exception as exc:
                logger.exception("Failed processing coin=%s due to %s", coin.get("symbol"), exc)

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        self.db.insert_opportunities(opportunities)
        return opportunities

    def start_scheduler(self) -> BackgroundScheduler:
        scheduler = BackgroundScheduler()
        scheduler.add_job(self.run_cycle, "interval", minutes=settings.scheduler_minutes)
        scheduler.start()
        return scheduler

    @staticmethod
    def cycle_timestamp() -> str:
        return datetime.utcnow().isoformat()
