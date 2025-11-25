"""
omni_sdk.randomness
===================

High-level client utilities for the on-chain/off-chain randomness beacon:
commit → reveal → VDF → beacon finalize. This package surfaces a single
entrypoint (:class:`RandomnessClient`) that wraps the node's RPC/WS methods
mounted by the randomness service.

Typical usage
-------------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.randomness import RandomnessClient

    rpc = HttpClient("http://127.0.0.1:8545")
    rand = RandomnessClient(rpc)

    # Inspect current round and parameters
    round_info = rand.get_round()

    # Participate in commit–reveal
    salt = bytes.fromhex("00"*32)
    payload = b"my-entropy"
    commit_rec = rand.commit(salt=salt, payload=payload)
    # ... wait for reveal window ...
    reveal_rec = rand.reveal(salt=salt, payload=payload)

    # Read latest beacon output
    beacon = rand.get_beacon()

This package re-exports :class:`RandomnessClient` from :mod:`omni_sdk.randomness.client`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export package version if available
try:  # pragma: no cover
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["RandomnessClient", "__version__"]

# Lazy import pattern keeps import-time side effects minimal and avoids
# circular imports during packaging.
if TYPE_CHECKING:
    from .client import RandomnessClient  # type: ignore
else:
    def __getattr__(name: str):
        if name == "RandomnessClient":
            from .client import RandomnessClient  # type: ignore
            return RandomnessClient
        raise AttributeError(name)
