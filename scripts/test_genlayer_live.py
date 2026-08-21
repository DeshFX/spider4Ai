"""Live test of Spider4AI's GenLayer evaluate_trade on Bradbury testnet.

Run with Python 3.13:
    py -3.13 scripts\\test_genlayer_live.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from genlayer_py import create_account, create_client
from genlayer_py.chains import testnet_bradbury
from genlayer_py.types import ExecutionResult, TransactionStatus

from genlayer.service import GenLayerContract, _read_contract_text

CONTRACT_ADDRESS = os.getenv("SPIDER4AI_GENLAYER_CONTRACT_ADDRESS", "").strip()

SAMPLE_PAYLOAD = {
    "coin_id": "live-test",
    "token": "SPIDR",
    "symbol": "SPIDR",
    "summary": "Trending Solana meme token with strong momentum and rising volume.",
    "signal_strength": 0.78,
    "risk_flags": [],
    "market_context": "High volatility market, meme sector pumping.",
    "source": "cambrian_live_test",
    "recent_trend": "up 5% in last hour, volume 2x average",
    "price": 0.0001234,
    "market_cap": 1500000.0,
    "volume_24h": 320000.0,
    "liquidity": 95000.0,
    "narrative": "AI meme coin",
    "accumulation_score": 0.61,
    "market_stability": 0.55,
    "onchain_context": "No recent minting. Top 10 holders control 18%. Liquidity locked.",
    "tier": "low",
    "reason": "Live GenLayer test",
}


def main() -> None:
    private_key = os.getenv("SPIDER4AI_WALLET_PRIVATE_KEY", "")
    if not private_key:
        print("SPIDER4AI_WALLET_PRIVATE_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    if not CONTRACT_ADDRESS:
        print("SPIDER4AI_GENLAYER_CONTRACT_ADDRESS is not set.", file=sys.stderr)
        sys.exit(1)

    client = create_client(
        chain=testnet_bradbury,
        account=create_account(private_key),
    )
    contract = GenLayerContract(client=client, address=CONTRACT_ADDRESS, account=client.local_account)

    print(f"Contract: {CONTRACT_ADDRESS}")
    print("Submitting evaluate_trade (3 LLM validator roles vote via consensus)...")
    result = contract.evaluate_trade(SAMPLE_PAYLOAD, timeout_seconds=180)
    print("evaluate_trade result:")
    print(json.dumps(result.get("decision"), indent=2, default=str))
    print("tx_hash:", result.get("transaction_hash"))
    print("tx_execution_result_name:", result.get("receipt", {}).get("tx_execution_result_name"))

    metrics = _read_contract_text(client, CONTRACT_ADDRESS, "get_metrics", client.local_account)
    print("metrics:", metrics)
    history = _read_contract_text(client, CONTRACT_ADDRESS, "get_decision_history", client.local_account)
    print("history:", history)


if __name__ == "__main__":
    main()
