"""
omni_sdk.light_client
=====================

Thin helpers for verifying chain headers and DA (Data Availability) light proofs
off-node. This package exposes a small surface area suitable for wallets, SDKs,
and tooling that need to validate minimal trust data fetched from RPC/HTTP.

You typically won't import submodules directly; instead use the re-exports below.

Quick start
-----------
    from omni_sdk.light_client import LightClient, verify_light_proof

    lc = LightClient(trust_anchor=genesis_header_obj)
    ok = lc.verify_header(new_header_obj)           # header linkage & basic checks
    ok_da = verify_light_proof(header_obj, proof)   # DA root/light-proof check

The concrete shapes expected by these functions are documented in
:mod:`omni_sdk.light_client.verify`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export package version if available
try:  # pragma: no cover
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["LightClient", "verify_light_proof", "__version__"]

# Lazy imports keep import-time side effects minimal and avoid circulars during packaging.
if TYPE_CHECKING:
    from .verify import LightClient, verify_light_proof  # type: ignore
else:

    def __getattr__(name: str):
        if name in ("LightClient", "verify_light_proof"):
            from . import verify as _verify  # type: ignore

            return getattr(_verify, name)
        raise AttributeError(name)
