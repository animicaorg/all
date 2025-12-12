"""Canonical transaction signing bytes.

This module defines :func:`build_signable_tx_bytes`, a single source of truth
for constructing the exact byte payload to be post-quantum signed for an
Animica transaction. The function is intentionally self contained and does not
rely on the legacy omni-sdk helpers to avoid accidental drift between CLI and
node implementations.

Encoding rules (v1):
* **Message:** Canonical CBOR representation of the transaction *body* with
  deterministic key ordering (RFC 7049 Canonical CBOR)
* **Chain ID binding:** The chain ID is *validated* (ensuring it exists and
  matches any explicit override) but **not embedded** in the message bytes.
  Chain separation is provided by the PQ signing layer via the ``chain_id``
  parameter passed to ``pq.sign.sign_detached`` / ``pq.verify.verify_detached``.

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


def _canonical_body(tx: Any) -> dict:
    """Build the canonical tx body map without signature metadata.

    Mirrors the SDK's canonical encoding to keep CLI/SDK/node sign-bytes aligned
    even when ``tx`` is a dataclass with extra helper fields.
    """

    def _get(key: str) -> Any:
        if hasattr(tx, key):
            return getattr(tx, key)
        if isinstance(tx, Mapping) and key in tx:
            return tx[key]
        if key == "from":
            if hasattr(tx, "from_addr"):
                return getattr(tx, "from_addr")
            if isinstance(tx, Mapping) and "from_addr" in tx:
                return tx["from_addr"]
        if key == "gasLimit":
            if hasattr(tx, "gas_limit"):
                return getattr(tx, "gas_limit")
            if isinstance(tx, Mapping) and "gas_limit" in tx:
                return tx["gas_limit"]
        if key == "maxFee":
            if hasattr(tx, "max_fee"):
                return getattr(tx, "max_fee")
            if isinstance(tx, Mapping) and "max_fee" in tx:
                return tx["max_fee"]
        if key == "chainId":
            if hasattr(tx, "chain_id"):
                return getattr(tx, "chain_id")
            if isinstance(tx, Mapping) and "chain_id" in tx:
                return tx["chain_id"]
        raise KeyError(key)

    body = {
        "chainId": int(_get("chainId")),
        "from": str(_get("from")),
        "to": _get("to"),
        "nonce": int(_get("nonce")),
        "value": int(_get("value")),
        "gasLimit": int(_get("gasLimit")),
        "maxFee": int(_get("maxFee")),
        "data": bytes(_get("data") or b""),
    }

    if body["to"] in ("", None):
        body["to"] = None
    else:
        body["to"] = str(body["to"])

    return body


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

    # Respect pre-built envelopes when present
    if "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    else:
        # Fall back to canonical body extraction to drop helper/meta fields
        try:
            body = _canonical_body(obj)
        except Exception:
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
            extracted from the transaction body/envelope. If provided, it must
            match the value found in the transaction or a ``ValueError`` is
            raised. The chain ID is validated only; it is **not** embedded in
            the resulting message bytes because domain separation is handled by
            the PQ signing/verification layer.
    """

    cid = extract_chain_id(tx)
    if chain_id is not None and int(chain_id) != cid:
        raise ValueError(
            f"Transaction chain_id mismatch: tx={cid}, override={int(chain_id)}"
        )
    body = _extract_body(tx)

    cbor_payload = cbor2.dumps(body, canonical=True)
    return cbor_payload

