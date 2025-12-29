from __future__ import annotations

from typing import Any, Mapping

from core.encoding.cbor import dumps as cbor_dumps
from core.utils.hash import sha3_256


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
    if isinstance(tx_like, (bytes, bytearray)):
        raw = bytes(tx_like)
        if not raw:
            raise ValueError("raw bytes empty")
        return raw
    if isinstance(tx_like, str):
        raw = _decode_hex_str(tx_like)
        if not raw:
            raise ValueError("raw bytes empty")
        return raw
    if isinstance(tx_like, dict):
        raw_val = None
        if "raw" in tx_like:
            raw_val = tx_like.get("raw")
        elif "rawTx" in tx_like:
            raw_val = tx_like.get("rawTx")

        if raw_val is not None:
            raw = _normalize_raw_value(raw_val)
        else:
            envelope = None
            if "body" in tx_like and ("sig" in tx_like or "sigs" in tx_like):
                envelope = {"body": tx_like.get("body")}
                if "sig" in tx_like:
                    envelope["sig"] = tx_like.get("sig")
                if "sigs" in tx_like:
                    envelope["sigs"] = tx_like.get("sigs")
            elif "tx" in tx_like and ("sig" in tx_like or "sigs" in tx_like):
                envelope = {"tx": tx_like.get("tx")}
                if "sig" in tx_like:
                    envelope["sig"] = tx_like.get("sig")
                if "sigs" in tx_like:
                    envelope["sigs"] = tx_like.get("sigs")

            if envelope is None:
                raise ValueError("missing raw bytes or envelope fields")

            raw = cbor_dumps(envelope)

        if not raw:
            raise ValueError("raw bytes empty")

        expected_hash = _extract_hash_bytes(tx_like)
        if expected_hash is not None:
            computed = sha3_256(raw)
            if computed != expected_hash:
                raise ValueError(
                    "raw hash mismatch: expected=0x"
                    + expected_hash.hex()
                    + " got=0x"
                    + computed.hex()
                )
        return raw

    raise ValueError(f"unsupported tx type: {type(tx_like).__name__}")


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


__all__ = ["normalize_tx_bytes", "normalize_tx", "TxNormalizationError"]
