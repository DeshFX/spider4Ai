"""Narrative classification with Ollama-first and keyword fallback."""

from __future__ import annotations

import json
from typing import Any

import requests

from config import settings

NARRATIVES = ["AI", "DePIN", "Gaming", "Layer2", "RWA", "Restaking", "Meme"]

KEYWORDS = {
    "AI": ["ai", "agent", "inference", "model"],
    "DePIN": ["depin", "compute", "wireless", "render", "storage"],
    "Gaming": ["game", "metaverse", "play", "nft"],
    "Layer2": ["layer2", "rollup", "optimistic", "zk"],
    "RWA": ["rwa", "real world", "treasury", "bond", "asset token"],
    "Restaking": ["restake", "eigen", "avs", "staking"],
    "Meme": ["meme", "dog", "cat", "pepe", "inu"],
}


class NarrativeDetector:
    """Classify coin narratives using local LLM or deterministic keyword matcher."""

    def __init__(self) -> None:
        self.ollama_url = settings.ollama_base_url
        self.model = settings.ollama_model

    def classify(self, coin: dict[str, Any]) -> tuple[str, float, str]:
        """Return narrative, confidence, and reasoning note."""
        prompt = (
            "Classify this crypto project into one category only from: "
            f"{', '.join(NARRATIVES)}. "
            "Return JSON with keys narrative and confidence (0-1).\n"
            f"Name: {coin.get('name')}\n"
            f"Symbol: {coin.get('symbol')}\n"
            f"Description hint: {coin.get('name')} {coin.get('symbol')}"
        )
        llm_result = self._classify_with_ollama(prompt)
        if llm_result:
            narrative = llm_result.get("narrative", "Meme")
            confidence = float(llm_result.get("confidence", 0.6))
            if narrative not in NARRATIVES:
                narrative = "Meme"
            confidence = max(0.0, min(1.0, confidence))
            return narrative, confidence, "Ollama classification"

        fallback = self._keyword_fallback(coin)
        return fallback, 0.5, "Keyword fallback classification"

    def _classify_with_ollama(self, prompt: str) -> dict[str, Any] | None:
        url = f"{self.ollama_url}/api/generate"
        try:
            response = requests.post(
                url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            body = payload.get("response", "")
            return json.loads(body)
        except Exception:
            return None

    def _keyword_fallback(self, coin: dict[str, Any]) -> str:
        text = f"{coin.get('name', '')} {coin.get('symbol', '')}".lower()
        for narrative, words in KEYWORDS.items():
            if any(word in text for word in words):
                return narrative
        return "Layer2"
