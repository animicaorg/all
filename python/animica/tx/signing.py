"""Canonical transaction signing bytes.

This module defines :func:`build_signable_tx_bytes`, a single source of truth
for constructing the exact byte payload to be post-quantum signed for an
Animica transaction. The function is intentionally self contained and does not
rely on the legacy omni-sdk helpers to avoid accidental drift between CLI and
node implementations.

Encoding rules (v1):
* Domain separation prefix: ``b"animica:tx:v1"``
* Chain ID: 4-byte big-endian unsigned integer (required)
* Canonical CBOR representation of the transaction *body* with deterministic
  key ordering (RFC 7049 Canonical CBOR)

The transaction input may be:
* A mapping that already represents the body
* An envelope that contains a ``body`` key plus ``sig``/``sigs`` entries; the
  signature metadata is stripped before encoding
* A dataclass with a ``body`` attribute
"""

from __future__ import annotations

import dataclasses as _dc
from typing import Any, Mapping

import cbor2

PREFIX = b"animica:tx:v1"

__all__ = ["build_signable_tx_bytes", "extract_chain_id"]


def _as_dict(obj: Any) -> dict:
    """Dataclass → dict (deep copy) helper."""

    if _dc.is_dataclass(obj):
        return {k: _as_dict(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, Mapping):
        return {k: _as_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_dict(x) for x in obj]
    return obj


def extract_chain_id(tx: Any) -> int:
    """Extract ``chainId``/``chain_id`` from common envelope shapes.

    Raises ``ValueError`` if not present.
    """

    obj = _as_dict(tx)

    # Check nested body first (authoritative)
    if "body" in obj and isinstance(obj["body"], Mapping):
        body = obj["body"]
        cid = body.get("chainId") or body.get("chain_id")
        if cid is not None:
            return int(cid)

    # Flat lookups
    cid = obj.get("chainId") or obj.get("chain_id")
    if cid is not None:
        return int(cid)

    raise ValueError("Transaction missing chain_id/chainId")


def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)
    if "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    else:
        body = dict(obj)

    # Remove signature metadata to avoid signing it
    for k in ("sig", "signature", "sigs"):
        body.pop(k, None)
    return body


def build_signable_tx_bytes(tx: Any, chain_id: int | None = None) -> bytes:
    """Build the deterministic signable bytes for a transaction.

    Args:
        tx: Transaction body or envelope (dict/dataclass).
        chain_id: Optional explicit chain ID; when omitted the value is
            extracted from the transaction body/envelope.
    """

    cid = int(chain_id if chain_id is not None else extract_chain_id(tx))
    body = _extract_body(tx)

    cbor_payload = cbor2.dumps(body, canonical=True)
    return PREFIX + cid.to_bytes(4, "big") + cbor_payload

