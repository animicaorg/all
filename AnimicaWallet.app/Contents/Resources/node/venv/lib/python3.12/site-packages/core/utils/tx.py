from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from core.encoding.cbor import dumps as cbor_dumps
from core.encoding.cbor import loads as cbor_loads
from core.utils.hash import sha3_256
from core.utils.address import address_to_bytes
from core.utils.address_codec import AccountKeyError, account_key_from_pubkey


class TxNormalizationError(ValueError):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or {}


_log = logging.getLogger(__name__)
_LAST_GASLIMIT_DICT_WARNING_TS = 0.0
_GASLIMIT_DICT_WARNING_INTERVAL_S = 60.0


def _field_error(
    *,
    reason_code: str,
    field: str,
    message: str,
    got_value: Any,
    got_keys: list[str] | None = None,
) -> TxNormalizationError:
    preview = repr(got_value)
    if len(preview) > 120:
        preview = preview[:117] + "..."
    details: dict[str, Any] = {
        "reason_code": reason_code,
        "field": field,
        "received_type": type(got_value).__name__,
        "got_type": type(got_value).__name__,
        "got_value_preview": preview,
    }
    if got_keys is not None:
        details["received_keys"] = got_keys
        details["got_keys"] = got_keys
    return TxNormalizationError(reason_code, message, details=details)


def coerce_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise _field_error(reason_code="bad_field_type", field=name, message=f"{name} must be an integer", got_value=value)
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        if len(value) == 0:
            raise _field_error(reason_code="bad_field_type", field=name, message=f"{name} must be an integer", got_value=value)
        return int.from_bytes(bytes(value), byteorder="big", signed=False)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _field_error(reason_code="bad_field_type", field=name, message=f"{name} must be an integer", got_value=value)
        try:
            return int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except ValueError as exc:
            raise _field_error(reason_code="bad_field_value", field=name, message=f"{name} must be an integer", got_value=value) from exc
    if isinstance(value, Mapping):
        accepted_keys = [k for k in ("value", "amount", "nonce", "n") if k in value]
        if len(accepted_keys) == 1:
            if len(value) != 1:
                raise _field_error(
                    reason_code="bad_field_type",
                    field=name,
                    message=f"{name} dict wrapper must contain exactly one key",
                    got_value=value,
                    got_keys=sorted(str(k) for k in value.keys()),
                )
            return coerce_int(name, value[accepted_keys[0]])
        raise _field_error(
            reason_code="bad_field_type",
            field=name,
            message=f"{name} must be an integer",
            got_value=value,
            got_keys=sorted(str(k) for k in value.keys()),
        )
    raise _field_error(reason_code="bad_field_type", field=name, message=f"{name} must be an integer", got_value=value)


def coerce_hex_int(name: str, value: Any) -> int:
    return coerce_int(name, value)


def coerce_u64(name: str, value: Any) -> int:
    parsed = coerce_int(name, value)
    if parsed < 0 or parsed > ((1 << 64) - 1):
        raise _field_error(reason_code="bad_field_value", field=name, message=f"{name} must fit in u64", got_value=value)
    return parsed


def coerce_u256(name: str, value: Any) -> int:
    parsed = coerce_int(name, value)
    if parsed < 0 or parsed > ((1 << 256) - 1):
        raise _field_error(reason_code="bad_field_value", field=name, message=f"{name} must fit in u256", got_value=value)
    return parsed


def _coerce_int(name: str, value: Any) -> int:
    """Backward-compatible alias for legacy internal call sites."""
    return coerce_int(name, value)


def _decode_hex_str(value: str) -> bytes:
    s = value.strip()
    if s.startswith("0x"):
        s = s[2:]
    if not s:
        raise ValueError("empty hex string")
    if len(s) % 2:
        s = "0" + s
    try:
        return bytes.fromhex(s)
    except ValueError as exc:
        raise ValueError("invalid hex string") from exc


def _normalize_raw_value(raw_val: Any) -> bytes:
    if isinstance(raw_val, (bytes, bytearray)):
        return bytes(raw_val)
    if isinstance(raw_val, str):
        return _decode_hex_str(raw_val)
    raise ValueError(f"unsupported raw type: {type(raw_val).__name__}")


