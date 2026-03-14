"""Fetch market data from CoinGecko for mid-cap assets."""

from __future__ import annotations

from typing import Any

import requests

from config import settings


class CoinGeckoFetcher:
    """CoinGecko markets endpoint wrapper with safe fallback behavior."""

    def __init__(self) -> None:
        self.base_url = settings.coingecko_base_url

    def fetch_mid_cap_markets(self, per_page: int = 200) -> list[dict[str, Any]]:
        """Retrieve liquid assets and keep mid-cap range data points."""
        url = f"{self.base_url}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(per_page, 250),
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "24h",
        }

        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            return []

        # Mid-cap heuristic: 100M to 10B market cap.
        mid_caps = [
            coin
            for coin in data
            if 100_000_000 <= (coin.get("market_cap") or 0) <= 10_000_000_000
        ]
        return mid_caps[:200]
