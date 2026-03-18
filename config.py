"""Central configuration for Spider4AI."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables with sensible defaults."""

    db_path: str = os.getenv("SPIDER4AI_DB_PATH", "spider4ai.db")
    coingecko_base_url: str = os.getenv(
        "SPIDER4AI_COINGECKO_URL", "https://api.coingecko.com/api/v3"
    )
    dexscreener_base_url: str = os.getenv(
        "SPIDER4AI_DEXSCREENER_URL", "https://api.dexscreener.com"
    )
    ollama_base_url: str = os.getenv("SPIDER4AI_OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("SPIDER4AI_OLLAMA_MODEL", "llama3")
    scheduler_minutes: int = int(os.getenv("SPIDER4AI_SCHEDULER_MINUTES", "10"))
    sepolia_rpc_url: str = os.getenv("SPIDER4AI_SEPOLIA_RPC_URL", "")
    wallet_private_key: str = os.getenv("SPIDER4AI_WALLET_PRIVATE_KEY", "")
    default_chain_id: int = int(os.getenv("SPIDER4AI_CHAIN_ID", "11155111"))
    genlayer_enabled: bool = os.getenv("SPIDER4AI_ENABLE_GENLAYER", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    genlayer_contract_address: str = os.getenv("SPIDER4AI_GENLAYER_CONTRACT_ADDRESS", "")
    genlayer_timeout_seconds: float = float(os.getenv("SPIDER4AI_GENLAYER_TIMEOUT_SECONDS", "20"))
    genlayer_fallback_timeout_seconds: float = float(
        os.getenv("SPIDER4AI_GENLAYER_FALLBACK_TIMEOUT_SECONDS", "15")
    )
    genlayer_max_retries: int = int(os.getenv("SPIDER4AI_GENLAYER_MAX_RETRIES", "3"))
    min_trade_confidence: float = float(os.getenv("SPIDER4AI_MIN_TRADE_CONFIDENCE", "0.6"))
    max_validator_disagreement: float = float(os.getenv("SPIDER4AI_MAX_VALIDATOR_DISAGREEMENT", "0.45"))
    paper_capital_usd: float = float(os.getenv("SPIDER4AI_PAPER_CAPITAL_USD", "10000"))
    min_position_pct: float = float(os.getenv("SPIDER4AI_MIN_POSITION_PCT", "0.01"))
    max_position_pct: float = float(os.getenv("SPIDER4AI_MAX_POSITION_PCT", "0.05"))
    global_cooldown_seconds: int = int(os.getenv("SPIDER4AI_GLOBAL_COOLDOWN_SECONDS", "180"))
    token_cooldown_seconds: int = int(os.getenv("SPIDER4AI_TOKEN_COOLDOWN_SECONDS", "300"))
    take_profit_pct: float = float(os.getenv("SPIDER4AI_TAKE_PROFIT_PCT", "0.2"))
    stop_loss_pct: float = float(os.getenv("SPIDER4AI_STOP_LOSS_PCT", "0.07"))
    trailing_stop_pct: float = float(os.getenv("SPIDER4AI_TRAILING_STOP_PCT", "0.05"))


settings = Settings()