def _extract_hash_bytes(obj: Mapping[str, Any]) -> bytes | None:
    for key in ("hash", "tx_hash", "txid"):
        if key not in obj:
            continue
        val = obj[key]
        if isinstance(val, (bytes, bytearray)):
            if len(val) == 32:
                return bytes(val)
            continue
        if isinstance(val, str):
            try:
                h = _decode_hex_str(val)
            except ValueError:
                continue
            if len(h) == 32:
                return h
    return None


# PQ algorithm pubkey size requirements (from pq/alg_ids.yaml)
_PQ_PUBKEY_SIZES = {
    0x1001: 1952,  # dilithium3
    0x1002: 64,    # sphincs_shake_128s
    4097: 1952,    # dilithium3 (decimal)
    4098: 64,      # sphincs_shake_128s (decimal)
}


def _validate_pubkey_size(alg_id: int, pubkey: bytes) -> None:
    """
    Validate that pubkey size matches algorithm requirements.
    
    Raises TxNormalizationError if the pubkey size is incorrect for the algorithm.
    This prevents address hashes (32 bytes) from being mistaken for actual public keys.
    """
    expected_size = _PQ_PUBKEY_SIZES.get(alg_id)
    if expected_size is None:
        # Unknown algorithm, skip validation (backward compatibility)
        return
    
    actual_size = len(pubkey)
    if actual_size != expected_size:
        # Special case: if we got 32 bytes for an algorithm requiring more,
        # it's likely an address hash was incorrectly used instead of the full pubkey
        if actual_size == 32 and expected_size > 32:
            raise TxNormalizationError(
                "invalid_pubkey_size",
                f"Public key appears to be an address hash (32 bytes) instead of full pubkey. "
                f"Algorithm {hex(alg_id)} requires {expected_size}-byte public keys.",
                details={
                    "alg_id": hex(alg_id),
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                    "hint": "Use the full public key, not the address hash, in signature fields"
                }
            )
        # General size mismatch
        raise TxNormalizationError(
            "invalid_pubkey_size",
            f"Public key size mismatch for algorithm {hex(alg_id)}",
            details={
                "alg_id": hex(alg_id),
                "expected_size": expected_size,
                "actual_size": actual_size,
            }
        )


