"""Fetch trending DEX pair data from Dexscreener."""

from __future__ import annotations

from typing import Any

import requests

from config import settings


class DexscreenerFetcher:
    """Dexscreener integrations for liquidity and volume context."""

    def __init__(self) -> None:
        self.base_url = settings.dexscreener_base_url

    def fetch_trending_pairs(self) -> list[dict[str, Any]]:
        """Fetch boosted token profiles as trend candidates and normalize fields."""
        # Public endpoint shape can change; parser is defensive.
        url = f"{self.base_url}/token-boosts/latest/v1"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            raw = response.json()
        except requests.RequestException:
            return []

        parsed: list[dict[str, Any]] = []
        for item in raw[:200]:
            token_address = item.get("tokenAddress") or ""
            chain_id = item.get("chainId", "unknown")
            symbol = (item.get("symbol") or token_address[:6] or "UNK").upper()
            parsed.append(
                {
                    "symbol": symbol,
                    "pair_address": token_address,
                    "dex_id": str(chain_id),
                    "liquidity": float(item.get("liquidityUsd", 0) or 0),
                    "volume_24h": float(item.get("volume24hUsd", 0) or 0),
                    "price_usd": float(item.get("priceUsd", 0) or 0),
                }
            )
        return parsed

    def pair_lookup(self, query: str) -> dict[str, Any]:
        """Lookup by ticker/symbol to enrich per-coin data; empty dict on failure."""
        if not query:
            return {}

        url = f"{self.base_url}/latest/dex/search"
        try:
            response = requests.get(url, params={"q": query}, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            return {}

        pairs = payload.get("pairs") or []
        if not pairs:
            return {}

        top = pairs[0]
        liquidity = top.get("liquidity", {}) or {}
        volume = top.get("volume", {}) or {}

        return {
            "liquidity": float(liquidity.get("usd", 0) or 0),
            "volume_24h": float(volume.get("h24", 0) or 0),
            "price_usd": float(top.get("priceUsd", 0) or 0),
            "pair_address": top.get("pairAddress", ""),
            "dex_id": top.get("dexId", ""),
        }
