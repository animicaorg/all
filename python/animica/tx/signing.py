"""Shared helpers for Animica transaction signing.

This module exposes a single canonical function,
:func:`build_signable_tx_bytes`, which returns the exact byte string that
post-quantum signers and verifiers should consume. Both the CLI and the
node-side RPC verifier use this helper to guarantee identical CBOR encoding
of the transaction body before domain separation is applied by the PQ layer.
"""

from __future__ import annotations

from omni_sdk.tx.encode import build_signable_tx_bytes

__all__ = ["build_signable_tx_bytes"]

