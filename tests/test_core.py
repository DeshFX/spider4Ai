"""Core unit tests for Spider4AI engines, GenLayer integration, CLI, and storage."""

from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

# Provide tiny dependency stubs so tests can run in minimal environments
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    class _ReqExc(Exception):
        pass

    def _post(*args, **kwargs):
        raise _ReqExc("requests stub")

    requests_stub.RequestException = _ReqExc
    requests_stub.post = _post
    sys.modules["requests"] = requests_stub

if "apscheduler" not in sys.modules:
    apscheduler = types.ModuleType("apscheduler")
    schedulers = types.ModuleType("apscheduler.schedulers")
    background = types.ModuleType("apscheduler.schedulers.background")

    class BackgroundScheduler:
        def add_job(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def shutdown(self, wait=False):
            return None

    background.BackgroundScheduler = BackgroundScheduler
    schedulers.background = background
    apscheduler.schedulers = schedulers
    sys.modules["apscheduler"] = apscheduler
    sys.modules["apscheduler.schedulers"] = schedulers
    sys.modules["apscheduler.schedulers.background"] = background

if "web3" not in sys.modules:
    web3_module = types.ModuleType("web3")

    class _Account:
        def from_key(self, key):
            return types.SimpleNamespace(address="0xabc")

    class _Eth:
        def __init__(self):
            self.account = _Account()

    class Web3:
        HTTPProvider = object

        def __init__(self, provider=None):
            self.provider = provider
            self.eth = _Eth()

        def is_connected(self):
            return True

    web3_module.Web3 = Web3
    sys.modules["web3"] = web3_module

from typer.testing import CliRunner

from agents.spider_agent import SpiderAgent
from config import ConfigError, Settings
from data.narrative_detector import NarrativeDetector
from engine.accumulation_detector import AccumulationDetector
from engine.risk_filter import RiskFilter
from engine.scoring_engine import ScoringEngine
from execution.dex_swap import swap_eth_to_token
from execution.trade_manager import TradeManager, calculate_position_size
from genlayer.service import (
    GenLayerService,
    LocalFallbackDecisionEngine,
    build_decision_prompt,
    normalize_decision_payload,
    normalize_trade_payload,
)
from main import app
from storage.database import Database


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
        with patch.object(agent.db, "blacklist_token") as blacklist_token:
            opportunity = {"coin_id": "abc", "symbol": "ABC", "genlayer_decision": "SCAM", "genlayer_reasoning": "rug risk", "decision_source": "genlayer"}
            agent._execute_decision(opportunity)
        self.assertEqual(opportunity["execution_status"], "blacklisted")
        blacklist_token.assert_called_once()

    def test_execute_decision_respects_dry_run(self) -> None:
        agent = SpiderAgent(decision_service=Mock())
        with patch.object(agent.trade_manager, "should_open_position", return_value=(True, "approved")), patch.object(agent.trade_manager, "compute_position_size") as cps, patch.object(agent.trade_manager, "record_open_position"):
            cps.return_value = types.SimpleNamespace(size_pct=0.02, size_usd=200, take_profit_price=1.2, stop_loss_price=0.9, trailing_stop_pct=0.05)
            opportunity = {"coin_id": "abc", "symbol": "ABC", "genlayer_decision": "BUY", "genlayer_confidence": 0.9, "genlayer_disagreement": 0.1, "price": 1.0, "decision_source": "genlayer", "risk_flags": []}
            agent._execute_decision(opportunity)
        self.assertEqual(opportunity["execution_status"], "dry_run")


class TradeManagerTests(unittest.TestCase):
    def test_position_sizing_respects_bounds(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            manager = TradeManager(Database(db_path=tmp.name))
            plan = manager.compute_position_size({"genlayer_confidence": 0.82, "genlayer_disagreement": 0.1, "market_stability": 0.7, "price": 1.0})
            self.assertGreaterEqual(plan.size_pct, 0.01)
            self.assertLessEqual(plan.size_usd, 500)

    def test_calculate_position_size_scales_with_confidence(self) -> None:
        self.assertEqual(calculate_position_size(0.5), 0.00045)
        self.assertEqual(calculate_position_size(0.8), 0.00054)
        self.assertEqual(calculate_position_size(0.0), 0.0003)

    def test_exit_logic_hits_take_profit(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            manager = TradeManager(Database(db_path=tmp.name))
            exit_reason, pnl_pct = manager.evaluate_exit({"entry_price": 1.0, "take_profit_price": 1.2, "stop_loss_price": 0.93, "trailing_stop_pct": 0.05, "peak_price": 1.0}, 1.21)
            self.assertEqual(exit_reason, "TAKE_PROFIT")
            self.assertGreater(pnl_pct, 0)


class DatabaseTests(unittest.TestCase):
    def test_insert_and_query(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(db_path=tmp.name)
            db.insert_opportunities([{"coin_id": "abc", "symbol": "ABC", "narrative": "AI", "score": 73.5, "accumulation_score": 0.62, "volume_24h": 1_000_000, "liquidity": 150_000, "price": 1.23, "reason": "test row", "summary": "summary", "risk_flags": ["thin_liquidity"], "signal_strength": 0.73, "source": "coingecko+dexscreener", "genlayer_status": "submitted", "genlayer_decision": "BUY", "genlayer_confidence": 0.88, "genlayer_reasoning": "Strong setup", "genlayer_votes": [{"decision": "BUY", "confidence": 0.9}], "decision_source": "genlayer", "execution_status": "submitted"}])
            rows = db.get_latest_opportunities(limit=5)
            self.assertEqual(rows[0]["genlayer_decision"], "BUY")


class CliTests(unittest.TestCase):
    def test_status_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("genlayer_enabled", result.stdout)


class SwapTests(unittest.TestCase):
    def test_swap_respects_dry_run(self) -> None:
        self.assertIsNone(swap_eth_to_token("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 0.82))

    def test_swap_skips_low_confidence(self) -> None:
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertIsNone(swap_eth_to_token("0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 0.5))
        self.assertIn("[SKIP] Confidence too low", stdout.getvalue())
        self.assertIn("calculated_size=0.00045 ETH", stdout.getvalue())

    def test_swap_test_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["swap-test"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[SWAP PREVIEW] Preview complete; no transaction was broadcast", result.stdout)
