"""Core unit tests for Spider4AI engines, GenLayer integration, CLI, storage, and features."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch


@contextlib.contextmanager
def _tmp_db_file():
    """Context manager yielding a writable temp DB path (Windows-safe)."""
    tmp_dir = tempfile.mkdtemp()
    path = os.path.join(tmp_dir, "test.db")
    try:
        yield path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Provide tiny dependency stubs so tests can run in minimal environments
# where optional dependencies are not installed yet.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    class _ReqExc(Exception):
        pass

    def _post(*args, **kwargs):
        raise _ReqExc("requests stub")

    def _get(*args, **kwargs):
        raise _ReqExc("requests stub")

    requests_stub.RequestException = _ReqExc
    requests_stub.post = _post
    requests_stub.get = _get
    sys.modules["requests"] = requests_stub

if "apscheduler" not in sys.modules:
    apscheduler = types.ModuleType("apscheduler")
    schedulers = types.ModuleType("apscheduler.schedulers")
    background = types.ModuleType("apscheduler.schedulers.background")

    class BackgroundScheduler:  # pragma: no cover - dependency shim
        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

    background.BackgroundScheduler = BackgroundScheduler
    schedulers.background = background
    apscheduler.schedulers = schedulers
    sys.modules["apscheduler"] = apscheduler
    sys.modules["apscheduler.schedulers"] = schedulers
    sys.modules["apscheduler.schedulers.background"] = background

if "web3" not in sys.modules:
    web3_module = types.ModuleType("web3")

    class Web3:  # pragma: no cover - dependency shim
        HTTPProvider = object

        def __init__(self, provider=None):
            self.provider = provider

        @staticmethod
        def to_checksum_address(address):
            return address

        @staticmethod
        def to_wei(amount, unit):
            return amount

    web3_module.Web3 = Web3
    sys.modules["web3"] = web3_module

from agents.spider_agent import SpiderAgent
from config import ConfigError, Settings, settings
from data.cambrian_client import CambrianClient
from data.narrative_detector import NarrativeDetector
from engine.accumulation_detector import AccumulationDetector
from engine.confidence_scorer import ConfidenceScorer
from engine.risk_filter import RiskFilter
from engine.rug_checker import RugChecker
from engine.scoring_engine import ScoringEngine
from genlayer.service import (
    GenLayerService,
    LocalFallbackDecisionEngine,
    build_decision_prompt,
    normalize_decision_payload,
    normalize_trade_payload,
)
from reports.report_generator import ReportGenerator
from execution.dex_swap import swap_eth_to_token
from execution.sepolia_executor import SepoliaExecutor
from storage.database import Database
from execution.dex_swap import swap_eth_to_token
from execution.trade_manager import TradeManager, TradePlan, calculate_position_size


def setUpModule() -> None:
    """Keep tests off external side effects (real Cambrian API, Telegram).

    NarrativeDetector / RiskFilter call the module-level singleton
    (``get_cambrian_client``); swap it for a keyless client so tests never
    make real network calls or pollute the production api_call_log.

    Exit notifications in ``TradeManager`` / ``SpiderAgent`` call the real
    ``send_telegram_message`` (configured bot token); patch those imported
    references so unit tests never spam the operator's Telegram chat.
    """
    global _PATCHERS
    _PATCHERS = [
        patch(
            "data.cambrian_client.get_cambrian_client",
            return_value=CambrianClient(api_key=""),
        ),
        patch("execution.trade_manager.send_telegram_message", return_value=True),
        patch("agents.spider_agent.send_telegram_message", return_value=True),
    ]
    for patcher in _PATCHERS:
        patcher.start()


def tearDownModule() -> None:
    if _PATCHERS:
        for patcher in reversed(_PATCHERS):
            patcher.stop()


_PATCHERS = []


class EngineTests(unittest.TestCase):
    def test_scoring_engine_bounds(self) -> None:
        engine = ScoringEngine()
        self.assertEqual(engine.score(1, 1, 1, 1, 1), 100)
        self.assertEqual(engine.score(0, 0, 0, 0, 0), 0)

    def test_accumulation_normalized(self) -> None:
        detector = AccumulationDetector()
        score = detector.score(
            {"total_volume": 8_000_000, "market_cap": 1_000_000_000, "price_change_percentage_24h": 3},
            {"liquidity": 2_000_000},
        )
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 1)

    def test_risk_filter_rules(self) -> None:
        risk = RiskFilter()
        safe, reason = risk.is_safe(
            {"price_change_percentage_24h": 50, "total_volume": 1_000_000},
            {"liquidity": 1_000_000},
        )
        self.assertFalse(safe)
        self.assertIn("pump", reason.lower())


class NarrativeTests(unittest.TestCase):
    def test_keyword_fallback(self) -> None:
        detector = NarrativeDetector()
        with patch.object(detector, "_classify_with_ollama", return_value=None):
            narrative, confidence, reason = detector.classify({"name": "AI Agents Token", "symbol": "AIA"})
        self.assertEqual(narrative, "AI")
        self.assertEqual(confidence, 0.5)
        self.assertIn("fallback", reason.lower())


class ConfigTests(unittest.TestCase):
    def test_validate_startup_raises(self) -> None:
        settings = Settings(sepolia_rpc_url="", wallet_private_key="", genlayer_enabled=False)
        with self.assertRaises(ConfigError):
            settings.validate_startup()


class GenLayerTests(unittest.TestCase):
    def test_send_decision_disabled_returns_status(self) -> None:
        result = GenLayerService(enabled=False).send_decision({"symbol": "ABC", "risk_flags": []})
        self.assertEqual(result["status"], "disabled")

    def test_normalize_trade_payload_enforces_schema(self) -> None:
        normalized = normalize_trade_payload({"symbol": "abc", "risk_flags": ["thin_liquidity"], "signal_strength": 0.8})
        self.assertEqual(normalized["symbol"], "ABC")
        self.assertEqual(normalized["risk_flags"], ["thin_liquidity"])

    def test_normalize_decision_payload_accepts_contract_shape(self) -> None:
        normalized = normalize_decision_payload({"final_decision": "BUY", "confidence": 0.81, "votes": [], "reasoning": "Committee majority", "disagreement": 0.1})
        self.assertEqual(normalized["final_decision"], "BUY")

    def test_fallback_used_after_retries(self) -> None:
        fallback_engine = Mock(spec=LocalFallbackDecisionEngine)
        fallback_engine.decide.return_value = {"status": "fallback", "decision_source": "heuristic", "decision": {"final_decision": "WAIT", "confidence": 0.5, "votes": [], "reasoning": "fallback", "disagreement": 0.2}}
        service = GenLayerService(enabled=True, retries=2, timeout_seconds=0.01, fallback_engine=fallback_engine)
        with patch("genlayer.service.get_contract_at", side_effect=RuntimeError("boom")):
            result = service.send_decision({"symbol": "ABC", "risk_flags": []})
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(len(result["errors"]), 2)


class AgentTests(unittest.TestCase):
    def test_build_decision_payload_shapes_market_context(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        payload = agent.build_decision_payload(
            {"id": "abc", "name": "Alpha", "market_cap": 1000},
            {"symbol": "ABC", "price": 1.2, "volume_24h": 500, "liquidity": 250, "narrative": "AI", "score": 72, "accumulation_score": 0.7, "reason": "ranked highly"},
            ["thin_liquidity"],
            0.66,
            "Mixed short-term price action.",
        )
        self.assertEqual(payload["symbol"], "ABC")
        self.assertEqual(payload["market_cap"], 1000)

    def test_execute_decision_blacklists_scam(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        with patch.object(agent.db, "blacklist_token") as blacklist_token, patch.object(agent.db, "record_trade_event") as record_event:
            opportunity = {"coin_id": "abc", "symbol": "ABC", "genlayer_decision": "SCAM", "genlayer_reasoning": "rug risk", "decision_source": "genlayer"}
            agent._execute_decision(opportunity)
        self.assertEqual(opportunity["execution_status"], "blacklisted")
        blacklist_token.assert_called_once()
        record_event.assert_called_once()

    def test_execute_decision_respects_dry_run(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        with patch.object(agent.trade_manager, "should_open_position", return_value=(True, "approved")), patch.object(agent.trade_manager, "compute_position_size") as cps, patch.object(agent.trade_manager, "record_open_position"), patch.object(agent.cambrian, "get_holders", return_value=[]):
            cps.return_value = types.SimpleNamespace(size_pct=0.02, size_usd=200, take_profit_price=1.2, stop_loss_price=0.9, trailing_stop_pct=0.05)
            opportunity = {"coin_id": "abc", "symbol": "ABC", "genlayer_decision": "BUY", "genlayer_confidence": 0.9, "genlayer_disagreement": 0.1, "price": 1.0, "decision_source": "genlayer", "risk_flags": []}
            agent._execute_decision(opportunity)
        self.assertEqual(opportunity["execution_status"], "dry_run")


class TradeManagerTests(unittest.TestCase):
    def test_position_sizing_respects_bounds(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            plan = manager.compute_position_size({"genlayer_confidence": 0.82, "genlayer_disagreement": 0.1, "market_stability": 0.7, "price": 1.0})
            self.assertGreaterEqual(plan.size_pct, 0.01)
            self.assertLessEqual(plan.size_usd, 500)

    def test_calculate_position_size_scales_with_confidence(self) -> None:
        self.assertEqual(calculate_position_size(0.5), 0.00045)
        self.assertEqual(calculate_position_size(0.8), 0.00054)
        self.assertEqual(calculate_position_size(0.0), 0.0003)

    def test_exit_logic_hits_take_profit(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            exit_reason, pnl_pct = manager.evaluate_exit({"entry_price": 1.0, "take_profit_price": 1.2, "stop_loss_price": 0.93, "trailing_stop_pct": 0.05, "peak_price": 1.0}, 1.21)
            self.assertEqual(exit_reason, "TAKE_PROFIT")
            self.assertGreater(pnl_pct, 0)


class GenLayerTests(unittest.TestCase):
    def test_send_decision_disabled_returns_status(self) -> None:
        result = GenLayerService(enabled=False).send_decision({"symbol": "ABC", "risk_flags": []})
        self.assertEqual(result["status"], "disabled")

    def test_normalize_trade_payload_enforces_schema(self) -> None:
        normalized = normalize_trade_payload(
            {
                "symbol": "abc",
                "risk_flags": ["thin_liquidity"],
                "signal_strength": 0.8,
            }
        )
        self.assertEqual(normalized["symbol"], "ABC")
        self.assertEqual(normalized["risk_flags"], ["thin_liquidity"])
        self.assertEqual(normalized["signal_strength"], 0.8)

    def test_normalize_decision_payload_accepts_contract_shape(self) -> None:
        normalized = normalize_decision_payload(
            {
                "final_decision": "BUY",
                "confidence": 0.81,
                "votes": [{"decision": "BUY", "confidence": 0.9}],
                "reasoning": "Committee majority",
            }
        )
        self.assertEqual(normalized["final_decision"], "BUY")
        self.assertAlmostEqual(normalized["confidence"], 0.81)

    def test_fallback_used_after_retries(self) -> None:
        fallback_engine = Mock(spec=LocalFallbackDecisionEngine)
        fallback_engine.decide.return_value = {
            "status": "fallback",
            "decision_source": "heuristic",
            "decision": {
                "final_decision": "WAIT",
                "confidence": 0.5,
                "votes": [],
                "reasoning": "fallback",
            },
        }
        service = GenLayerService(enabled=True, retries=2, timeout_seconds=0.01, fallback_engine=fallback_engine)
        with patch("genlayer.service.get_contract_at", side_effect=RuntimeError("boom")):
            result = service.send_decision({"symbol": "ABC", "risk_flags": []})
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(len(result["errors"]), 2)
        fallback_engine.decide.assert_called_once()

    def test_build_decision_prompt_includes_risk_and_summary(self) -> None:
        prompt = build_decision_prompt(
            {
                "symbol": "ABC",
                "summary": "Token summary",
                "narrative": "AI",
                "risk_flags": ["thin_liquidity"],
                "source": "coingecko+dexscreener",
            }
        )
        self.assertIn("Token summary", prompt)
        self.assertIn("thin_liquidity", prompt)
        self.assertIn("AI", prompt)

    def test_build_decision_payload_shapes_market_context(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        payload = agent.build_decision_payload(
            {"id": "abc", "name": "Alpha", "market_cap": 1000},
            {
                "symbol": "ABC",
                "price": 1.2,
                "volume_24h": 500,
                "liquidity": 250,
                "narrative": "AI",
                "score": 72,
                "accumulation_score": 0.7,
                "reason": "ranked highly",
            },
            ["thin_liquidity"],
            0.66,
            "Mixed short-term price action.",
        )
        self.assertEqual(payload["symbol"], "ABC")
        self.assertEqual(payload["market_cap"], 1000)
        self.assertEqual(payload["score"], 72)
        self.assertEqual(payload["risk_flags"], ["thin_liquidity"])
        self.assertEqual(payload["market_stability"], 0.66)

    def test_execute_decision_blacklists_scam(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        with patch.object(agent.db, "blacklist_token") as blacklist_token:
            opportunity = {
                "coin_id": "abc",
                "symbol": "ABC",
                "genlayer_decision": "SCAM",
                "genlayer_reasoning": "rug risk",
                "decision_source": "genlayer",
            }
            agent._execute_decision(opportunity)
        self.assertEqual(opportunity["execution_status"], "blacklisted")
        blacklist_token.assert_called_once()


class DatabaseTests(unittest.TestCase):
    def test_insert_and_query(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            db.insert_opportunities(
                [
                    {
                        "coin_id": "abc",
                        "symbol": "ABC",
                        "narrative": "AI",
                        "score": 73.5,
                        "accumulation_score": 0.62,
                        "volume_24h": 1_000_000,
                        "liquidity": 150_000,
                        "price": 1.23,
                        "reason": "test row",
                        "summary": "summary",
                        "risk_flags": ["thin_liquidity"],
                        "signal_strength": 0.73,
                        "source": "coingecko+dexscreener",
                        "genlayer_status": "submitted",
                        "genlayer_decision": "BUY",
                        "genlayer_confidence": 0.88,
                        "genlayer_reasoning": "Strong setup",
                        "genlayer_votes": [{"decision": "BUY", "confidence": 0.9}],
                        "decision_source": "genlayer",
                        "execution_status": "submitted",
                    }
                ]
            )
            rows = db.get_latest_opportunities(limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "ABC")
            self.assertEqual(rows[0]["genlayer_decision"], "BUY")
            self.assertEqual(rows[0]["risk_flags"], ["thin_liquidity"])
            self.assertEqual(rows[0]["genlayer_votes"][0]["decision"], "BUY")

    def test_blacklist_token_upsert(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            db.blacklist_token("abc", "ABC", "rug risk", "genlayer")
            status = db.get_scan_status()
            self.assertEqual(status["blacklisted_tokens"], 1)

    def test_swap_skips_low_confidence(self) -> None:
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertIsNone(swap_eth_to_token("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 0.5))
        self.assertIn("[SKIP] Confidence too low", stdout.getvalue())
        self.assertIn("calculated_size=0.00045 ETH", stdout.getvalue())

if __name__ == "__main__":
    unittest.main()


class AlphaGatesTests(unittest.TestCase):
    def test_clean_token_passes_all_gates(self) -> None:
        from agents.spider_agent import SpiderAgent

        ok, reason = SpiderAgent._alpha_gates(
            fdv=50_000,
            volume_24h=500_000,
            security={"top10_pct": 22.0, "holder_count": 350, "tx_uniqueness_ratio": 0.55},
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_wash_trade_volume_ratio_blocked(self) -> None:
        from agents.spider_agent import SpiderAgent

        ok, reason = SpiderAgent._alpha_gates(
            fdv=20_000,
            volume_24h=2_000_000,
            security={"top10_pct": 10.0, "holder_count": 500, "tx_uniqueness_ratio": 0.6},
        )
        self.assertFalse(ok)
        self.assertIn("wash trade", reason)

    def test_low_tx_uniqueness_blocked(self) -> None:
        from agents.spider_agent import SpiderAgent

        ok, reason = SpiderAgent._alpha_gates(
            fdv=50_000,
            volume_24h=400_000,
            security={"top10_pct": 15.0, "holder_count": 300, "tx_uniqueness_ratio": 0.05},
        )
        self.assertFalse(ok)
        self.assertIn("uniqueness", reason)

    def test_holder_concentration_and_count_blocked(self) -> None:
        from agents.spider_agent import SpiderAgent

        ok, reason = SpiderAgent._alpha_gates(
            fdv=80_000,
            volume_24h=100_000,
            security={"top10_pct": 85.0, "holder_count": 12, "tx_uniqueness_ratio": 0.7},
        )
        self.assertFalse(ok)
        self.assertIn("top10", reason)
        self.assertIn("holders", reason)


class TradeManagerTests(unittest.TestCase):
    def test_position_sizing_respects_bounds(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            plan = manager.compute_position_size({
                "genlayer_confidence": 0.82,
                "genlayer_disagreement": 0.1,
                "market_stability": 0.7,
                "price": 1.0,
            })
            self.assertGreaterEqual(plan.size_pct, 0.01)
            self.assertLessEqual(plan.size_pct, 0.05)

    def test_exit_logic_hits_take_profit(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            exit_reason, pnl_pct = manager.evaluate_exit({
                "entry_price": 1.0,
                "take_profit_price": 1.2,
                "stop_loss_price": 0.93,
                "trailing_stop_pct": 0.05,
                "peak_price": 1.0,
            }, 1.21)
            self.assertEqual(exit_reason, "TAKE_PROFIT")
            self.assertGreater(pnl_pct, 0)

    def test_blacklist_blocks_position_open(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            db.blacklist_token("abc", "ABC", "scam", "genlayer")
            manager = TradeManager(db)
            approved, reason = manager.should_open_position({
                "coin_id": "abc",
                "symbol": "ABC",
                "genlayer_confidence": 0.9,
                "risk_flags": [],
                "genlayer_disagreement": 0.1,
            })
            self.assertFalse(approved)
            self.assertEqual(reason, "token_blacklisted")


class ConfidenceScorerTests(unittest.TestCase):
    """FITUR 1 — composite confidence score (engine + risk + decision)."""

    def test_scam_returns_zero(self) -> None:
        scorer = ConfidenceScorer()
        self.assertEqual(
            scorer.score({"genlayer_decision": "SCAM", "score": 95, "symbol": "AAA"}),
            0.0,
        )

    def test_skip_is_capped(self) -> None:
        scorer = ConfidenceScorer()
        value = scorer.score({"genlayer_decision": "SKIP", "score": 90, "symbol": "AAA"})
        self.assertLessEqual(value, 30.0)

    def test_bounds_always_0_100(self) -> None:
        scorer = ConfidenceScorer()
        for s in range(0, 101, 25):
            value = scorer.score(
                {
                    "score": s,
                    "genlayer_decision": "BUY",
                    "genlayer_confidence": 1.0,
                    "genlayer_disagreement": 0.0,
                    "symbol": "AAA",
                }
            )
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)

    def test_higher_inputs_yield_higher_confidence(self) -> None:
        scorer = ConfidenceScorer()
        low = scorer.score(
            {
                "score": 20,
                "genlayer_decision": "BUY",
                "genlayer_confidence": 0.4,
                "genlayer_disagreement": 0.1,
                "symbol": "AAA",
            }
        )
        high = scorer.score(
            {
                "score": 90,
                "genlayer_decision": "BUY",
                "genlayer_confidence": 0.95,
                "genlayer_disagreement": 0.05,
                "symbol": "BBB",
            }
        )
        self.assertLess(low, high)

    def test_wait_lowers_confidence(self) -> None:
        scorer = ConfidenceScorer()
        buy = scorer.score(
            {"score": 80, "genlayer_decision": "BUY", "genlayer_confidence": 0.9, "genlayer_disagreement": 0.1, "symbol": "AAA"}
        )
        wait = scorer.score(
            {"score": 80, "genlayer_decision": "WAIT", "genlayer_confidence": 0.9, "genlayer_disagreement": 0.1, "symbol": "AAA"}
        )
        self.assertLess(wait, buy)

    def test_trade_manager_uses_composite_before_sizing(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            opportunity = {
                "score": 80,
                "genlayer_decision": "BUY",
                "genlayer_confidence": 0.9,
                "genlayer_disagreement": 0.1,
                "market_stability": 0.7,
                "price": 1.0,
                "symbol": "AAA",
                "liquidity": 500_000,
            }
            plan = manager.compute_position_size(opportunity)
            self.assertIn("confidence_score", opportunity)
            self.assertGreater(opportunity["confidence_score"], 0)
            self.assertLessEqual(opportunity["confidence_score"], 100)
            self.assertGreaterEqual(plan.size_pct, 0.01)
            self.assertLessEqual(plan.size_usd, 500)


class CircuitBreakerTests(unittest.TestCase):
    """FITUR 2 — consecutive-loss circuit breaker with auto-pause."""

    def _close_position(self, db: Database, symbol: str, pnl_pct: float, reason: str = "STOP_LOSS") -> None:
        pid = db.insert_position(
            {
                "coin_id": symbol.lower(),
                "symbol": symbol,
                "decision_source": "test",
                "entry_price": 1.0,
                "size_usd": 100,
                "size_pct": 0.01,
                "take_profit_price": 1.2,
                "stop_loss_price": 0.9,
                "trailing_stop_pct": 0.05,
                "status": "OPEN",
                "execution_tx_hash": None,
            }
        )
        db.close_position(pid, 0.8 if pnl_pct < 0 else 1.2, reason, pnl_pct)

    def test_consecutive_losses_resets_after_win(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            self._close_position(db, "AAA", -10.0)
            self._close_position(db, "BBB", -15.0)
            self.assertEqual(db.count_consecutive_losses(), 2)
            self._close_position(db, "CCC", 12.0, "TAKE_PROFIT")
            self.assertEqual(db.count_consecutive_losses(), 0)

    def test_circuit_breaker_trigger_and_pause(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            for i in range(settings.circuit_breaker_max_losses):
                self._close_position(db, f"LOSS{i}", -20.0)
            self.assertTrue(manager.check_circuit_breaker())
            self.assertTrue(db.is_circuit_breaker_paused(settings.circuit_breaker_pause_minutes))
            approved, reason = manager.should_open_position(
                {"symbol": "NEW", "genlayer_decision": "BUY", "genlayer_confidence": 0.9}
            )
            self.assertFalse(approved)
            self.assertEqual(reason, "circuit_breaker_paused")

    def test_circuit_breaker_not_triggered_below_threshold(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            self._close_position(db, "AAA", -20.0)
            self.assertFalse(manager.check_circuit_breaker())


class DailyReportTests(unittest.TestCase):
    """FITUR 3 — automatic daily markdown report generation."""

    def test_daily_report_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(db_path=os.path.join(tmp, "t.db"))
            gen = ReportGenerator()
            gen.db = db
            gen.output_dir = Path(tmp)
            db.insert_opportunities(
                [
                    {
                        "coin_id": "abc",
                        "symbol": "ABC",
                        "narrative": "AI",
                        "score": 80.0,
                        "accumulation_score": 0.5,
                        "volume_24h": 1000,
                        "liquidity": 500_000,
                        "price": 1.0,
                        "reason": "ranked highly",
                        "summary": "summary",
                        "risk_flags": [],
                        "signal_strength": 0.8,
                        "source": "cambrian",
                        "market_context": "context",
                        "recent_trend": "trend",
                        "market_stability": 0.7,
                        "genlayer_status": "submitted",
                        "genlayer_decision": "BUY",
                        "genlayer_confidence": 0.9,
                        "genlayer_reasoning": "ok",
                        "genlayer_votes": [],
                        "genlayer_disagreement": 0.1,
                        "genlayer_tx_hash": None,
                        "decision_source": "genlayer",
                        "execution_status": "dry_run",
                        "execution_tx_hash": None,
                    }
                ]
            )
            path = gen.generate_daily_report()
            self.assertTrue(os.path.exists(path))
            self.assertIn("daily_report_", path)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("# Spider4AI Daily Report", content)
            self.assertIn("ABC", content)

    def test_daily_report_handles_empty_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(db_path=os.path.join(tmp, "t.db"))
            gen = ReportGenerator()
            gen.db = db
            gen.output_dir = Path(tmp)
            path = gen.generate_daily_report()
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("No opportunities available", content)


class DryRunConsistencyTests(unittest.TestCase):
    """FITUR 4 — dry-run mode never broadcasts; simulated trades are recorded."""

    def test_sepolia_executor_dry_run_no_broadcast(self) -> None:
        fake_settings = types.SimpleNamespace(dry_run=True, default_chain_id=11155111)
        with patch("execution.sepolia_executor.settings", fake_settings):
            executor = SepoliaExecutor.__new__(SepoliaExecutor)
            executor.rpc_url = "http://localhost:8545"
            executor.private_key = "0x" + "ab" * 32
            executor.w3 = Mock()
            with patch("sys.stdout", new_callable=io.StringIO):
                tx_hash = executor.simulate_test_transaction()
            self.assertTrue(tx_hash.startswith("0xSIMULATED_"))
            executor.w3.eth.send_raw_transaction.assert_not_called()

    def test_swap_eth_to_token_dry_run_records_simulated_trade(self) -> None:
        fake_settings = types.SimpleNamespace(dry_run=True)
        with patch("execution.dex_swap.settings", fake_settings), patch("execution.dex_swap.Database") as mock_db:
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertIsNone(swap_eth_to_token("0xabc", 0.9))
        mock_db.return_value.record_trade_event.assert_called_once()
        event_type = mock_db.return_value.record_trade_event.call_args[0][1]
        self.assertEqual(event_type, "SIMULATED_TRADE")


class HealthCheckTests(unittest.TestCase):
    """FITUR 5 — comprehensive health check command."""

    def test_collect_health_reports_all_sections(self) -> None:
        from main import collect_health

        fake_settings = Mock()
        fake_settings.health_snapshot.return_value = {"dry_run": "yes", "rpc_configured": "no"}
        fake_cambrian = Mock()
        fake_cambrian.api_key = "test-key"
        fake_cambrian.ping.return_value = True
        fake_genlayer = Mock()
        fake_genlayer.enabled = False
        fake_db = Mock()
        fake_db.get_scan_status.return_value = {"coins_scanned": 3, "open_positions": 1}

        checks = collect_health(
            cambrian_client=fake_cambrian,
            genlayer_service=fake_genlayer,
            database=fake_db,
            settings_obj=fake_settings,
        )
        self.assertEqual(checks["cambrian_api"], "connected")
        self.assertEqual(checks["genlayer"], "disabled")
        self.assertEqual(checks["database"], "ok (3 coins scanned, 1 open)")
        self.assertEqual(checks["env.dry_run"], "yes")

    def test_cambrian_unconfigured_without_key(self) -> None:
        fake_cambrian = Mock()
        fake_cambrian.api_key = ""
        from main import _check_cambrian

        self.assertIn("unconfigured", _check_cambrian(fake_cambrian))

    def test_database_error_reported(self) -> None:
        from main import _check_database

        fake_db = Mock()
        fake_db.get_scan_status.side_effect = RuntimeError("boom")
        self.assertIn("error", _check_database(fake_db))


class TierScoringTests(unittest.TestCase):
    def test_alpha_tier_weights_volume_momentum(self) -> None:
        engine = ScoringEngine()
        low_stability = engine.score(0.5, 1.0, 0.5, 0.5, 0.0, tier="alpha")
        high_stability = engine.score(0.5, 1.0, 0.5, 0.5, 1.0, tier="alpha")
        self.assertGreaterEqual(low_stability, 0)
        self.assertLessEqual(low_stability, 100)
        self.assertLessEqual(high_stability, 100)

    def test_alpha_prefers_momentum_over_stability(self) -> None:
        engine = ScoringEngine()
        alpha_momentum = engine.score(0.5, 1.0, 0.5, 0.5, 0.0, tier="alpha")
        alpha_stability = engine.score(0.5, 0.0, 0.5, 0.5, 1.0, tier="alpha")
        self.assertGreater(alpha_momentum, alpha_stability)

    def test_big_tier_prefers_stability(self) -> None:
        engine = ScoringEngine()
        big_stability = engine.score(0.5, 0.0, 0.5, 0.5, 1.0, tier="big")
        big_momentum = engine.score(0.5, 1.0, 0.5, 0.5, 0.0, tier="big")
        self.assertGreater(big_stability, big_momentum)

    def test_unknown_tier_falls_back_to_default_weights(self) -> None:
        engine = ScoringEngine()
        self.assertEqual(engine.score(1, 1, 1, 1, 1, tier="unknown"), 100)


class TierConfigTests(unittest.TestCase):
    def test_classify_tier_bands(self) -> None:
        s = Settings(
            tier_alpha_max_fdv=200_000,
            tier_low_max_fdv=5_000_000,
            tier_mid_max_fdv=100_000_000,
        )
        self.assertEqual(s.classify_tier(100_000), "alpha")
        self.assertEqual(s.classify_tier(200_000), "alpha")
        self.assertEqual(s.classify_tier(200_001), "low")
        self.assertEqual(s.classify_tier(5_000_000), "low")
        self.assertEqual(s.classify_tier(50_000_000), "mid")
        self.assertEqual(s.classify_tier(500_000_000), "big")


class RiskFilterTierTests(unittest.TestCase):
    def test_alpha_tier_tolerates_higher_price_swing(self) -> None:
        risk = RiskFilter()
        safe, _ = risk.is_safe(
            {"price_change_percentage_24h": 40, "total_volume": 1_000_000, "symbol": "x"},
            {"liquidity": 500_000},
            tier="alpha",
        )
        self.assertTrue(safe)
        safe_mid, _ = risk.is_safe(
            {"price_change_percentage_24h": 40, "total_volume": 1_000_000, "symbol": "x"},
            {"liquidity": 500_000},
            tier="mid",
        )
        self.assertFalse(safe_mid)


class RugCheckerTests(unittest.TestCase):
    def test_blocks_on_post_launch_mint(self) -> None:
        client = Mock()
        client.get_token_security.return_value = {"top10_pct": 10}
        client.get_holders.return_value = [{"pct": 5}]
        client.get_mint_burn.return_value = [{"type": "mint"}]
        client.get_pool_transactions.return_value = []
        checker = RugChecker(client=client)
        result = checker.check("tok11111111111111111111111111111111111111111")
        self.assertTrue(result["blocked"])
        self.assertIn("post_launch_mint", result["flags"])

    def test_blocks_on_top_holder_concentration(self) -> None:
        client = Mock()
        client.get_token_security.return_value = {"top10_pct": 10}
        client.get_holders.return_value = [{"pct": 55}]
        client.get_mint_burn.return_value = []
        client.get_pool_transactions.return_value = []
        checker = RugChecker(client=client)
        result = checker.check("tok11111111111111111111111111111111111111111")
        self.assertTrue(result["blocked"])
        self.assertIn("top_holder_concentration", result["flags"])

    def test_blocks_on_liquidity_removal(self) -> None:
        client = Mock()
        client.get_token_security.return_value = {"top10_pct": 10}
        client.get_holders.return_value = [{"pct": 5}]
        client.get_mint_burn.return_value = []
        client.get_pool_transactions.return_value = [{"type": "remove_liquidity"}]
        checker = RugChecker(client=client)
        result = checker.check("tok11111111111111111111111111111111111111111", pool_address="pool111")
        self.assertTrue(result["blocked"])
        self.assertIn("liquidity_removal", result["flags"])

    def test_passes_when_clean(self) -> None:
        client = Mock()
        client.get_token_security.return_value = {"top10_pct": 10}
        client.get_holders.return_value = [{"pct": 5}]
        client.get_mint_burn.return_value = []
        client.get_pool_transactions.return_value = []
        checker = RugChecker(client=client)
        result = checker.check("tok11111111111111111111111111111111111111111")
        self.assertFalse(result["blocked"])


class GenLayerOnchainTests(unittest.TestCase):
    def test_normalize_trade_payload_keeps_onchain_context(self) -> None:
        normalized = normalize_trade_payload(
            {
                "symbol": "ABC",
                "risk_flags": [],
                "onchain_context": "top holder 55%",
                "tier": "alpha",
            }
        )
        self.assertEqual(normalized["onchain_context"], "top holder 55%")
        self.assertEqual(normalized["tier"], "alpha")

    def test_build_decision_prompt_includes_onchain(self) -> None:
        prompt = build_decision_prompt(
            {"token": "ABC", "onchain_context": "mint detected", "tier": "alpha"}
        )
        self.assertIn("mint detected", prompt)
        self.assertIn("alpha", prompt)


class CambrianUsageTests(unittest.TestCase):
    """TASK 2 — Cambrian API call counter, api_call_log, and TTL cache."""

    def test_api_call_log_and_usage_counts(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            db.log_api_call("solana/tokens/security", 200)
            db.log_api_call("solana/tokens/security", 500)
            usage = db.get_api_usage()
            self.assertGreaterEqual(usage["today_calls"], 2)
            self.assertGreaterEqual(usage["month_calls"], 2)
            self.assertGreaterEqual(usage["total_calls"], 2)

    @staticmethod
    def _fake_response(payload):
        response = Mock()
        response.status_code = 200
        response.raise_for_status = Mock()
        response.json.return_value = payload
        return response

    def test_ttl_cache_serves_repeat_call_from_cache(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            fake_settings = types.SimpleNamespace(
                cambrian_base_url="https://api.cambrian.org",
                cambrian_api_key="test-key",
                cambrian_cache_ttl_seconds=300,
            )
            with patch("data.cambrian_client.settings", fake_settings), patch("data.cambrian_client.requests.get") as mock_get:
                mock_get.return_value = self._fake_response(
                    {"priceUSD": 1.23, "priceChangePercent": 5, "volumeUSD": 1000, "symbol": "AAA", "tokenAddress": "tok1"}
                )
                client = CambrianClient(db=db)
                first = client.get_price_trend("tok1")
                second = client.get_price_trend("tok1")
            self.assertEqual(first["current_price"], 1.23)
            self.assertEqual(second["current_price"], 1.23)
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(client.call_count, 1)
            self.assertEqual(client.cache_hits, 1)

    def test_ttl_cache_expired_refetches(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            fake_settings = types.SimpleNamespace(
                cambrian_base_url="https://api.cambrian.org",
                cambrian_api_key="test-key",
                cambrian_cache_ttl_seconds=0,
            )
            with patch("data.cambrian_client.settings", fake_settings), patch("data.cambrian_client.requests.get") as mock_get:
                mock_get.return_value = self._fake_response(
                    {"priceUSD": 1.23, "priceChangePercent": 5, "volumeUSD": 1000, "symbol": "AAA", "tokenAddress": "tok1"}
                )
                client = CambrianClient(db=db)
                client.get_price_trend("tok1")
                client.get_price_trend("tok1")
            self.assertEqual(mock_get.call_count, 2)
            self.assertEqual(client.cache_hits, 0)

    def test_wallet_activity_include_history_computes_drop(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            fake_settings = types.SimpleNamespace(
                cambrian_base_url="https://api.cambrian.org",
                cambrian_api_key="test-key",
                cambrian_cache_ttl_seconds=300,
            )
            with patch("data.cambrian_client.settings", fake_settings), patch("data.cambrian_client.requests.get") as mock_get:
                def side_effect(url, params=None, headers=None, timeout=None):
                    if "holder-token-balances" in url:
                        return self._fake_response({"tokenAddress": "X", "holderCount": 5})
                    if "wallet-balance-history" in url:
                        return self._fake_response([{"tokenAddress": "X", "balanceBefore": 1000, "balanceAfter": 400, "blockTime": "t"}])
                    raise AssertionError(f"unexpected url {url}")

                mock_get.side_effect = side_effect
                client = CambrianClient(db=db)
                activity = client.get_wallet_activity("w1", include_history=True)
            self.assertAlmostEqual(activity["balance_drop_pct"], 60.0)

    def test_cambrian_usage_command_output(self) -> None:
        from main import cambrian_usage_command

        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            db.log_api_call("solana/tokens/security", 200)
            with patch("main.Database", return_value=db), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                cambrian_usage_command()
            output = stdout.getvalue()
        self.assertIn("Cambrian API usage", output)
        self.assertIn("today calls", output)
        self.assertIn("monthly budget", output)


class LiquidityFallbackTests(unittest.TestCase):
    """Liquidity data gap — volume-derived proxy + skip check when data absent."""

    @staticmethod
    def _fake_response(payload):
        response = Mock()
        response.status_code = 200
        response.raise_for_status = Mock()
        response.json.return_value = payload
        return response

    @staticmethod
    def _settings():
        return types.SimpleNamespace(
            cambrian_base_url="https://api.cambrian.org",
            cambrian_api_key="test-key",
            cambrian_cache_ttl_seconds=300,
            cambrian_liquidity_volume_divisor=5,
        )

    def test_liquidity_derived_from_volume_with_divisor(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            with patch("data.cambrian_client.settings", self._settings()), patch("data.cambrian_client.requests.get") as mock_get:
                mock_get.return_value = self._fake_response(
                    {
                        "tokenSymbol": "AAA",
                        "poolPairToken": "SOL",
                        "poolAddress": "pool1",
                        "poolDex": "pump_amm",
                        "volume24hUSD": 100000,
                        "tokenPrice": 0.5,
                    }
                )
                client = CambrianClient(db=db)
                data = client.get_liquidity_volume("tok1")
            self.assertEqual(data["liquidity"], 20000.0)
            self.assertEqual(data["volume_24h"], 100000.0)

    def test_liquidity_zero_volume_yields_zero_liquidity(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            with patch("data.cambrian_client.settings", self._settings()), patch("data.cambrian_client.requests.get") as mock_get:
                mock_get.return_value = self._fake_response(
                    {"tokenSymbol": "BBB", "poolPairToken": "SOL", "poolAddress": "p2", "poolDex": "pump_amm", "volume24hUSD": 0, "tokenPrice": 0.5}
                )
                client = CambrianClient(db=db)
                data = client.get_liquidity_volume("tok2")
            self.assertEqual(data["liquidity"], 0.0)

    def test_risk_filter_skips_liquidity_check_when_data_absent(self) -> None:
        risk = RiskFilter()
        safe, reason = risk.is_safe(
            {"price_change_percentage_24h": 5, "total_volume": 0},
            {"liquidity": 0},
        )
        self.assertTrue(safe)

    def test_risk_filter_still_blocks_thin_liquidity(self) -> None:
        risk = RiskFilter()
        safe, reason = risk.is_safe(
            {"price_change_percentage_24h": 5, "total_volume": 100},
            {"liquidity": 5000},
        )
        self.assertFalse(safe)
        self.assertIn("liquidity", reason.lower())

    def test_risk_filter_passes_decent_liquidity(self) -> None:
        risk = RiskFilter()
        safe, _ = risk.is_safe(
            {"price_change_percentage_24h": 5, "total_volume": 100000},
            {"liquidity": 250000},
        )
        self.assertTrue(safe)


class ExitConditionsTests(unittest.TestCase):
    """TASK 3 — take-profit partial, market crash, and dev/whale exit triggers."""

    @staticmethod
    def _insert_open_position(
        db,
        symbol="AAA",
        entry=1.0,
        last_price=None,
        last_price_at=None,
        partial=False,
        dev=None,
        holders=None,
    ) -> int:
        pid = db.insert_position(
            {
                "coin_id": symbol.lower(),
                "symbol": symbol,
                "decision_source": "test",
                "entry_price": entry,
                "size_usd": 100,
                "size_pct": 0.01,
                "take_profit_price": entry * 1.2,
                "stop_loss_price": entry * 0.93,
                "trailing_stop_pct": 0.05,
                "status": "OPEN",
                "execution_tx_hash": None,
                "tracked_dev_wallet": dev,
                "tracked_top_holder_wallets": holders or [],
                "partial_sell_done": partial,
            }
        )
        if last_price is not None:
            db.update_position_last_price(pid, last_price, last_price_at)
        return pid

    @staticmethod
    def _position(db, pid: int) -> dict:
        return next(p for p in db.get_open_positions() if p["id"] == pid)

    def test_partial_take_profit_triggers_at_2x(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            pid = self._insert_open_position(db, entry=1.0)
            result = manager.check_exit_conditions(self._position(db, pid), 2.1)
            self.assertEqual(result["action"], "PARTIAL_SELL")
            positions = db.get_open_positions()
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions[0]["partial_sell_done"], 1)
            result2 = manager.check_exit_conditions(self._position(db, pid), 3.0)
            self.assertNotEqual(result2["action"], "PARTIAL_SELL")

    def test_partial_take_profit_not_triggered_below_2x(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            pid = self._insert_open_position(db, entry=1.0)
            result = manager.check_exit_conditions(self._position(db, pid), 1.9)
            self.assertEqual(result["action"], "HOLD")
            self.assertEqual(db.get_open_positions()[0]["partial_sell_done"], 0)

    def test_crash_exit_triggers_within_window(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            now = datetime.utcnow()
            pid = self._insert_open_position(
                db, entry=1.0, last_price=1.0, last_price_at=(now - timedelta(minutes=5)).isoformat()
            )
            result = manager.check_exit_conditions(self._position(db, pid), 0.70, now=now)
            self.assertEqual(result["action"], "CRASH_EXIT")
            self.assertEqual(len(db.get_open_positions()), 0)

    def test_crash_exit_not_triggered_on_small_drop(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            now = datetime.utcnow()
            pid = self._insert_open_position(
                db, entry=1.0, last_price=1.0, last_price_at=(now - timedelta(minutes=5)).isoformat()
            )
            result = manager.check_exit_conditions(self._position(db, pid), 0.90, now=now)
            self.assertEqual(result["action"], "HOLD")

    def test_crash_exit_not_triggered_when_baseline_stale(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            now = datetime.utcnow()
            pid = self._insert_open_position(
                db, entry=1.0, last_price=1.0, last_price_at=(now - timedelta(minutes=30)).isoformat()
            )
            result = manager.check_exit_conditions(self._position(db, pid), 0.70, now=now)
            self.assertEqual(result["action"], "HOLD")

    def test_dev_whale_exit_triggers_on_dump(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            cambrian = Mock()
            cambrian.get_wallet_activity.return_value = {"balance_drop_pct": 35}
            manager = TradeManager(db, cambrian=cambrian)
            pid = self._insert_open_position(db, dev="devwallet", holders=["holder1", "holder2"])
            result = manager.check_exit_conditions(self._position(db, pid), 1.0)
            self.assertEqual(result["action"], "DEV_WHALE_EXIT")
            self.assertEqual(len(db.get_open_positions()), 0)
            cambrian.get_wallet_activity.assert_called_with("devwallet", include_history=True)

    def test_dev_whale_exit_not_triggered_on_small_dump(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            cambrian = Mock()
            cambrian.get_wallet_activity.return_value = {"balance_drop_pct": 10}
            manager = TradeManager(db, cambrian=cambrian)
            pid = self._insert_open_position(db, dev="devwallet", holders=["holder1"])
            result = manager.check_exit_conditions(self._position(db, pid), 1.0)
            self.assertEqual(result["action"], "HOLD")
            self.assertEqual(len(db.get_open_positions()), 1)

    def test_dev_whale_exit_skipped_without_tracked_wallets(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            cambrian = Mock()
            cambrian.get_wallet_activity.return_value = {"balance_drop_pct": 100}
            manager = TradeManager(db, cambrian=cambrian)
            pid = self._insert_open_position(db)
            result = manager.check_exit_conditions(self._position(db, pid), 1.0)
            self.assertEqual(result["action"], "HOLD")
            cambrian.get_wallet_activity.assert_not_called()

    def test_crash_exit_has_priority_over_partial_tp(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            now = datetime.utcnow()
            pid = self._insert_open_position(
                db, entry=1.0, last_price=2.5, last_price_at=(now - timedelta(minutes=5)).isoformat()
            )
            result = manager.check_exit_conditions(self._position(db, pid), 1.3, now=now)
            self.assertEqual(result["action"], "CRASH_EXIT")
            self.assertEqual(len(db.get_open_positions()), 0)

    def test_dev_whale_exit_has_priority_over_partial_tp(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            cambrian = Mock()
            cambrian.get_wallet_activity.return_value = {"balance_drop_pct": 40}
            manager = TradeManager(db, cambrian=cambrian)
            pid = self._insert_open_position(db, entry=1.0, dev="devwallet")
            result = manager.check_exit_conditions(self._position(db, pid), 2.5)
            self.assertEqual(result["action"], "DEV_WHALE_EXIT")
            self.assertEqual(len(db.get_open_positions()), 0)

    def test_broadcast_sell_is_noop_in_dry_run(self) -> None:
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            with patch("execution.trade_manager.SepoliaExecutor") as mock_exec:
                manager._broadcast_sell({"symbol": "AAA"}, 1.0, "CRASH_EXIT")
            mock_exec.assert_not_called()

    def test_broadcast_sell_broadcasts_when_live(self) -> None:
        fake_settings = types.SimpleNamespace(
            dry_run=False,
            sepolia_rpc_url="http://localhost:8545",
            wallet_private_key="0x" + "ab" * 32,
        )
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            with patch("execution.trade_manager.settings", fake_settings), patch("execution.trade_manager.SepoliaExecutor") as mock_exec:
                manager._broadcast_sell({"symbol": "AAA"}, 1.0, "CRASH_EXIT")
            mock_exec.assert_called_once()

    def test_monitor_positions_applies_new_exit_triggers(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            now = datetime.utcnow()
            self._insert_open_position(
                db, symbol="AAA", entry=1.0, last_price=1.0, last_price_at=(now - timedelta(minutes=5)).isoformat()
            )
            manager.monitor_positions([{"symbol": "AAA", "current_price": 0.70}])
            self.assertEqual(len(db.get_open_positions()), 0)


class NotificationTests(unittest.TestCase):
    """Detailed Telegram notifications: ticker, contract address, buy/sell amount."""

    def test_notification_text_includes_ticker_ca_amount(self) -> None:
        text = TradeManager._notification_text(
            {"symbol": "AAA", "coin_id": "0x1234", "entry_price": 1.5},
            "CRASH_EXIT",
            50.0,
            "AAA dropped 30.0%",
        )
        self.assertIn("CRASH_EXIT", text)
        self.assertIn("Ticker: AAA", text)
        self.assertIn("CA: 0x1234", text)
        self.assertIn("Amount: $50.00", text)
        self.assertIn("Price: $1.5", text)
        self.assertIn("Reason: AAA dropped 30.0%", text)

    def test_open_notification_sends_buy_with_amount(self) -> None:
        plan = TradePlan(size_pct=0.02, size_usd=50.0, take_profit_price=1.5, stop_loss_price=0.9, trailing_stop_pct=0.05)
        with _tmp_db_file() as tmp:
            manager = TradeManager(Database(db_path=tmp))
            with patch("execution.trade_manager.send_telegram_message") as mock_send:
                manager._send_open_notification({"symbol": "AAA", "coin_id": "0x1234", "price": 1.2}, plan)
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
        self.assertIn("BUY", text)
        self.assertIn("Ticker: AAA", text)
        self.assertIn("CA: 0x1234", text)
        self.assertIn("Amount: $50.00", text)

    def test_partial_sell_notification_uses_scaled_amount(self) -> None:
        with _tmp_db_file() as tmp:
            db = Database(db_path=tmp)
            manager = TradeManager(db)
            pid = ExitConditionsTests._insert_open_position(db, entry=1.0)
            position = next(p for p in db.get_open_positions() if p["id"] == pid)
            position["size_usd"] = 100.0
            with patch("execution.trade_manager.send_telegram_message") as mock_send:
                manager._apply_partial_sell(position, 2.1, datetime.utcnow())
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
        self.assertIn("TAKE_PROFIT", text)
        self.assertIn("Ticker: AAA", text)
        self.assertIn("Amount: $50.00", text)
