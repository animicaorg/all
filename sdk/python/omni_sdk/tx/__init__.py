"""
omni_sdk.tx
===========

High-level transaction helpers for Animica: build, encode, and send.

Submodules
----------
- build : Builders for transfer / call / deploy transactions and gas-estimation helpers.
- encode: Canonical SignBytes preparation and CBOR (de)serialization helpers.
- send  : JSON-RPC integration for submitting raw CBOR transactions and awaiting receipts.

Typical usage
-------------
    from omni_sdk.tx import build, encode, send

    # 1) Build a transaction (function names live in `build`)
    # tx = build.transfer(from_addr=..., to_addr=..., amount=..., nonce=..., gas_price=..., gas_limit=...)
    # or:
    # tx = build.deploy(manifest=..., code=..., sender=..., ...)

    # 2) Prepare sign-bytes (function names live in `encode`)
    # signbytes = encode.sign_bytes(tx, chain_id=...)

    # 3) Sign using PQ signer (see omni_sdk.wallet.signer.PQSigner)
    # sig = signer.sign(signbytes, domain=None)

    # 4) Produce raw, signed CBOR blob for submission
    # raw_tx = encode.pack_signed(tx, signature=sig, alg_id=signer.alg_id, public_key=signer.public_key)

    # 5) Submit and wait for a receipt (helpers live in `send`)
    # receipt = send.submit_and_wait(rpc_client, raw_tx, timeout_s=30)

Notes
-----
This package intentionally re-exports its submodules without pinning exact function
names to remain compatible with the SDK's evolving internals. Import from the
specific submodule for concrete APIs.
"""

from __future__ import annotations

# Re-export stable submodule namespaces
from . import build as build
from . import encode as encode
from . import send as send

__all__ = ["build", "encode", "send"]


class TxAPI:
    """
    Optional ergonomic accessor that groups the tx helpers under a single object.

    Example:
        from omni_sdk.tx import tx
        raw = tx.encode.pack_signed(...)
        receipt = tx.send.submit_and_wait(rpc, raw)
    """

    @property
    def build(self):
        return build

    @property
    def encode(self):
        return encode

    @property
    def send(self):
        return send


# Singleton namespace for convenience
tx = TxAPI()
