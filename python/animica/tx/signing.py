"""Canonical transaction signing and txid helpers.

This module is the single source of truth for transaction signing preimages.
"""

from __future__ import annotations

import dataclasses as _dc
import re
from typing import Any, Mapping

import cbor2

try:
    from core.encoding.cbor import dumps as _canonical_cbor_dumps
except Exception:  # pragma: no cover
    _canonical_cbor_dumps = None

DOMAIN_TX_SIGN_V1 = "animica.tx.v1"

__all__ = [
    "DOMAIN_TX_SIGN_V1",
    "build_signable_tx_bytes",
    "extract_chain_id",
    "tx_signing_preimage",
    "txid_from_canonical_bytes",
]


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


def _canonical_cbor(obj: Any) -> bytes:
    if _canonical_cbor_dumps is not None:
        return _canonical_cbor_dumps(obj)
    return cbor2.dumps(obj, canonical=True)


def tx_signing_preimage(
    tx: Any,
    *,
    chain_id: int,
    genesis: bytes,
    network: str,
    domain: str = DOMAIN_TX_SIGN_V1,
    message_type: str = "tx",
) -> bytes:
    """Build the canonical, versioned tx signing preimage.

    The preimage intentionally excludes signatures and includes explicit domain
    separators plus chain/genesis bindings.
    """
    body = _extract_body(tx)
    tx_version = int(body.get("v", body.get("version", 1)))
    preimage_obj = {
        1: str(domain),
        2: int(chain_id),
        3: bytes(genesis),
        4: str(network),
        5: str(message_type),
        6: int(tx_version),
        7: body,
    }
    return _canonical_cbor(preimage_obj)


def build_signable_tx_bytes(tx: Any, chain_id: int | None = None) -> bytes:
    """Backward-compatible alias used by existing callers.

    Returns canonical CBOR(tx body), i.e. the payload embedded into
    ``tx_signing_preimage(...)[7]``.
    """
    cid = extract_chain_id(tx)
    if chain_id is not None and int(chain_id) != cid:
        raise ValueError(f"Transaction chain_id mismatch: tx={cid}, override={int(chain_id)}")

    body = _extract_body(tx)
    return _canonical_cbor(body)


def txid_from_canonical_bytes(tx_canonical_bytes: bytes) -> bytes:
    """Consensus txid definition: sha3-256(full canonical signed tx bytes)."""
    import hashlib

    return hashlib.sha3_256(bytes(tx_canonical_bytes)).digest()
