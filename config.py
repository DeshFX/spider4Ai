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


settings = Settings()