def _normalize_sig_entry(sig: Any) -> Any:
    if hasattr(sig, "to_obj") and callable(getattr(sig, "to_obj")):
        sig = sig.to_obj()
    if not isinstance(sig, Mapping):
        return sig
    sig_dict = dict(sig)
    alg = sig_dict.get("alg", sig_dict.get("algId", sig_dict.get("alg_id")))
    pubkey = sig_dict.get("pubkey", sig_dict.get("pk", sig_dict.get("pub")))
    sig_bytes = sig_dict.get("sig", sig_dict.get("signature"))

    def _maybe_hex_to_bytes(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            try:
                return _decode_hex_str(value)
            except ValueError:
                return value
        return value

    normalized_pubkey = _maybe_hex_to_bytes(pubkey)
    normalized_sig = _maybe_hex_to_bytes(sig_bytes)
    
    # Validate pubkey size matches algorithm requirements
    if alg is not None and isinstance(normalized_pubkey, (bytes, bytearray)):
        alg_id = None
        if isinstance(alg, int):
            alg_id = alg
        elif isinstance(alg, str) and alg.isdigit():
            alg_id = int(alg)
        elif isinstance(alg, str) and alg.startswith("0x"):
            try:
                alg_id = int(alg, 16)
            except ValueError:
                pass
        
        if alg_id is not None:
            _validate_pubkey_size(alg_id, normalized_pubkey)

    return {
        "alg": alg,
        "pubkey": normalized_pubkey,
        "sig": normalized_sig,
    }


def _normalize_sigs(obj: Mapping[str, Any]) -> list[Any]:
    if "sigs" in obj and isinstance(obj.get("sigs"), list):
        return [_normalize_sig_entry(sig) for sig in obj.get("sigs", [])]
    if "sig" in obj:
        return [_normalize_sig_entry(obj.get("sig"))]
    return []


def _addr_to_bytes(addr: Any) -> bytes | None:
    if addr is None:
        return None
    if isinstance(addr, (bytes, bytearray)):
        return bytes(addr)
    if isinstance(addr, str):
        try:
            return address_to_bytes(addr)
        except Exception:
            try:
                return _decode_hex_str(addr)
            except ValueError:
                return sha3_256(addr.encode("utf-8"))
    return None


def _pad_addr(addr: Any) -> bytes:
    addr_bytes = _addr_to_bytes(addr) or b""
    if len(addr_bytes) < 32:
        return addr_bytes.rjust(32, b"\x00")
    if len(addr_bytes) == 34:
        return addr_bytes[2:34]
    if len(addr_bytes) > 32:
        return addr_bytes[-32:]
    return addr_bytes


def _safe_to_bytes(val: Any) -> bytes:
    """
    Safely convert a value to bytes without raising TypeError.
    
    Handles:
    - None or empty -> b""
    - bytes/bytearray -> bytes
    - hex strings (e.g., "0xabcd") -> bytes
    - UTF-8 strings -> bytes (encoded)
    - list/tuple of valid integers (0-255) -> bytes
    - invalid types -> b"" (defensive fallback)
    
    Returns:
        bytes: The value as bytes, or empty bytes if conversion fails
    """
    if val is None:
        return b""
    if isinstance(val, bytes):
        return val
    if isinstance(val, bytearray):
        return bytes(val)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return b""
        # Handle hex strings
        if val.startswith(("0x", "0X")):
            try:
                return bytes.fromhex(val[2:])
            except (ValueError, TypeError):
                return b""
        # Try to interpret as hex without prefix
        try:
            return bytes.fromhex(val)
        except (ValueError, TypeError):
            # Fall back to UTF-8 encoding for non-hex strings
            try:
                return val.encode("utf-8")
            except (UnicodeEncodeError, AttributeError):
                return b""
    if isinstance(val, (list, tuple)):
        # Attempt to convert list/tuple to bytes
        # Only works if all elements are integers in range 0-255
        try:
            return bytes(val)
        except (TypeError, ValueError):
            # If conversion fails (e.g., non-integer elements), return empty bytes
            return b""
    # For any other type (dict, int, etc.), return empty bytes
    return b""


def normalize_tx_fields(tx: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    tx2 = dict(tx)
    warnings: list[dict[str, str]] = []

    gas_limit = tx2.get("gasLimit")
    if isinstance(gas_limit, Mapping):
        keys = {str(k) for k in gas_limit.keys()}
        if "limit" not in keys:
            raise _field_error(
                reason_code="bad_field_value",
                field="gasLimit",
                message="gasLimit dict must include 'limit'",
                got_value=gas_limit,
                got_keys=sorted(keys),
            )

        limit_int = coerce_int("gasLimit.limit", gas_limit.get("limit"))
        price_int = None
        if "price" in keys:
            price_int = coerce_int("gasLimit.price", gas_limit.get("price"))

        tx2["gasLimit"] = limit_int
        if "gasPrice" not in tx2 and "gas_price" not in tx2 and price_int is not None:
            tx2["gasPrice"] = price_int

        global _LAST_GASLIMIT_DICT_WARNING_TS
        now = time.time()
        if now - _LAST_GASLIMIT_DICT_WARNING_TS >= _GASLIMIT_DICT_WARNING_INTERVAL_S:
            _LAST_GASLIMIT_DICT_WARNING_TS = now
            _log.warning(
                "DEPRECATED_TX_FIELD_SHAPE gasLimit=dict keys=%s",
                sorted(keys),
            )

        warnings.append(
            {
                "code": "deprecated_field_shape",
                "field": "gasLimit",
                "detail": "gasLimit provided as {limit,price} dict; please send gasLimit=int and gasPrice=int",
                "used": "{limit,price} dict",
            }
        )

    return tx2, warnings


def normalize_tx_body(body: Mapping[str, Any]) -> dict:
    body, _ = normalize_tx_fields(body)

    if "v" in body and "gas" in body and "payload" in body:
        return dict(body)

    chain_id = body.get("chainId", body.get("chain_id", 1))
    from_addr = body.get("from", body.get("sender"))
    to_addr = body.get("to")
    nonce = body.get("nonce")
    value = body.get("value", body.get("amount", 0))
    gas_field = body.get("gas")
    gas_limit = body.get("gasLimit", body.get("gas_limit"))
    if gas_limit is None and isinstance(gas_field, Mapping):
        gas_limit = gas_field.get("limit")
    if gas_limit is None:
        gas_limit = 21000

    gas_price = body.get("maxFee", body.get("max_fee"))
    if gas_price is None:
        gas_price = body.get("gasPrice", body.get("gas_price"))
    if gas_price is None and isinstance(gas_field, Mapping):
        gas_price = gas_field.get("price")
    if gas_price is None:
        gas_price = body.get("tip", 1)
    data = _safe_to_bytes(body.get("data", b""))

    valid_after = body.get("validAfter", body.get("valid_after"))
    valid_until = body.get("validUntil", body.get("valid_until"))
    salt_raw = body.get("salt")
    salt = _safe_to_bytes(salt_raw)
    fork_id = body.get("forkId", body.get("fork_id"))
    version = int(body.get("v", 2))
    if version == 2 and (valid_after is None or valid_until is None or salt_raw is None):
        version = 1

    normalized = {
        "v": version,
        "chainId": _coerce_int("chainId", chain_id),
        "from": _pad_addr(from_addr),
        "gas": {
            "price": _coerce_int("maxFee", gas_price),
            "limit": _coerce_int("gasLimit", gas_limit),
        },
        "payload": {
            "t": 0,
            "v": {
                "to": _pad_addr(to_addr),
                "amount": _coerce_int("value", value),
                # data is already bytes from _safe_to_bytes(), no need for bytes() call
                # Previously: bytes(data) could raise TypeError if data was a dict/list of non-ints
                "data": data,
            },
        },
        "accessList": [],
    }
    if version == 1:
        normalized["nonce"] = _coerce_int("nonce", nonce or 0)
    else:
        normalized["validAfter"] = _coerce_int("validAfter", valid_after or 0)
        normalized["validUntil"] = _coerce_int("validUntil", valid_until or 0)
        # salt is already bytes from _safe_to_bytes(), no need for bytes() call
        # Previously: bytes(salt or b"") was redundant and could mask issues
        normalized["salt"] = salt
        if fork_id is not None:
            normalized["forkId"] = _coerce_int("forkId", fork_id)
    return normalized


def normalize_tx_envelope(tx_like: Any) -> dict:
    """
    Normalize a transaction envelope into canonical shape.

    Returns:
      {
        "tx": <canonical tx dict>,
        "sigs": <list>,
        "hash": "0x...",
        "sender": "0x...",
        "nonce": <int>,
        "raw": <bytes>,
      }
    """
    raw_override = None
    if isinstance(tx_like, (bytes, bytearray)):
        raw_override = bytes(tx_like)
    elif isinstance(tx_like, str):
        raw_override = _normalize_raw_value(tx_like)
    elif isinstance(tx_like, dict):
        raw_override = tx_like.get("raw") or tx_like.get("rawTx")
    elif hasattr(tx_like, "to_obj") and callable(getattr(tx_like, "to_obj")):
        tx_like = tx_like.to_obj()
    elif hasattr(tx_like, "to_cbor") and callable(getattr(tx_like, "to_cbor")):
        raw_override = tx_like.to_cbor()

    if raw_override is not None:
        raw_bytes = _normalize_raw_value(raw_override)
        try:
            decoded = cbor_loads(raw_bytes)
        except Exception as exc:
            raise TxNormalizationError(
                "decode_error",
                "invalid raw cbor bytes",
                details={"error": str(exc)},
            ) from exc
        if not isinstance(decoded, dict):
            raise TxNormalizationError(
                "invalid_format",
                "raw cbor did not decode to map",
                details={"type": type(decoded).__name__},
            )
        obj = decoded
    elif isinstance(tx_like, Mapping):
        obj = dict(tx_like)
    else:
        raise TxNormalizationError(
            "invalid_format",
            "unsupported tx envelope type",
            details={"type": type(tx_like).__name__},
        )

    cleaned = {
        k: v
        for k, v in obj.items()
        if k not in ("hash", "raw", "rawTx", "tx_hash", "txid", "sender", "nonce")
    }

    tx_body = None
    if "tx" in cleaned and isinstance(cleaned.get("tx"), Mapping):
        tx_body = cleaned.get("tx")
    elif "body" in cleaned and isinstance(cleaned.get("body"), Mapping):
        tx_body = cleaned.get("body")
    if tx_body is None:
        raise TxNormalizationError(
            "normalize_error",
            "missing tx/body fields",
            details={"decoded_keys": list(cleaned.keys())},
        )

    tx_body, warnings = normalize_tx_fields(tx_body)
    canonical_tx = normalize_tx_body(tx_body)
    sigs = _normalize_sigs(cleaned)
    envelope = {"tx": canonical_tx, "sigs": sigs}
    raw_canonical = cbor_dumps(envelope)
    expected_hash = _extract_hash_bytes(obj)
    computed_hash = sha3_256(raw_canonical)
    if expected_hash is not None and expected_hash != computed_hash:
        raise TxNormalizationError(
            "hash_mismatch",
            "raw hash mismatch",
            details={
                "expected": "0x" + expected_hash.hex(),
                "computed": "0x" + computed_hash.hex(),
            },
        )

    sender_bytes = _addr_to_bytes(canonical_tx.get("from")) if isinstance(canonical_tx, Mapping) else None
    if sender_bytes is None:
        if sigs:
            sig0 = sigs[0]
            alg_id = None
            pubkey = None
            if isinstance(sig0, Mapping):
                alg_id = sig0.get("alg")
                pubkey = sig0.get("pubkey")
            if isinstance(pubkey, str):
                try:
                    pubkey = _decode_hex_str(pubkey)
                except ValueError:
                    pubkey = None
            if isinstance(pubkey, (bytes, bytearray)):
                try:
                    sender_bytes = account_key_from_pubkey(
                        bytes(pubkey), int(alg_id) if alg_id is not None else None
                    )
                except (AccountKeyError, ValueError, TypeError):
                    sender_bytes = None
    if sender_bytes is None:
        raise TxNormalizationError(
            "missing_sender",
            "missing sender",
            details={},
        )

    tx_version = 1
    if isinstance(canonical_tx, Mapping):
        try:
            tx_version = int(canonical_tx.get("v", 1))
        except Exception:
            tx_version = 1

    nonce_int = None
    if tx_version == 1:
        nonce_val = canonical_tx.get("nonce") if isinstance(canonical_tx, Mapping) else None
        try:
            nonce_int = int(nonce_val) if nonce_val is not None else None
        except Exception:
            nonce_int = None
        if nonce_int is None:
            raise TxNormalizationError(
                "missing_nonce",
                "missing nonce",
                details={},
            )

    return {
        "tx": canonical_tx,
        "sigs": sigs,
        "hash": "0x" + computed_hash.hex(),
        # sender_bytes is guaranteed to be non-None here (would have raised TxNormalizationError above)
        # but could be bytes or bytearray from _addr_to_bytes() or account_key_from_pubkey()
        "sender": "0x" + (sender_bytes if isinstance(sender_bytes, bytes) else bytes(sender_bytes)).hex(),
        **({"nonce": nonce_int} if nonce_int is not None else {}),
        **({"warnings": warnings} if warnings else {}),
        "raw": raw_canonical,
    }


def normalize_tx_bytes(tx_like: Any) -> bytes:
    """
    Normalize a transaction-like object to canonical raw CBOR bytes.

    Accepts:
      - bytes/bytearray: returned as-is
      - hex strings (0x...): decoded to bytes
      - dict envelopes with raw/body/sig fields

    Raises:
      ValueError if the input cannot be normalized or hash mismatch detected.
    """
    if isinstance(tx_like, (bytes, bytearray, str, dict)) or hasattr(tx_like, "to_obj") or hasattr(tx_like, "to_cbor"):
        try:
            env = normalize_tx_envelope(tx_like)
        except TxNormalizationError as exc:
            raise ValueError(str(exc)) from exc
        raw = env.get("raw", b"")
        if not raw:
            raise ValueError("raw bytes empty")
        return raw

    raise ValueError(f"unsupported tx type: {type(tx_like).__name__}")


def serialize_tx_canonical(tx_like: Any) -> bytes:
    """
    Canonical transaction serialization used for txid hashing, signatures,
    and P2P payloads.
    """
    return normalize_tx_bytes(tx_like)


def txid(tx_like: Any) -> bytes:
    """Canonical txid: sha3_256 over canonical transaction bytes."""
    return sha3_256(serialize_tx_canonical(tx_like))


def normalize_tx(tx_like: Any) -> bytes:
    """
    Normalize a transaction-like object to canonical raw CBOR bytes.

    Raises TxNormalizationError with a reason suitable for mempool/miner
    rejection when normalization fails.
    """
    try:
        return normalize_tx_bytes(tx_like)
    except ValueError as exc:
        message = str(exc)
        reason = "decode_error"
        if "hash mismatch" in message:
            reason = "hash_mismatch"
        raise TxNormalizationError(reason, message, details={"error": message}) from exc


__all__ = [
    "normalize_tx_bytes",
    "serialize_tx_canonical",
    "txid",
    "normalize_tx",
    "normalize_tx_envelope",
    "normalize_tx_body",
    "normalize_tx_fields",
    "TxNormalizationError",
]
