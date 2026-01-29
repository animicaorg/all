from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from pq.py.address import decode_address


@dataclass(frozen=True)
class PendingTxEntry:
    hash_hex: str
    raw: bytes
    tx: Any | None = None
    received_at: float | None = None
    expires_at: float | None = None


@dataclass
class BlockSelection:
    selected: list[Any] = field(default_factory=list)
    selected_hashes: list[str] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    rejected_by_hash: dict[str, str] = field(default_factory=dict)
    rejected_details_by_hash: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_pending: int = 0


def _normalize_hash_hex(hash_hex: str) -> str:
    if not hash_hex:
        return hash_hex
    normalized = hash_hex if hash_hex.startswith("0x") else f"0x{hash_hex}"
    return normalized.lower()


def _tx_chain_id(tx: Any) -> Optional[int]:
    for attr in ("chain_id", "chainId"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    unsigned = getattr(tx, "unsigned", None)
    if unsigned is not None:
        value = getattr(unsigned, "chain_id", getattr(unsigned, "chainId", None))
        if value is not None:
            return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", {}))
        if isinstance(body, dict):
            value = body.get("chain_id", body.get("chainId"))
            if value is not None:
                return int(value)
    return None


def _tx_sender(tx: Any) -> bytes | None:
    sender = None
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            sender = getattr(unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", {}))
        if isinstance(body, dict):
            sender = sender or body.get("sender", body.get("from"))
    if isinstance(sender, str):
        if sender.startswith("0x"):
            try:
                sender = bytes.fromhex(sender[2:])
            except ValueError:
                sender = None
        elif sender.startswith("anim1"):
            try:
                record = decode_address(sender)
                sender = bytes(record.digest)[:32].ljust(32, b"\x00")
            except Exception:
                sender = None
        else:
            try:
                sender = bytes.fromhex(sender)
            except ValueError:
                sender = None
    if sender is not None and not isinstance(sender, (bytes, bytearray)):
        sender = None
    return bytes(sender) if sender else None


def _tx_valid_after(tx: Any) -> int | None:
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            val = getattr(unsigned, "valid_after", None)
            if val is not None:
                return int(val)
    for attr in ("valid_after", "validAfter"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", tx))
        if isinstance(body, dict):
            value = body.get("validAfter", body.get("valid_after"))
            if value is not None:
                return int(value)
    return None


def _tx_valid_until(tx: Any) -> int | None:
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            val = getattr(unsigned, "valid_until", None)
            if val is not None:
                return int(val)
    for attr in ("valid_until", "validUntil"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", tx))
        if isinstance(body, dict):
            value = body.get("validUntil", body.get("valid_until"))
            if value is not None:
                return int(value)
    return None


def _tx_hash_bytes(entry: PendingTxEntry, tx: Any) -> bytes | None:
    if entry.hash_hex:
        try:
            return bytes.fromhex(entry.hash_hex[2:] if entry.hash_hex.startswith("0x") else entry.hash_hex)
        except Exception:
            return None
    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        try:
            return bytes(tx.txid())
        except Exception:
            return None
    return None

def _tx_gas_limit(tx: Any) -> int:
    for attr in ("gas_limit", "gas", "intrinsic_gas"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            value = getattr(unsigned, "gas_limit", getattr(unsigned, "gas", None))
            if value is not None:
                return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", {}))
        if isinstance(body, dict):
            value = body.get("gasLimit", body.get("gas", body.get("gas_limit")))
            if value is not None:
                return int(value)
    return 0


def _tx_size_bytes(raw: bytes, tx: Any) -> int:
    if raw:
        return len(raw)
    raw_cbor = getattr(tx, "raw_cbor", None)
    if raw_cbor:
        return len(raw_cbor)
    if hasattr(tx, "to_cbor"):
        try:
            return len(tx.to_cbor())
        except Exception:
            return 0
    return 0


def _bump_reject(
    result: BlockSelection,
    hash_hex: str,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    result.rejected[reason] = result.rejected.get(reason, 0) + 1
    if hash_hex:
        result.rejected_by_hash[hash_hex] = reason
        if details is not None:
            result.rejected_details_by_hash[hash_hex] = {
                "reason": reason,
                "details": details,
            }


def _tx_gas_price(tx: Any) -> int:
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            value = getattr(unsigned, "gas_price", None)
            if value is not None:
                return int(value)
    for attr in ("gas_price", "gasPrice", "tip", "maxFee", "max_fee"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", tx))
        if isinstance(body, dict):
            gas = body.get("gas")
            if isinstance(gas, dict) and gas.get("price") is not None:
                return int(gas["price"])
            for key in ("gasPrice", "gas_price", "tip", "maxFee", "max_fee"):
                if body.get(key) is not None:
                    return int(body[key])
    return 0


def _tx_value(tx: Any) -> int:
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            payload = getattr(unsigned, "payload", None)
            if payload is not None and hasattr(payload, "amount"):
                return int(getattr(payload, "amount", 0))
    for attr in ("value", "amount"):
        value = getattr(tx, attr, None)
        if value is not None:
            return int(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", tx))
        if isinstance(body, dict):
            for key in ("value", "amount"):
                if body.get(key) is not None:
                    return int(body[key])
    return 0


def _tx_recipient(tx: Any) -> bytes | None:
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            payload = getattr(unsigned, "payload", None)
            if payload is not None and hasattr(payload, "to"):
                return bytes(getattr(payload, "to"))
    for attr in ("to", "recipient"):
        value = getattr(tx, attr, None)
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", tx))
        if isinstance(body, dict):
            payload = body.get("payload")
            if isinstance(payload, dict):
                payload_v = payload.get("v", {})
                if isinstance(payload_v, dict) and payload_v.get("to") is not None:
                    val = payload_v.get("to")
                    if isinstance(val, (bytes, bytearray)):
                        return bytes(val)
            val = body.get("to")
            if isinstance(val, (bytes, bytearray)):
                return bytes(val)
    return None


def select_for_block(
    *,
    head_state: dict[str, Any],
    limits: dict[str, Any],
    pending: Iterable[PendingTxEntry],
    decode: Callable[[bytes], Any] | None = None,
    state_db: Any | None = None,
    policy: dict[str, Any] | None = None,
    tx_index: Any | None = None,
    signature_validator: Callable[[Any, dict[str, Any] | None], None] | None = None,
    now: float | None = None,
) -> BlockSelection:
    """
    Select eligible transactions for block assembly.

    Returns a BlockSelection containing the chosen txs plus rejection counts/reasons.
    """
    result = BlockSelection()
    chain_id = head_state.get("chain_id", head_state.get("chainId"))
    max_gas = int(
        limits.get("max_gas", limits.get("gas_limit", limits.get("gas", 0))) or 0
    )
    max_bytes = int(limits.get("max_bytes", limits.get("byte_limit", limits.get("bytes", 0))) or 0)
    max_txs = limits.get("max_txs", limits.get("limit"))
    max_txs = int(max_txs) if max_txs is not None else None
    min_gas_price = 0
    if policy:
        min_gas_price = int(
            policy.get("min_gas_price", policy.get("minGasPrice", 0)) or 0
        )

    total_gas = 0
    total_bytes = 0
    now_ts = now if now is not None else None
    current_height = int(head_state.get("height", head_state.get("block_height", 0)) or 0)

    candidates: list[tuple[int, bytes, PendingTxEntry, Any]] = []

    for entry in pending:
        result.total_pending += 1
        hash_hex = _normalize_hash_hex(entry.hash_hex)
        tx = entry.tx
        decoded_obj: dict[str, Any] | None = None
        if entry.expires_at is not None:
            now_value = now_ts
            if now_value is None:
                import time

                now_value = time.time()
                now_ts = now_value
            if entry.expires_at <= now_value:
                _bump_reject(
                    result,
                    hash_hex,
                    "expired",
                    details={"expires_at": entry.expires_at, "now": now_value},
                )
                continue
        if tx is None and decode is not None:
            decoded = decode(entry.raw)
            if isinstance(decoded, tuple):
                tx = decoded[0]
                decoded_obj = decoded[1] if isinstance(decoded[1], dict) else None
            else:
                tx = decoded
                decoded_obj = decoded if isinstance(decoded, dict) else None
        if tx is None:
            _bump_reject(result, hash_hex, "invalid_format")
            continue

        sender = _tx_sender(tx)
        if sender is None and decode is not None and entry.raw:
            try:
                decoded = decode(entry.raw)
                if isinstance(decoded, tuple):
                    tx = decoded[0]
                    if decoded_obj is None and isinstance(decoded[1], dict):
                        decoded_obj = decoded[1]
                else:
                    tx = decoded
                    if decoded_obj is None and isinstance(decoded, dict):
                        decoded_obj = decoded
            except Exception:
                tx = tx
            sender = _tx_sender(tx)

        if chain_id is not None:
            tx_chain_id = _tx_chain_id(tx)
            if tx_chain_id is not None and int(tx_chain_id) != int(chain_id):
                _bump_reject(
                    result,
                    hash_hex,
                    "chain_id_mismatch",
                    details={"expected": int(chain_id), "got": int(tx_chain_id)},
                )
                continue

        tx_hash_bytes = _tx_hash_bytes(entry, tx)
        if tx_index is not None and hasattr(tx_index, "exists") and tx_hash_bytes is not None:
            try:
                if tx_index.exists(tx_hash_bytes):
                    _bump_reject(result, hash_hex, "replay")
                    continue
            except Exception:
                pass

        if signature_validator is not None:
            try:
                signature_validator(tx, decoded_obj)
            except Exception:
                _bump_reject(result, hash_hex, "invalid_sig")
                continue

        if sender is None:
            _bump_reject(result, hash_hex, "missing_sender")
            continue

        valid_after = _tx_valid_after(tx)
        valid_until = _tx_valid_until(tx)
        if valid_after is None or valid_until is None:
            _bump_reject(result, hash_hex, "missing_validity")
            continue
        if current_height < valid_after:
            _bump_reject(
                result,
                hash_hex,
                "not_yet_valid",
                details={"valid_after": valid_after, "current_height": current_height},
            )
            continue
        if current_height > valid_until:
            _bump_reject(
                result,
                hash_hex,
                "expired",
                details={"valid_until": valid_until, "current_height": current_height},
            )
            continue

        gas_price = _tx_gas_price(tx)
        if min_gas_price and gas_price < min_gas_price:
            _bump_reject(
                result,
                hash_hex,
                "fee_too_low",
                details={"min": min_gas_price, "got": gas_price},
            )
            continue

        if tx_hash_bytes is None:
            tx_hash_bytes = bytes.fromhex(hash_hex[2:]) if hash_hex.startswith("0x") else bytes.fromhex(hash_hex)

        candidates.append((gas_price, tx_hash_bytes, entry, tx))

    # Deterministic ordering by fee desc, txid asc
    candidates.sort(key=lambda item: (-item[0], item[1]))

    balances: dict[bytes, int] = {}

    for gas_price, tx_hash_bytes, entry, tx in candidates:
        hash_hex = _normalize_hash_hex(entry.hash_hex)
        sender = _tx_sender(tx)
        if sender is None:
            continue

        gas = _tx_gas_limit(tx)
        if max_gas and total_gas + gas > max_gas:
            _bump_reject(result, hash_hex, "exceeds_block_gas")
            continue

        size_bytes = _tx_size_bytes(entry.raw, tx)
        if max_bytes and size_bytes and total_bytes + size_bytes > max_bytes:
            _bump_reject(result, hash_hex, "exceeds_block_bytes")
            continue

        if max_txs is not None and len(result.selected) >= max_txs:
            _bump_reject(result, hash_hex, "max_txs")
            continue

        required = _tx_value(tx) + (gas * gas_price)
        if state_db is not None and hasattr(state_db, "get_balance"):
            if sender not in balances:
                try:
                    balances[sender] = int(state_db.get_balance(sender))  # type: ignore[call-arg]
                except Exception:
                    balances[sender] = 0
            if balances[sender] < required:
                _bump_reject(
                    result,
                    hash_hex,
                    "insufficient_funds",
                    details={"need": required, "have": balances[sender]},
                )
                continue

        total_gas += gas
        total_bytes += size_bytes
        result.selected.append(tx)
        result.selected_hashes.append(hash_hex)

        if sender in balances:
            balances[sender] -= required
        recipient = _tx_recipient(tx)
        if recipient is not None and recipient in balances:
            balances[recipient] += _tx_value(tx)

    return result


__all__ = ["PendingTxEntry", "BlockSelection", "select_for_block"]
