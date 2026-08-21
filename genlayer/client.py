"""GenLayer client helpers using official GenLayerPY SDK patterns."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

try:
    from genlayer_py import create_account, create_client
    from genlayer_py.chains import localnet, testnet_bradbury
except ImportError:  # pragma: no cover - depends on external SDK availability
    create_account = None
    create_client = None
    localnet = None
    testnet_bradbury = None


def _wallet_account() -> Any:
    """Build the funded EVM account from SPIDER4AI_WALLET_PRIVATE_KEY."""
    from config import settings

    if create_account is None:
        raise RuntimeError(
            "genlayer_py is not installed. Install the official GenLayer Python SDK "
            "to enable GenLayer contract calls."
        )
    if not settings.wallet_private_key:
        raise RuntimeError("SPIDER4AI_WALLET_PRIVATE_KEY is not configured for GenLayer.")
    return create_account(settings.wallet_private_key)


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Create a GenLayer client bound to the Bradbury testnet.

    Official SDK pattern:
        from genlayer_py import create_client
        from genlayer_py.chains import testnet_bradbury
        client = create_client(chain=testnet_bradbury)
    """
    if create_client is None or testnet_bradbury is None:
        raise RuntimeError(
            "genlayer_py is not installed. Install the official GenLayer Python SDK "
            "to enable GenLayer contract calls."
        )
    return create_client(chain=testnet_bradbury, account=_wallet_account())
