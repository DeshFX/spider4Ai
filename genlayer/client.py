"""GenLayer client helpers using official GenLayerPY SDK patterns."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

try:
    from genlayer_py import create_client
    from genlayer_py.chains import localnet
except ImportError:  # pragma: no cover - depends on external SDK availability
    create_client = None
    localnet = None


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Create a GenLayer client bound to localnet.

    Official SDK pattern:
        from genlayer_py import create_client
        from genlayer_py.chains import localnet
        client = create_client(chain=localnet)
    """
    if create_client is None or localnet is None:
        raise RuntimeError(
            "genlayer_py is not installed. Install the official GenLayer Python SDK "
            "to enable GenLayer contract calls."
        )
    return create_client(chain=localnet)
