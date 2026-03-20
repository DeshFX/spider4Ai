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
    ollama_base_url: str = _env("SPIDER4AI_OLLAMA_URL", default="http://localhost:11434")
    ollama_model: str = _env("SPIDER4AI_OLLAMA_MODEL", default="llama3")
    scheduler_minutes: int = int(_env("SPIDER4AI_SCHEDULER_MINUTES", default="10"))
    sepolia_rpc_url: str = _env("SPIDER4AI_SEPOLIA_RPC_URL")
    wallet_private_key: str = _env("SPIDER4AI_WALLET_PRIVATE_KEY")
    default_chain_id: int = int(_env("SPIDER4AI_CHAIN_ID", default="11155111"))
    genlayer_enabled: bool = _env_bool("SPIDER4AI_GENLAYER_ENABLED", "SPIDER4AI_ENABLE_GENLAYER", default=False)
    genlayer_contract_address: str = _env("SPIDER4AI_GENLAYER_CONTRACT_ADDRESS")
    genlayer_timeout_seconds: float = float(_env("SPIDER4AI_GENLAYER_TIMEOUT_SECONDS", default="20"))
    genlayer_fallback_timeout_seconds: float = float(_env("SPIDER4AI_GENLAYER_FALLBACK_TIMEOUT_SECONDS", default="15"))
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
    dry_run: bool = _env_bool("SPIDER4AI_DRY_RUN", default=True)

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
