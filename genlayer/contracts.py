"""Contract deployment and lookup abstractions for GenLayer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import settings
from genlayer.client import get_client

CONTRACT_SOURCE = Path(__file__).resolve().parent / "contracts_src" / "trade_decision_contract.py"


def deploy_contract(
    contract_path: str | Path | None = None,
    constructor_args: list[Any] | None = None,
) -> dict[str, Any]:
    """Return the intelligent-contract source path and deployment metadata.

    GenLayerPY currently documents client creation plus contract read/write operations.
    Spider4AI keeps deployment concerns explicit and separate so the returned contract
    source can be deployed through GenLayer Studio, CLI, or a dedicated deployment script.
    """
    path = Path(contract_path) if contract_path else CONTRACT_SOURCE
    return {
        "contract_path": str(path),
        "constructor_args": constructor_args or [],
        "chain": "localnet",
        "status": "ready_for_deploy",
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

    return GenLayerContract(client=get_client(), address=contract_address)
