"""Core unit tests for Spider4AI engines and storage."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from unittest.mock import patch

# Provide a tiny requests stub so tests can run in minimal environments
# where dependencies are not installed yet.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    class _ReqExc(Exception):
        pass

    requests_stub.RequestException = _ReqExc
    sys.modules["requests"] = requests_stub

from data.narrative_detector import NarrativeDetector
from engine.accumulation_detector import AccumulationDetector
from engine.risk_filter import RiskFilter
from engine.scoring_engine import ScoringEngine
from storage.database import Database


class EngineTests(unittest.TestCase):
    def test_scoring_engine_bounds(self) -> None:
        engine = ScoringEngine()
        self.assertEqual(engine.score(1, 1, 1, 1, 1), 100)
        self.assertEqual(engine.score(0, 0, 0, 0, 0), 0)

    def test_accumulation_normalized(self) -> None:
        detector = AccumulationDetector()
        score = detector.score(
            {
                "total_volume": 8_000_000,
                "market_cap": 1_000_000_000,
                "price_change_percentage_24h": 3,
            },
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
            narrative, confidence, reason = detector.classify(
                {"name": "AI Agents Token", "symbol": "AIA"}
            )
        self.assertEqual(narrative, "AI")
        self.assertEqual(confidence, 0.5)
        self.assertIn("fallback", reason.lower())

    def test_invalid_llm_payload_is_sanitized(self) -> None:
        detector = NarrativeDetector()
        with patch.object(
            detector,
            "_classify_with_ollama",
            return_value={"narrative": "UnknownTag", "confidence": 9.0},
        ):
            narrative, confidence, _ = detector.classify({"name": "X", "symbol": "Y"})
        self.assertEqual(narrative, "Meme")
        self.assertEqual(confidence, 1.0)


class DatabaseTests(unittest.TestCase):
    def test_insert_and_query(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(db_path=tmp.name)
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
                    }
                ]
            )
            rows = db.get_latest_opportunities(limit=5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "ABC")


if __name__ == "__main__":
    unittest.main()
