"""Deploy the SpiderTradeDecision contract to GenLayer Bradbury testnet.

Run with Python 3.13 (genlayer-py is installed only there):
    py -3.13 scripts\\deploy_contract.py

Prints the deployment transaction id and waits for consensus.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from genlayer_py import create_account, create_client
from genlayer_py.chains import testnet_bradbury
from genlayer_py.types import ExecutionResult, TransactionStatus

CONTRACT_SOURCE = (
    Path(__file__).resolve().parent.parent / "genlayer" / "contracts_src" / "trade_decision_contract.py"
)


def main() -> None:
    private_key = os.getenv("SPIDER4AI_WALLET_PRIVATE_KEY", "")
    if not private_key:
        print("SPIDER4AI_WALLET_PRIVATE_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    client = create_client(chain=testnet_bradbury)
    account = create_account(private_key)
    print(f"Deployer: {account.address}")
    balance = client.get_balance(account.address)
    print(f"Balance: {balance / 10**18:.4f} GEN")

    code = CONTRACT_SOURCE.read_text(encoding="utf-8")
    print(f"Contract source: {CONTRACT_SOURCE}")
    print("Submitting deployment transaction...")
    tx_id = client.deploy_contract(code=code, account=account, args=[])
    print(f"Deployment transaction id: {tx_id}")

    print("Waiting for consensus...")
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_id,
        status=TransactionStatus.ACCEPTED,
        full_transaction=True,
    )
    exec_result = receipt.get("tx_execution_result_name")
    print(f"Execution result: {exec_result}")
    if exec_result == ExecutionResult.FINISHED_WITH_ERROR.value:
        print("Deployment failed during execution.", file=sys.stderr)
        sys.exit(1)

    contract_address = receipt.get("recipient") or receipt.get("to_address")
    print("Deployment transaction finalized.")
    print(f"Contract address: {contract_address}")
    print("Set SPIDER4AI_GENLAYER_CONTRACT_ADDRESS to this address in .env and .env.txt.")


if __name__ == "__main__":
    main()
