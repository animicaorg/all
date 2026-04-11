"""
Transaction encoding helpers for the Python SDK.

This module builds the canonical transaction body dict and packs signed
transactions into the wire format expected by Animica nodes.
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from omni_sdk.utils.cbor import dumps as cbor_dumps  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.utils.cbor is required for deterministic encoding"
    ) from _e


TxLike = Any


def _get(tx: TxLike, key: str) -> Any:
    if isinstance(tx, dict):
        if key in tx:
            return tx[key]
        raise KeyError(f"Tx field '{key}' not found")
    if hasattr(tx, key):
        return getattr(tx, key)
    raise KeyError(f"Tx field '{key}' not found")


def _get_optional(tx: TxLike, *keys: str) -> Any:
    for key in keys:
        if isinstance(tx, dict):
            if key in tx:
                return tx[key]
        else:
            if hasattr(tx, key):
                return getattr(tx, key)
    return None


def _normalize_int(value: Any, *, field: str) -> int:
    if value is None:
        raise KeyError(f"Tx field '{field}' not found")
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        return int(s)
    return int(value)


def _normalize_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("0x", "0X")):
            raw = raw[2:]
        if raw == "":
            return b""
        if len(raw) % 2:
            raw = "0" + raw
        try:
            return bytes.fromhex(raw)
        except Exception:
            return value.encode("utf-8")
    return bytes(value)


def _normalize_account_key(value: Any, *, field: str) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        out = bytes(value)
        if len(out) != 32:
            raise ValueError(f"{field} must be 32 bytes, got {len(out)}")
        return out
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith(("0x", "0X")):
            out = bytes.fromhex(raw[2:])
            if len(out) != 32:
                raise ValueError(f"{field} must be 32-byte hex, got {len(out)} bytes")
            return out
        try:
            from omni_sdk.address import parse as parse_address  # type: ignore

            parsed = parse_address(raw)
            pub_hash = parsed.get("pubkey_hash")
            if isinstance(pub_hash, (bytes, bytearray, memoryview)):
                out = bytes(pub_hash)
                if len(out) == 32:
                    return out
        except Exception as exc:
            raise ValueError(f"{field} must be a valid address or 0x-hex key: {exc}") from exc
    raise TypeError(f"{field} must be 32-byte bytes, address, or 0x-hex")


def _canonical_body_from_typed_tx(tx: TxLike) -> Dict[str, Any] | None:
    if not isinstance(tx, dict):
        return None
    if not {"v", "gas", "payload"}.issubset(tx.keys()):
        return None

    gas = tx.get("gas")
    payload = tx.get("payload")
    if not isinstance(gas, dict) or not isinstance(payload, dict):
        raise TypeError("typed tx requires 'gas' and 'payload' maps")

    version = _normalize_int(tx.get("v"), field="v")
    out: Dict[str, Any] = {
        "v": version,
        "chainId": _normalize_int(tx.get("chainId"), field="chainId"),
        "from": _normalize_account_key(tx.get("from"), field="from"),
        "gas": {
            "price": _normalize_int(gas.get("price"), field="gas.price"),
            "limit": _normalize_int(gas.get("limit"), field="gas.limit"),
        },
        "accessList": [],
    }

    payload_tag = _normalize_int(payload.get("t"), field="payload.t")
    payload_val = payload.get("v")
    if not isinstance(payload_val, dict):
        raise TypeError("typed tx payload.v must be a map")

    if payload_tag == 0:
        out["payload"] = {
            "t": 0,
            "v": {
                "to": _normalize_account_key(payload_val.get("to"), field="payload.v.to"),
                "amount": _normalize_int(payload_val.get("amount", 0), field="payload.v.amount"),
                "data": _normalize_bytes(payload_val.get("data", b"")),
            },
        }
    elif payload_tag == 1:
        out["payload"] = {
            "t": 1,
            "v": {
                "code": _normalize_bytes(payload_val.get("code", b"")),
                "manifest": _normalize_bytes(payload_val.get("manifest", b"")),
            },
        }
    elif payload_tag == 2:
        out["payload"] = {
            "t": 2,
            "v": {
                "to": _normalize_account_key(payload_val.get("to"), field="payload.v.to"),
                "data": _normalize_bytes(payload_val.get("data", b"")),
            },
        }
    elif payload_tag == 3:
        out["payload"] = {
            "t": 3,
            "v": {
                "to": _normalize_account_key(payload_val.get("to"), field="payload.v.to"),
                "amount": _normalize_int(payload_val.get("amount", 0), field="payload.v.amount"),
                "data": _normalize_bytes(payload_val.get("data", b"")),
            },
        }
    else:
        raise ValueError(f"unsupported typed payload tag: {payload_tag}")

    access_list = tx.get("accessList", [])
    if access_list is None:
        access_list = []
    if not isinstance(access_list, list):
        raise TypeError("accessList must be a list")
    for idx, entry in enumerate(access_list):
        if not isinstance(entry, dict):
            raise TypeError(f"accessList[{idx}] must be a map")
        keys = entry.get("keys", [])
        if keys is None:
            keys = []
        if not isinstance(keys, list):
            raise TypeError(f"accessList[{idx}].keys must be a list")
        out["accessList"].append(
            {
                "addr": _normalize_account_key(entry.get("addr"), field=f"accessList[{idx}].addr"),
                "keys": [_normalize_bytes(k) for k in keys],
            }
        )

    if version == 1:
        out["nonce"] = _normalize_int(tx.get("nonce"), field="nonce")
    else:
        out["validAfter"] = _normalize_int(tx.get("validAfter"), field="validAfter")
        out["validUntil"] = _normalize_int(tx.get("validUntil"), field="validUntil")
        out["salt"] = _normalize_bytes(tx.get("salt"))
        fork_id = tx.get("forkId")
        if fork_id is not None:
            out["forkId"] = _normalize_int(fork_id, field="forkId")

    return out


def canonical_body_dict(tx: TxLike) -> Dict[str, Any]:
    typed_body = _canonical_body_from_typed_tx(tx)
    if typed_body is not None:
        return typed_body

    body: Dict[str, Any] = {}

    from_addr = _get_optional(tx, "from_addr", "from", "sender")
    to_addr = _get_optional(tx, "to", "to_addr", "recipient")
    value = _get_optional(tx, "value")
    data = _get_optional(tx, "data", "payload")
    gas_limit = _get_optional(tx, "gas_limit", "gasLimit", "gas")
    max_fee = _get_optional(tx, "max_fee", "maxFee")
    chain_id = _get_optional(tx, "chain_id", "chainId")
    nonce = _get_optional(tx, "nonce")
    valid_after = _get_optional(tx, "valid_after", "validAfter")
    valid_until = _get_optional(tx, "valid_until", "validUntil")
    salt = _get_optional(tx, "salt")

    if from_addr is not None:
        body["from"] = str(from_addr)
    if to_addr is not None:
        body["to"] = str(to_addr)
    if value is not None:
        body["value"] = _normalize_int(value, field="value")
    if data is not None:
        body["data"] = _normalize_bytes(data)
    if gas_limit is not None:
        body["gasLimit"] = _normalize_int(gas_limit, field="gas_limit")
    if max_fee is not None:
        body["maxFee"] = _normalize_int(max_fee, field="max_fee")
    if chain_id is not None:
        body["chainId"] = _normalize_int(chain_id, field="chain_id")

    # v2 tx semantics:
    # if validity window is present, omit nonce and require salt
    is_v2 = (valid_after is not None) or (valid_until is not None) or (salt is not None)

    if is_v2:
        if valid_after is not None:
            body["validAfter"] = _normalize_int(valid_after, field="valid_after")
        if valid_until is not None:
            body["validUntil"] = _normalize_int(valid_until, field="valid_until")
        if salt is not None:
            body["salt"] = _normalize_bytes(salt)
    else:
        if nonce is not None:
            body["nonce"] = _normalize_int(nonce, field="nonce")

    return body


def sign_bytes(tx: TxLike) -> bytes:
    """
    Canonical CBOR of the unsigned tx body.
    """
    return cbor_dumps(canonical_body_dict(tx))


def pack_signed(
    tx: TxLike,
    *,
    signature: bytes,
    alg_id: int,
    public_key: bytes,
) -> bytes:
    """
    Pack a signed tx envelope in canonical CBOR form.

    Node expects a top-level 'sig' object.
    """
    env = {
        "body": canonical_body_dict(tx),
        "sig": {
            "algId": int(alg_id),
            "pubkey": _normalize_bytes(public_key),
            "sig": _normalize_bytes(signature),
        },
    }
    return cbor_dumps(env)


__all__ = [
    "canonical_body_dict",
    "sign_bytes",
    "pack_signed",
]
