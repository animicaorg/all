"""Transaction builder for Animica Studio.

Builds canonical transaction dicts that match the Animica CBOR schema,
encodes them to hex, and provides fee estimation.

Note: This is a *watch-only / send-ready* builder.  It produces the unsigned
tx body for signing and the final encoding for submission.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Default fee / gas parameters (overridden by RPC if available)
_DEFAULT_GAS_LIMIT = 21_000
_DEFAULT_GAS_PRICE_WEI = 10 ** 9  # 1 Gwei equivalent
_DEFAULT_FEE_WEI = _DEFAULT_GAS_LIMIT * _DEFAULT_GAS_PRICE_WEI  # 21_000 * 1e9

TX_VERSION = 1
TX_KIND_TRANSFER = "transfer"


def build_transfer_tx(
    *,
    chain_id: int,
    from_addr: str,
    to_addr: str,
    value_wei: int,
    nonce: int,
    gas_limit: int = _DEFAULT_GAS_LIMIT,
    gas_price_wei: int = _DEFAULT_GAS_PRICE_WEI,
    memo: str | None = None,
    data: bytes | None = None,
) -> dict[str, Any]:
    """Build an unsigned transfer transaction dict.

    Returns a dict with ``body`` and ``sigs`` (empty list until signed).

    Parameters
    ----------
    chain_id:
        The target chain's integer ID.
    from_addr:
        Sender's bech32m address.
    to_addr:
        Recipient's bech32m address.
    value_wei:
        Transfer amount in wei (raw integer).
    nonce:
        Sender's current pending nonce.
    gas_limit:
        Maximum gas units for the transaction.
    gas_price_wei:
        Gas price in wei per gas unit.
    memo:
        Optional human-readable memo string.
    data:
        Optional binary payload (bytes); hex-encoded in the body.

    Raises
    ------
    ValueError
        If required fields fail validation.
    """
    tx = {
        "body": {
            "version": TX_VERSION,
            "kind": TX_KIND_TRANSFER,
            "chain_id": int(chain_id),
            "from": str(from_addr),
            "to": str(to_addr),
            "value": int(value_wei),
            "nonce": int(nonce),
            "gas_limit": int(gas_limit),
            "gas_price": int(gas_price_wei),
            "fee": int(gas_limit) * int(gas_price_wei),
            "memo": memo,
            "data": ("0x" + data.hex()) if data else None,
        },
        "sigs": [],
    }
    validate_tx_dict(tx)
    return tx


def validate_tx_dict(tx: dict[str, Any]) -> None:
    """Validate a transaction dict.

    Raises
    ------
    ValueError
        With a clear message if any required field is missing or invalid.
    """
    body = tx.get("body")
    if not isinstance(body, dict):
        raise ValueError("Transaction must have a 'body' dict")

    required = {
        "version": int,
        "kind": str,
        "chain_id": int,
        "from": str,
        "to": str,
        "value": int,
        "nonce": int,
        "gas_limit": int,
    }
    for key, typ in required.items():
        val = body.get(key)
        if val is None:
            raise ValueError(f"Transaction body missing required field: {key!r}")
        if not isinstance(val, typ):
            raise ValueError(
                f"Transaction body field {key!r} must be {typ.__name__}, got {type(val).__name__}"
            )

    if body["value"] < 0:
        raise ValueError("Transaction value must be non-negative")
    if body["nonce"] < 0:
        raise ValueError("Transaction nonce must be non-negative")
    if body["gas_limit"] <= 0:
        raise ValueError("Transaction gas_limit must be positive")


def estimate_fee(
    gas_limit: int = _DEFAULT_GAS_LIMIT,
    gas_price_wei: int = _DEFAULT_GAS_PRICE_WEI,
) -> int:
    """Return a simple fee estimate: gas_limit × gas_price_wei."""
    return gas_limit * gas_price_wei


def encode_to_cbor_hex(tx_dict: dict[str, Any]) -> str:
    """Encode *tx_dict* to CBOR and return a ``"0x..."`` hex string.

    Falls back to a JSON-based encoding if cbor2 is unavailable.
    """
    try:
        import cbor2  # type: ignore[import]  # noqa: PLC0415
        encoded = cbor2.dumps(tx_dict)
        return "0x" + encoded.hex()
    except ImportError:
        log.warning("cbor2 not available; falling back to JSON-based hex encoding for tx")
        import json  # noqa: PLC0415
        from animica_studio.services.error_format import safe_json_dumps  # noqa: PLC0415
        encoded_str = safe_json_dumps(tx_dict, separators=(",", ":"))
        return "0x" + encoded_str.encode("utf-8").hex()
    except Exception as exc:  # noqa: BLE001
        log.error("encode_to_cbor_hex failed: %s", exc)
        raise ValueError(f"Failed to encode transaction: {exc}") from exc
