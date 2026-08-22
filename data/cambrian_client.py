"""Single external data source for Spider4AI via the Cambrian API."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.cambrian.org"
MAX_RETRIES = 3
REQUEST_TIMEOUT_SECONDS = 20
BACKOFF_BASE_SECONDS = 1.0

SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"

CACHE_TTL_SECONDS_DEFAULT = 300

CACHEABLE_ENDPOINTS = {
    "solana/price-volume",
    "solana/token-details",
    "solana/tokens/security",
    "solana/tokens/holders",
    "solana/token-mint-burn-transactions",
    "solana/token-pool-search",
    "solana/orca/pool",
    "deep42/social-data/token-analysis",
    "solana/holder-token-balances",
    "solana/wallet-balance-history",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class CambrianClient:
    """Thin HTTP wrapper around the Cambrian API with retry + safe fallback.

    The Cambrian API authenticates with an ``X-API-Key`` header and returns
    ClickHouse-style columnar payloads of the form ``[{columns, data, rows}]``
    (or a flat JSON object for some endpoints). Responses are normalised into
    plain dicts before being returned.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, db: Any | None = None) -> None:
        self.base_url = (base_url or settings.cambrian_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.cambrian_api_key
        self._db_instance = db
        self._cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.call_count = 0
        self.cache_hits = 0
        self._registry_map: dict[str, str] | None = None
        self._registry_at: float = 0.0

    def _db(self) -> Any:
        if self._db_instance is None:
            from storage.database import Database

            self._db_instance = Database()
        return self._db_instance

    def _cache_ttl(self) -> float:
        ttl = getattr(settings, "cambrian_cache_ttl_seconds", CACHE_TTL_SECONDS_DEFAULT)
        try:
            return float(ttl)
        except (TypeError, ValueError):
            return CACHE_TTL_SECONDS_DEFAULT

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any] | None) -> tuple[Any, ...]:
        items = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        return (path, items)

    def _cache_get(self, key: tuple[Any, ...]) -> Any:
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry["at"] > self._cache_ttl():
            self._cache.pop(key, None)
            return None
        self.cache_hits += 1
        return entry["value"]

    def _cache_set(self, key: tuple[Any, ...], value: Any) -> None:
        self._cache[key] = {"at": time.time(), "value": value}

    def _log_call(self, endpoint: str, response_status: int | None) -> None:
        """Increment the in-memory counter and persist the call into api_call_log."""
        self.call_count += 1
        try:
            self._db().log_api_call(endpoint, response_status)
        except Exception:
            logger.debug("Failed to persist Cambrian API call log", exc_info=True)

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }

    @staticmethod
    def _normalize(payload: Any) -> dict[str, Any]:
        """Convert a columnar Cambrian payload into a single flat dict.

        Columnar payloads look like ``[{"columns": [{"name": ...}], "data": [[...]], "rows": 1}]``.
        Some endpoints (e.g. social-data) return a flat JSON object directly.
        """
        if isinstance(payload, list):
            for block in payload:
                if not isinstance(block, dict):
                    continue
                columns = block.get("columns")
                rows = block.get("data")
                if not columns or not rows:
                    continue
                names = [col.get("name") for col in columns]
                first = rows[0]
                return {name: first[idx] for idx, name in enumerate(names) if name}
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        """Convert a Cambrian payload into a list of flat dicts.

        Handles both columnar payloads (``[{columns, data, rows}]``) and plain
        JSON lists of flat dicts (as returned by the deep42 endpoints).
        """
        if isinstance(payload, list):
            result: list[dict[str, Any]] = []
            for block in payload:
                if not isinstance(block, dict):
                    continue
                columns = block.get("columns")
                rows = block.get("data")
                if columns and rows:
                    names = [col.get("name") for col in columns]
                    for row in rows:
                        result.append({name: row[idx] for idx, name in enumerate(names) if name})
                else:
                    result.append(block)
            return result
        if isinstance(payload, dict):
            return [payload]
        return []

    def _get_rows(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not self.api_key:
            logger.warning("Cambrian API key is not configured; skipping request to %s", path)
            return []
        cacheable = path in CACHEABLE_ENDPOINTS
        key = self._cache_key(path, params)
        if cacheable:
            cached = self._cache_get(key)
            if cached is not None:
                return list(cached)
        url = f"{self.base_url}/{path}"
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                rows = self._rows(response.json())
                self._log_call(path, response.status_code)
                if cacheable:
                    self._cache_set(key, rows)
                return rows
            except requests.HTTPError as exc:
                last_error = exc
                status = getattr(exc.response, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    self._log_call(path, status)
                    logger.warning("Cambrian GET %s failed (HTTP %s, not retrying): %s", path, status, exc)
                    return []
                logger.warning(
                    "Cambrian GET %s attempt %d/%d failed: %s",
                    path,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Cambrian GET %s attempt %d/%d failed: %s",
                    path,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        self._log_call(path, None)
        logger.error("Cambrian GET %s failed after %d attempts: %s", path, MAX_RETRIES, last_error)
        return []

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            logger.warning("Cambrian API key is not configured; skipping request to %s", path)
            return {}
        cacheable = path in CACHEABLE_ENDPOINTS
        key = self._cache_key(path, params)
        if cacheable:
            cached = self._cache_get(key)
            if cached is not None:
                return dict(cached)
        url = f"{self.base_url}/{path}"
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = self._normalize(response.json())
                self._log_call(path, response.status_code)
                if cacheable:
                    self._cache_set(key, data)
                return data
            except requests.HTTPError as exc:
                last_error = exc
                status = getattr(exc.response, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    self._log_call(path, status)
                    logger.warning("Cambrian GET %s failed (HTTP %s, not retrying): %s", path, status, exc)
                    return {}
                logger.warning(
                    "Cambrian GET %s attempt %d/%d failed: %s",
                    path,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Cambrian GET %s attempt %d/%d failed: %s",
                    path,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        self._log_call(path, None)
        logger.error("Cambrian GET %s failed after %d attempts: %s", path, MAX_RETRIES, last_error)
        return {}

    def ping(self, timeout: int = 5) -> bool:
        """Lightweight connectivity probe; returns False on any failure."""
        if not self.api_key:
            return False
        try:
            response = requests.get(
                f"{self.base_url}/solana/tokens/security",
                params={"token_address": SOL_TOKEN_ADDRESS},
                headers=self._headers(),
                timeout=timeout,
            )
            self._log_call("solana/tokens/security", response.status_code)
            return response.status_code == 200
        except requests.RequestException:
            self._log_call("solana/tokens/security", None)
            return False

    def get_price_trend(self, token: str) -> dict[str, Any]:
        """Price / market-cap / volume trend for a token (replaces CoinGecko).

        Backed by ``/solana/price-volume`` (multi-token variant; the old
        ``price-volume/single`` endpoint was removed), which expects a
        comma-separated list of token mint addresses. ``stability`` and
        ``trend`` are derived locally.
        """
        if not token:
            return {}
        data = self._get(
            "solana/price-volume",
            {"token_addresses": token, "timeframe": "24h"},
        )
        if not data:
            return {}
        price = _to_float(data.get("priceUSD"))
        change = _to_float(data.get("priceChangePercent"))
        volume = _to_float(data.get("volumeUSD"))
        symbol = str(data.get("symbol") or token)
        stability = _clamp(1.0 - abs(change) / 25.0)
        if change >= 10:
            trend = "bullish"
        elif change <= -10:
            trend = "bearish"
        else:
            trend = "neutral"
        return {
            "id": str(data.get("tokenAddress") or token.lower()),
            "symbol": symbol.upper(),
            "name": symbol,
            "current_price": price,
            "price_usd": price,
            "market_cap": 0.0,
            "total_volume": volume,
            "price_change_percentage_24h": change,
            "stability": stability,
            "trend": trend,
        }

    def get_trending_tokens(
        self,
        order_by: str = "volume_usd_24h",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Ranked list of active Solana tokens (replaces the watchlist feed).

        Backed by ``/solana/trending-tokens``. Returns dicts shaped like
        ``get_price_trend`` so the pipeline can treat them as ``coin`` rows.
        """
        rows = self._get_rows(
            "solana/trending-tokens",
            {"order_by": order_by, "limit": limit},
        )
        markets: list[dict[str, Any]] = []
        for data in rows:
            price = _to_float(data.get("currentPriceUSD"))
            change = _to_float(data.get("priceChangePercentage"))
            volume = _to_float(data.get("volume24hUSD"))
            address = str(data.get("tokenAddress") or "")
            symbol = str(data.get("symbol") or address)
            markets.append(
                {
                    "id": address,
                    "address": address,
                    "symbol": symbol.upper(),
                    "name": symbol,
                    "current_price": price,
                    "price_usd": price,
                    "market_cap": 0.0,
                    "total_volume": volume,
                    "volume_24h": volume,
                    "price_change_percentage_24h": change,
                    "stability": _clamp(1.0 - abs(change) / 25.0),
                    "trend": (
                        "bullish" if change >= 10 else "bearish" if change <= -10 else "neutral"
                    ),
                }
            )
        return markets

    def get_token_details(self, token_address: str) -> dict[str, Any]:
        """Full detail snapshot for a Solana token address.

        Backed by ``/solana/token-details``; enriches a trending row with name,
        holder count and FDV when the caller already holds the address.
        """
        if not token_address:
            return {}
        data = self._get("solana/token-details", {"token_address": token_address})
        if not data:
            return {}
        return {
            "address": str(data.get("tokenAddress") or token_address),
            "symbol": str(data.get("symbol") or "").upper(),
            "name": str(data.get("name") or data.get("symbol") or ""),
            "decimals": int(_to_float(data.get("decimals"))),
            "current_price": _to_float(data.get("priceUSD")),
            "volume_24h": _to_float(data.get("volume24hUSD")),
            "holder_count": int(_to_float(data.get("holderCount"))),
            "fdv": _to_float(data.get("fdvUSD")),
            "trade_24h_count": int(_to_float(data.get("trade24hCount"))),
        }

    def get_token_security(self, token_address: str) -> dict[str, Any]:
        """Raw holder-concentration security metrics (replaces get_risk_score data).

        Backed by ``/solana/tokens/security``.
        """
        if not token_address:
            return {}
        data = self._get("solana/tokens/security", {"token_address": token_address})
        if not data:
            return {}
        return {
            "address": str(data.get("address") or token_address),
            "symbol": str(data.get("symbol") or ""),
            "name": str(data.get("name") or ""),
            "holder_count": int(_to_float(data.get("holderCount"))),
            "total_supply": _to_float(data.get("totalSupply")),
            "top5_pct": _to_float(data.get("top5HolderConcentration")),
            "top10_pct": _to_float(data.get("top10HolderConcentration")),
            "top20_pct": _to_float(data.get("top20HolderConcentration")),
            "top50_pct": _to_float(data.get("top50HolderConcentration")),
            "transaction_1d": int(_to_float(data.get("transaction1dCount"))),
            "active_accounts_1d": int(_to_float(data.get("activeAccounts1dCount"))),
            "tx_uniqueness_ratio": _to_float(data.get("txUniquenessRatio")),
            "price_volatility_30d": _to_float(data.get("priceVolatility30d")),
            "security_score": _to_float(data.get("securityScore")),
        }

    def get_holders(self, token_address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Top holders for a token, largest balance first.

        Backed by ``/solana/tokens/holders``. The API renamed its parameter:
        the token mint address must now be sent as ``program_id``. Used to
        detect whale/dev concentration and sudden supply control changes.
        """
        if not token_address:
            return []
        rows = self._get_rows("solana/tokens/holders", {"program_id": token_address, "limit": limit})
        holders: list[dict[str, Any]] = []
        for data in rows:
            holders.append(
                {
                    "address": str(data.get("account") or data.get("ownerAddress") or data.get("address") or ""),
                    # balanceUi shares units with total_supply from /tokens/security,
                    # so RugChecker can compute holder percentage from it.
                    "balance": _to_float(data.get("balanceUi") or data.get("balance")),
                    "balance_usd": _to_float(data.get("balanceUSD")),
                    "pct": _to_float(data.get("percentage") or data.get("share")),
                }
            )
        return holders

    def get_mint_burn(self, token_address: str, limit: int = 20, lookback_days: int = 30) -> list[dict[str, Any]]:
        """Recent mint/burn supply changes (rug-dilution audit trail).

        Backed by ``/solana/token-mint-burn-transactions``. The API now
        requires a ``after_time``/``before_time`` unix-timestamp window.
        A post-launch mint of new supply is a strong rugpull signal.
        """
        if not token_address:
            return []
        now = int(time.time())
        rows = self._get_rows(
            "solana/token-mint-burn-transactions",
            {
                "token_address": token_address,
                "limit": limit,
                "after_time": now - lookback_days * 24 * 3600,
                "before_time": now,
            },
        )
        events: list[dict[str, Any]] = []
        for data in rows:
            events.append(
                {
                    "type": str(data.get("operationType") or data.get("type") or data.get("eventType") or "").lower(),
                    "amount": _to_float(data.get("amount")),
                    "signer": str(data.get("authority") or data.get("signer") or ""),
                    "timestamp": str(data.get("blockTime") or data.get("timestamp") or ""),
                }
            )
        return events

    def get_wallet_history(self, wallet_address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Balance-change audit trail for a wallet (dev-dump detection).

        Backed by ``/solana/wallet-balance-history``.
        """
        if not wallet_address:
            return []
        rows = self._get_rows(
            "solana/wallet-balance-history",
            {"wallet_address": wallet_address, "limit": limit},
        )
        history: list[dict[str, Any]] = []
        for data in rows:
            history.append(
                {
                    "token": str(data.get("tokenAddress") or data.get("token") or ""),
                    "balance_before": _to_float(data.get("balanceBefore") or data.get("preBalance")),
                    "balance_after": _to_float(data.get("balanceAfter") or data.get("postBalance")),
                    "timestamp": str(data.get("timestamp") or data.get("blockTime") or ""),
                }
            )
        return history

    def get_pool_transactions(self, pool_address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recent pool trades/liquidity events (LP-removal detection).

        Backed by ``/solana/pool-transactions``.
        """
        if not pool_address:
            return []
        rows = self._get_rows("solana/pool-transactions", {"pool_address": pool_address, "limit": limit})
        txs: list[dict[str, Any]] = []
        for data in rows:
            txs.append(
                {
                    "type": str(data.get("type") or data.get("eventType") or "").lower(),
                    "amount_usd": _to_float(data.get("amountUSD") or data.get("amountUsd")),
                    "sender": str(data.get("sender") or data.get("trader") or ""),
                    "timestamp": str(data.get("timestamp") or data.get("blockTime") or ""),
                }
            )
        return txs

    def get_social_momentum(self, limit: int = 15, lookback_hours: int = 24) -> list[dict[str, Any]]:
        """Tokens with accelerating social attention (alpha discovery).

        Backed by ``/deep42/social-data/trending-momentum``.
        """
        rows = self._get_rows(
            "deep42/social-data/trending-momentum",
            {"lookback_hours": lookback_hours, "limit": limit},
        )
        result: list[dict[str, Any]] = []
        for item in rows:
            if item.get("tokenSymbol"):
                result.append(
                    {
                        "symbol": str(item.get("tokenSymbol")),
                        "momentum_score": _to_float(item.get("momentumScore")),
                        "tweet_velocity": _to_float(item.get("tweetVelocity")),
                        "sentiment": _to_float(item.get("sentiment")),
                        "total_reach": _to_float(item.get("totalReach")),
                    }
                )
        return result

    def get_alpha_tweets(self, limit: int = 20, token_filter: str = "") -> list[dict[str, Any]]:
        """High-alpha social tweets (alpha-discovery source).

        Backed by ``/deep42/social-data/alpha-tweet-detection``.
        """
        params: dict[str, Any] = {"limit": limit}
        if token_filter:
            params["token_filter"] = token_filter
        rows = self._get_rows("deep42/social-data/alpha-tweet-detection", params)
        result: list[dict[str, Any]] = []
        for item in rows:
            if item.get("tokenSymbol"):
                result.append(
                    {
                        "symbol": str(item.get("tokenSymbol")),
                        "sentiment": _to_float(item.get("sentiment")),
                        "alpha": _to_float(item.get("alpha")),
                        "legitimacy": _to_float(item.get("legitimacy")),
                        "text": str(item.get("text") or "")[:200],
                        "author": str(item.get("twitterHandle") or ""),
                    }
                )
        return result

    def list_tokens(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """Paginated token registry (symbol -> address resolution source).

        Backed by ``/solana/tokens``.
        """
        rows = self._get_rows("solana/tokens", {"limit": limit, "offset": offset})
        return [
            {
                "address": str(data.get("programId") or ""),
                "symbol": str(data.get("symbol") or "").upper(),
                "name": str(data.get("name") or ""),
                "decimals": int(_to_float(data.get("decimals"))),
            }
            for data in rows
        ]

    def resolve_symbol_to_address(
        self,
        symbol: str,
        max_scan: int = 5000,
        page_size: int = 500,
    ) -> str:
        """Resolve a token symbol to its Solana mint address via /solana/tokens.

        The full registry is fetched once and cached in memory for
        ``registry_cache_ttl_hours``; subsequent lookups are instant local
        dict hits with zero API cost.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return ""
        now = time.time()
        ttl_hours = _to_float(getattr(settings, "registry_cache_ttl_hours", 6), 6) or 6
        if self._registry_map is None or now - self._registry_at > ttl_hours * 3600:
            registry: dict[str, str] = {}
            offset = 0
            while len(registry) < max_scan:
                page = self.list_tokens(limit=page_size, offset=offset)
                if not page:
                    break
                for token in page:
                    sym = str(token.get("symbol") or "").upper()
                    if sym and sym not in registry:
                        registry[sym] = token.get("address") or ""
                offset += len(page)
            self._registry_map = registry
            self._registry_at = now
        return self._registry_map.get(symbol, "")

    def get_liquidity_volume(self, pair_or_pool: str) -> dict[str, Any]:
        """Liquidity / volume context for a token (replaces Dexscreener).

        Backed by ``/solana/token-pool-search``, which accepts a token mint
        address and returns the most active pool for it. A fallback to
        ``/solana/orca/pool`` is used when an Orca pool address is supplied
        directly instead of a token address.
        """
        if not pair_or_pool:
            return {}
        rows = self._get_rows("solana/token-pool-search", {"token_address": pair_or_pool, "limit": 1})
        if rows:
            data = rows[0]
            volume = _to_float(data.get("volume24hUSD"))
            divisor = max(settings.cambrian_liquidity_volume_divisor, 0.001)
            return {
                "symbol": f"{data.get('tokenSymbol', '')}/{data.get('poolPairToken', '')}".upper(),
                "pair_address": str(data.get("poolAddress") or ""),
                "dex_id": str(data.get("poolDex") or ""),
                "liquidity": volume / divisor,
                "volume_24h": volume,
                "price_usd": _to_float(data.get("tokenPrice")),
            }
        data = self._get("solana/orca/pool", {"pool_address": pair_or_pool})
        if not data:
            return {}
        return {
            "symbol": f"{data.get('token0Symbol', '')}/{data.get('token1Symbol', '')}".upper(),
            "pair_address": str(data.get("poolAddress") or ""),
            "dex_id": str(data.get("factoryName") or ""),
            "liquidity": _to_float(data.get("tvl")),
            "volume_24h": _to_float(data.get("volume24h")),
            "price_usd": _to_float(data.get("price")),
        }

    def get_social_sentiment(self, token_or_ticker: str) -> dict[str, Any]:
        """Social sentiment signal (replaces SocialScanner).

        Backed by ``/deep42/social-data/token-analysis``, which takes a token
        symbol. ``avgSentiment`` is on a 0-10 scale and is mapped to 0-1.
        """
        if not token_or_ticker:
            return {}
        data = self._get(
            "deep42/social-data/token-analysis",
            {"token_symbol": token_or_ticker.upper(), "days_back": 7},
        )
        if not data:
            return {}
        avg = _to_float(data.get("avgSentiment"), default=5.0)
        score = _clamp(avg / 10.0)
        if score >= 0.6:
            sentiment = "positive"
        elif score <= 0.4:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        return {
            "score": score,
            "sentiment": sentiment,
            "narrative": "",
            "mentions": int(_to_float(data.get("totalTweets"))),
        }

    def get_wallet_activity(self, address: str, include_history: bool = False) -> dict[str, Any]:
        """Wallet-level on-chain activity signals.

        Backed by ``/solana/holder-token-balances``, which returns USD-valued
        SPL token balances for a wallet. ``suspicious`` and ``risk_indicators``
        default to safe since the source reports balances rather than labels.

        When ``include_history`` is set, the largest single-transaction balance
        drop is computed from ``/solana/wallet-balance-history`` and exposed as
        ``balance_drop_pct`` (used by the dev/whale exit monitor).
        """
        if not address:
            return {}
        data = self._get(
            "solana/holder-token-balances",
            {"wallet_address": address, "limit": 10},
        )
        result: dict[str, Any] = {
            "address": str(data.get("tokenAddress") or address),
            "tx_count": int(_to_float(data.get("holderCount"))),
            "unique_tokens": 0,
            "active_days_30": 0,
            "suspicious": False,
            "risk_indicators": [],
        }
        if include_history:
            drop = 0.0
            for row in self.get_wallet_history(address, limit=20):
                before = _to_float(row.get("balance_before"))
                after = _to_float(row.get("balance_after"))
                if before > 0 and after < before:
                    drop = max(drop, (before - after) / before * 100.0)
            result["balance_drop_pct"] = round(drop, 2)
        return result

    def get_risk_score(self, wallet_or_token: str) -> dict[str, Any]:
        """Unified risk score for a token.

        Backed by ``/solana/tokens/security``, which returns a ``securityScore``
        on a 0-100 scale where higher means safer. The score is inverted so a
        higher value here means higher risk (matching ``RiskFilter`` semantics).
        """
        if not wallet_or_token:
            return {}
        data = self._get("solana/tokens/security", {"token_address": wallet_or_token})
        if not data:
            return {}
        security = _to_float(data.get("securityScore"))
        score = _clamp((100.0 - security) / 100.0) if security > 0 else 1.0
        return {
            "score": score,
            "label": "high" if score >= 0.75 else "medium" if score >= 0.4 else "low",
            "flags": [],
            "summary": (
                f"top5 concentration {data.get('top5HolderConcentration')}; "
                f"securityScore {security}"
            ),
        }


_client: CambrianClient | None = None


def get_cambrian_client() -> CambrianClient:
    global _client
    if _client is None:
        _client = CambrianClient()
    return _client


def get_price_trend(token: str) -> dict[str, Any]:
    return get_cambrian_client().get_price_trend(token)


def get_trending_tokens(order_by: str = "volume_usd_24h", limit: int = 10) -> list[dict[str, Any]]:
    return get_cambrian_client().get_trending_tokens(order_by=order_by, limit=limit)


def get_token_details(token_address: str) -> dict[str, Any]:
    return get_cambrian_client().get_token_details(token_address)


def get_liquidity_volume(pair_or_pool: str) -> dict[str, Any]:
    return get_cambrian_client().get_liquidity_volume(pair_or_pool)


def get_social_sentiment(token_or_ticker: str) -> dict[str, Any]:
    return get_cambrian_client().get_social_sentiment(token_or_ticker)


def get_wallet_activity(address: str) -> dict[str, Any]:
    return get_cambrian_client().get_wallet_activity(address)


def get_risk_score(wallet_or_token: str) -> dict[str, Any]:
    return get_cambrian_client().get_risk_score(wallet_or_token)


def get_token_security(token_address: str) -> dict[str, Any]:
    return get_cambrian_client().get_token_security(token_address)


def get_holders(token_address: str, limit: int = 20) -> list[dict[str, Any]]:
    return get_cambrian_client().get_holders(token_address, limit=limit)


def get_mint_burn(token_address: str, limit: int = 20) -> list[dict[str, Any]]:
    return get_cambrian_client().get_mint_burn(token_address, limit=limit)


def get_wallet_history(wallet_address: str, limit: int = 20) -> list[dict[str, Any]]:
    return get_cambrian_client().get_wallet_history(wallet_address, limit=limit)


def get_pool_transactions(pool_address: str, limit: int = 20) -> list[dict[str, Any]]:
    return get_cambrian_client().get_pool_transactions(pool_address, limit=limit)


def get_social_momentum(limit: int = 15, lookback_hours: int = 24) -> list[dict[str, Any]]:
    return get_cambrian_client().get_social_momentum(limit=limit, lookback_hours=lookback_hours)


def get_alpha_tweets(limit: int = 20, token_filter: str = "") -> list[dict[str, Any]]:
    return get_cambrian_client().get_alpha_tweets(limit=limit, token_filter=token_filter)


def list_tokens(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    return get_cambrian_client().list_tokens(limit=limit, offset=offset)


def resolve_symbol_to_address(symbol: str, max_scan: int = 5000, page_size: int = 500) -> str:
    return get_cambrian_client().resolve_symbol_to_address(symbol, max_scan=max_scan, page_size=page_size)
