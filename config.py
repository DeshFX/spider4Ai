"""Central configuration for Spider4AI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during bootstrap
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when runtime configuration is missing or invalid."""


TRUE_VALUES = {"1", "true", "yes", "on"}


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default


def _env_bool(*names: str, default: bool = False) -> bool:
    raw = _env(*names, default="true" if default else "false")
    return raw.strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables with sensible defaults."""

    db_path: str = _env("SPIDER4AI_DB_PATH", default="spider4ai.db")
    env_file: str = _env("SPIDER4AI_ENV_FILE", default=".env")
    coingecko_base_url: str = _env("SPIDER4AI_COINGECKO_URL", default="https://api.coingecko.com/api/v3")
    dexscreener_base_url: str = _env("SPIDER4AI_DEXSCREENER_URL", default="https://api.dexscreener.com")
    cambrian_base_url: str = _env("CAMBRIAN_BASE_URL", default="https://api.cambrian.org")
    cambrian_api_key: str = _env("CAMBRIAN_API_KEY")
    watchlist: str = _env("SPIDER4AI_WATCHLIST", default="BTC,ETH,SOL,PEPE,WIF,BONK")
    ollama_base_url: str = _env("SPIDER4AI_OLLAMA_URL", default="http://localhost:11434")
    ollama_model: str = _env("SPIDER4AI_OLLAMA_MODEL", default="llama3")
    scheduler_minutes: int = int(_env("SPIDER4AI_SCHEDULER_MINUTES", default="10"))
    sepolia_rpc_url: str = _env("SPIDER4AI_SEPOLIA_RPC_URL")
    wallet_private_key: str = _env("SPIDER4AI_WALLET_PRIVATE_KEY")
    default_chain_id: int = int(_env("SPIDER4AI_CHAIN_ID", default="11155111"))
    genlayer_enabled: bool = _env_bool("SPIDER4AI_GENLAYER_ENABLED", "SPIDER4AI_ENABLE_GENLAYER", default=False)
    genlayer_contract_address: str = _env("SPIDER4AI_GENLAYER_CONTRACT_ADDRESS")
    genlayer_timeout_seconds: float = float(_env("SPIDER4AI_GENLAYER_TIMEOUT_SECONDS", default="20"))
    genlayer_max_retries: int = int(_env("SPIDER4AI_GENLAYER_MAX_RETRIES", default="3"))
    min_trade_confidence: float = float(_env("SPIDER4AI_MIN_TRADE_CONFIDENCE", default="0.7"))
    max_validator_disagreement: float = float(_env("SPIDER4AI_MAX_VALIDATOR_DISAGREEMENT", default="0.45"))
    paper_capital_usd: float = float(_env("SPIDER4AI_PAPER_CAPITAL_USD", default="10000"))
    min_position_pct: float = float(_env("SPIDER4AI_MIN_POSITION_PCT", default="0.01"))
    max_position_pct: float = float(_env("SPIDER4AI_MAX_POSITION_PCT", default="0.05"))
    max_trade_size_usd: float = float(_env("SPIDER4AI_MAX_TRADE_SIZE_USD", default="500"))
    max_trade_size_eth: float = float(_env("SPIDER4AI_MAX_TRADE_SIZE_ETH", default="0.001"))
    min_trade_size_eth: float = float(_env("SPIDER4AI_MIN_TRADE_SIZE_ETH", default="0.0002"))
    global_cooldown_seconds: int = int(_env("SPIDER4AI_GLOBAL_COOLDOWN_SECONDS", default="180"))
    token_cooldown_seconds: int = int(_env("SPIDER4AI_TOKEN_COOLDOWN_SECONDS", default="300"))
    take_profit_pct: float = float(_env("SPIDER4AI_TAKE_PROFIT_PCT", default="0.2"))
    stop_loss_pct: float = float(_env("SPIDER4AI_STOP_LOSS_PCT", default="0.07"))
    trailing_stop_pct: float = float(_env("SPIDER4AI_TRAILING_STOP_PCT", default="0.05"))
    circuit_breaker_enabled: bool = _env_bool("SPIDER4AI_CIRCUIT_BREAKER_ENABLED", default=True)
    circuit_breaker_max_losses: int = int(_env("SPIDER4AI_CIRCUIT_BREAKER_MAX_LOSSES", default="3"))
    circuit_breaker_pause_minutes: int = int(_env("SPIDER4AI_CIRCUIT_BREAKER_PAUSE_MINUTES", default="30"))
    dry_run: bool = _env_bool("SPIDER4AI_DRY_RUN", default=True)

    tier_alpha_max_fdv: float = float(_env("SPIDER4AI_TIER_ALPHA_MAX_FDV", default="200000"))
    tier_low_max_fdv: float = float(_env("SPIDER4AI_TIER_LOW_MAX_FDV", default="5000000"))
    tier_mid_max_fdv: float = float(_env("SPIDER4AI_TIER_MID_MAX_FDV", default="100000000"))
    alpha_hunter_enabled: bool = _env_bool("SPIDER4AI_ALPHA_HUNTER_ENABLED", default=False)
    alpha_gate_top10_max_pct: float = float(_env("SPIDER4AI_ALPHA_GATE_TOP10_MAX_PCT", default="30"))
    alpha_gate_min_holders: int = int(_env("SPIDER4AI_ALPHA_GATE_MIN_HOLDERS", default="100"))
    alpha_gate_max_volume_fdv_ratio: float = float(_env("SPIDER4AI_ALPHA_GATE_MAX_VOLUME_FDV_RATIO", default="50"))
    alpha_gate_min_tx_uniqueness: float = float(_env("SPIDER4AI_ALPHA_GATE_MIN_TX_UNIQUENESS", default="0.2"))
    registry_cache_ttl_hours: float = float(_env("SPIDER4AI_REGISTRY_CACHE_TTL_HOURS", default="6"))
    alpha_hunter_limit: int = int(_env("SPIDER4AI_ALPHA_HUNTER_LIMIT", default="10"))
    alpha_min_fdv: float = float(_env("SPIDER4AI_ALPHA_MIN_FDV", default="10000"))
    rugcheck_top_holder_pct: float = float(_env("SPIDER4AI_RUGCHECK_TOP_HOLDER_PCT", default="20"))
    rugcheck_top10_holder_pct: float = float(_env("SPIDER4AI_RUGCHECK_TOP10_HOLDER_PCT", default="50"))
    rugcheck_max_holders: int = int(_env("SPIDER4AI_RUGCHECK_MAX_HOLDERS", default="100"))

    cambrian_monthly_budget: int = int(_env("CAMBRIAN_MONTHLY_BUDGET", default="1000"))
    cambrian_safety_margin: float = float(_env("CAMBRIAN_SAFETY_MARGIN", default="0.9"))
    cambrian_cache_ttl_seconds: float = float(_env("CAMBRIAN_CACHE_TTL_SECONDS", default="300"))
    cambrian_liquidity_volume_divisor: float = float(_env("CAMBRIAN_LIQUIDITY_VOLUME_DIVISOR", default="5"))

    exit_crash_drop_pct: float = float(_env("SPIDER4AI_EXIT_CRASH_DROP_PCT", default="25"))
    exit_crash_window_minutes: int = int(_env("SPIDER4AI_EXIT_CRASH_WINDOW_MINUTES", default="15"))
    exit_dev_whale_sell_pct: float = float(_env("SPIDER4AI_EXIT_DEV_WHALE_SELL_PCT", default="30"))
    exit_take_profit_partial_multiplier: float = float(_env("SPIDER4AI_EXIT_TP_PARTIAL_MULTIPLIER", default="2.0"))
    exit_take_profit_partial_sell_pct: float = float(_env("SPIDER4AI_EXIT_TP_PARTIAL_SELL_PCT", default="50"))

    telegram_bot_token: str = _env("SPIDER4AI_TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _env("SPIDER4AI_TELEGRAM_CHAT_ID")

    def classify_tier(self, fdv: float) -> str:
        """Map an FDV (fully diluted valuation) to a market-cap tier."""
        fdv = max(0.0, float(fdv or 0))
        if fdv <= self.tier_alpha_max_fdv:
            return "alpha"
        if fdv <= self.tier_low_max_fdv:
            return "low"
        if fdv <= self.tier_mid_max_fdv:
            return "mid"
        return "big"

    def validate_startup(self) -> None:
        missing: list[str] = []
        if _env("SPIDER4AI_GENLAYER_ENABLED", "SPIDER4AI_ENABLE_GENLAYER") == "":
            missing.append("SPIDER4AI_GENLAYER_ENABLED")
        if not self.sepolia_rpc_url:
            missing.append("SPIDER4AI_SEPOLIA_RPC_URL")
        if not self.wallet_private_key:
            missing.append("SPIDER4AI_WALLET_PRIVATE_KEY")
        if missing:
            raise ConfigError(
                "Missing required environment variables. Set them in your shell or .env file: "
                + ", ".join(missing)
            )

    def validate_execution(self) -> None:
        missing = []
        if not self.sepolia_rpc_url:
            missing.append("SPIDER4AI_SEPOLIA_RPC_URL")
        if not self.wallet_private_key:
            missing.append("SPIDER4AI_WALLET_PRIVATE_KEY")
        if missing:
            raise ConfigError("Execution requires: " + ", ".join(missing))

    def health_snapshot(self) -> dict[str, str]:
        env_file_exists = str(Path(self.env_file).exists())
        return {
            "env_file": self.env_file,
            "env_file_exists": env_file_exists,
            "rpc_configured": "yes" if bool(self.sepolia_rpc_url) else "no",
            "wallet_configured": "yes" if bool(self.wallet_private_key) else "no",
            "genlayer_enabled": "yes" if self.genlayer_enabled else "no",
            "genlayer_contract_address": self.genlayer_contract_address or "missing",
            "dry_run": "yes" if self.dry_run else "no",
        }


settings = Settings()
