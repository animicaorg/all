"""
Canonical transaction signing bytes.

This module defines `build_signable_tx_bytes`, a single source of truth for constructing
the exact byte payload that is signed for an Animica transaction.

Important:
- The payload for TX signing is the deterministic CBOR of the transaction *body*.
- Domain separation / replay protection is handled by the PQ signing layer, which
  prefixes the payload with the chain-bound DomainTag per spec/domains.yaml.
"""

from __future__ import annotations

import dataclasses as _dc
import re
from typing import Any, Mapping

import cbor2

__all__ = ["build_signable_tx_bytes", "extract_chain_id"]


_HEX_RE = re.compile(r"^[0-9a-fA-F]*$")


def _as_dict(obj: Any) -> Any:
    """Dataclass → nested dict/list (deep) helper."""
    if _dc.is_dataclass(obj):
        return {k: _as_dict(v) for k, v in _dc.asdict(obj).items()}
    if isinstance(obj, Mapping):
        return {k: _as_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_dict(x) for x in obj]
    return obj


def _coerce_data_to_bytes(v: Any) -> bytes:
    """
    Coerce tx `data` into bytes, accepting common forms:
    - None / "" -> b""
    - bytes / bytearray / memoryview -> bytes(...)
    - hex string "0x..." or bare hex -> bytes.fromhex(...)
    - list[int]/tuple[int] -> bytes(list)
    - str (non-hex) -> UTF-8 bytes (last-resort)
    """
    if v is None or v == "":
        return b""

    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)

    if isinstance(v, (list, tuple)):
        # assume sequence of ints
        return bytes(v)

    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() == "0x":
            return b""
        if s.startswith(("0x", "0X")):
            h = s[2:]
            if len(h) % 2 == 1:
                h = "0" + h
            if not _HEX_RE.match(h):
                raise ValueError(f"tx.data looks like hex but contains non-hex chars: {v!r}")
            return bytes.fromhex(h)
        # bare hex?
        if _HEX_RE.match(s) and len(s) >= 2:
            h = s
            if len(h) % 2 == 1:
                h = "0" + h
            return bytes.fromhex(h)
        # last-resort: treat as UTF-8
        return s.encode("utf-8")

    raise TypeError(f"Unsupported tx.data type: {type(v).__name__}")


def _canonical_body(tx: Any) -> dict:
    """
    Build the canonical tx body map without signature metadata.
    Mirrors common field aliases and ensures deterministic types.
    """

    def _get(key: str) -> Any:
        if hasattr(tx, key):
            return getattr(tx, key)
        if isinstance(tx, Mapping) and key in tx:
            return tx[key]

        # aliases
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
        "data": _coerce_data_to_bytes(_get("data") if ("data" in tx if isinstance(tx, Mapping) else hasattr(tx, "data")) else None),
    }

    # normalize "to"
    if body["to"] in ("", None):
        body["to"] = None
    else:
        body["to"] = str(body["to"])

    return body


def extract_chain_id(tx: Any) -> int:
    """
    Extract chainId/chain_id from common envelope shapes.
    Raises ValueError if not present.
    """
    obj = _as_dict(tx)

    # Nested body is authoritative
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        cid = obj["body"].get("chainId") or obj["body"].get("chain_id")
        if cid is not None:
            return int(cid)

    if isinstance(obj, Mapping):
        cid = obj.get("chainId") or obj.get("chain_id")
        if cid is not None:
            return int(cid)

    raise ValueError("Transaction missing chain_id/chainId")


def _extract_body(tx: Any) -> dict:
    obj = _as_dict(tx)

    # If it's already an envelope, respect it
    if isinstance(obj, Mapping) and "body" in obj and isinstance(obj["body"], Mapping):
        body = dict(obj["body"])
    else:
        # fall back to canonical extraction
        try:
            body = _canonical_body(obj)
        except Exception:
            body = dict(obj) if isinstance(obj, Mapping) else {}

    # Remove signature metadata
    for k in ("sig", "signature", "sigs"):
        if k in body:
            body.pop(k, None)

    # Ensure data is bytes if present
    if "data" in body:
        body["data"] = _coerce_data_to_bytes(body["data"])

    return body


def build_signable_tx_bytes(tx: Any, chain_id: int | None = None) -> bytes:
    """
    Build the deterministic signable bytes for a transaction: canonical CBOR(body).

    Args:
      tx: Transaction body or envelope (dict/dataclass).
      chain_id: Optional explicit chain ID; when provided, must match tx's chainId.
    """
    cid = extract_chain_id(tx)
    if chain_id is not None and int(chain_id) != cid:
        raise ValueError(f"Transaction chain_id mismatch: tx={cid}, override={int(chain_id)}")

    body = _extract_body(tx)
    return cbor2.dumps(body, canonical=True)
