from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from pq.py.address import decode_address


@dataclass(frozen=True)
class PendingTxEntry:
    hash_hex: str
    raw: bytes
    tx: Any | None = None


@dataclass
class BlockSelection:
    selected: list[Any] = field(default_factory=list)
    selected_hashes: list[str] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    rejected_by_hash: dict[str, str] = field(default_factory=dict)
    total_pending: int = 0


def _normalize_hash_hex(hash_hex: str) -> str:
    if not hash_hex:
        return hash_hex
    return hash_hex if hash_hex.startswith("0x") else f"0x{hash_hex}"


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


def _tx_sender_nonce(tx: Any) -> tuple[bytes | None, int | None]:
    sender = None
    nonce = None
    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            sender = getattr(unsigned, "sender", None)
            nonce = getattr(unsigned, "nonce", None)
    if sender is None:
        sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
    if nonce is None:
        nonce = getattr(tx, "nonce", None)
    if isinstance(tx, dict):
        body = tx.get("body", tx.get("tx", {}))
        if isinstance(body, dict):
            sender = sender or body.get("sender", body.get("from"))
            nonce = nonce or body.get("nonce")
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
    try:
        nonce = int(nonce) if nonce is not None else None
    except Exception:
        nonce = None
    return (bytes(sender) if sender else None, nonce)


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


def _bump_reject(result: BlockSelection, hash_hex: str, reason: str) -> None:
    result.rejected[reason] = result.rejected.get(reason, 0) + 1
    if hash_hex:
        result.rejected_by_hash[hash_hex] = reason


def select_for_block(
    *,
    head_state: dict[str, Any],
    limits: dict[str, Any],
    pending: Iterable[PendingTxEntry],
    decode: Callable[[bytes], Any] | None = None,
    state_db: Any | None = None,
) -> BlockSelection:
    """
    Select eligible transactions for block assembly.

    Returns a BlockSelection containing the chosen txs plus rejection counts/reasons.
    """
    result = BlockSelection()
    chain_id = head_state.get("chain_id", head_state.get("chainId"))
    max_gas = int(limits.get("max_gas", limits.get("gas_limit", limits.get("gas", 0))) or 0)
    max_bytes = int(limits.get("max_bytes", limits.get("byte_limit", limits.get("bytes", 0))) or 0)
    max_txs = limits.get("max_txs", limits.get("limit"))
    max_txs = int(max_txs) if max_txs is not None else None

    total_gas = 0
    total_bytes = 0
    sender_nonces: dict[bytes, int] = {}

    for entry in pending:
        result.total_pending += 1
        hash_hex = _normalize_hash_hex(entry.hash_hex)
        tx = entry.tx
        if tx is None and decode is not None:
            decoded = decode(entry.raw)
            if isinstance(decoded, tuple):
                tx = decoded[0]
            else:
                tx = decoded
        if tx is None:
            _bump_reject(result, hash_hex, "decode_failed")
            continue

        if chain_id is not None:
            tx_chain_id = _tx_chain_id(tx)
            if tx_chain_id is not None and int(tx_chain_id) != int(chain_id):
                _bump_reject(result, hash_hex, "chain_id_mismatch")
                continue

        sender, nonce = _tx_sender_nonce(tx)
        if sender is None:
            _bump_reject(result, hash_hex, "missing_sender")
            continue
        if nonce is None:
            _bump_reject(result, hash_hex, "missing_nonce")
            continue

        expected = sender_nonces.get(sender)
        if expected is None:
            expected = 0
            if state_db is not None and hasattr(state_db, "get_nonce"):
                try:
                    expected = int(state_db.get_nonce(sender))  # type: ignore[call-arg]
                except Exception:
                    expected = 0
            sender_nonces[sender] = expected
        if nonce < expected:
            _bump_reject(result, hash_hex, "nonce_too_low")
            continue
        if nonce > expected:
            _bump_reject(result, hash_hex, "nonce_gap")
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

        total_gas += gas
        total_bytes += size_bytes
        sender_nonces[sender] = expected + 1
        result.selected.append(tx)
        result.selected_hashes.append(hash_hex)

    return result


__all__ = ["PendingTxEntry", "BlockSelection", "select_for_block"]
