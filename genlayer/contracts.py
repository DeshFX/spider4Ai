"""Contract deployment and lookup abstractions for GenLayer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import settings
from genlayer.client import get_client, _wallet_account

CONTRACT_SOURCE = Path(__file__).resolve().parent / "contracts_src" / "trade_decision_contract.py"


def deploy_contract(
    contract_path: str | Path | None = None,
    constructor_args: list[Any] | None = None,
) -> dict[str, Any]:
    """Deploy the intelligent contract to the Bradbury testnet.

    Returns deployment metadata including the deployed contract address.
    """
    path = Path(contract_path) if contract_path else CONTRACT_SOURCE
    client = get_client()
    account = _wallet_account()
    address = client.deploy_contract(
        code=path.read_text(encoding="utf-8"),
        account=account,
        args=constructor_args or [],
    )
    return {
        "contract_path": str(path),
        "constructor_args": constructor_args or [],
        "chain": "testnet_bradbury",
        "contract_address": address,
        "status": "deployed",
    }


def get_contract_at(address: str | None = None) -> "GenLayerContract":
    """Build a GenLayer contract adapter for an already deployed contract."""
    contract_address = address or settings.genlayer_contract_address
    if not contract_address:
        raise ValueError(
            "SPIDER4AI_GENLAYER_CONTRACT_ADDRESS is not configured. "
            "Deploy the intelligent contract and set its address before enabling GenLayer."
        )

    from genlayer.service import GenLayerContract

    return GenLayerContract(client=get_client(), address=contract_address, account=_wallet_account())
