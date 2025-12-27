from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from core.utils.hash import sha3_256
from mempool.config import MempoolConfig, load_config as load_mempool_config
from mempool.errors import AdmissionError, FeeTooLow, NonceGap
from mempool.pool import Pool, PoolConfig
from mempool.select import PendingTxEntry, select_for_block
from mempool.types import EffectiveFee, PoolTx, TxMeta

log = logging.getLogger("animica.rpc.mempool")


@dataclass(frozen=True)
class MempoolSnapshot:
    entries: list[PendingTxEntry]
    raw_by_hash: dict[str, bytes]
    total: int


def _normalize_hash_hex(hash_hex: str) -> str:
    if hash_hex.startswith("0x"):
        return hash_hex.lower()
    return f"0x{hash_hex.lower()}"


def _normalize_hash_bytes(hash_value: Any) -> bytes:
    if isinstance(hash_value, (bytes, bytearray)):
        return bytes(hash_value)
    if isinstance(hash_value, str):
        value = hash_value[2:] if hash_value.startswith("0x") else hash_value
        return bytes.fromhex(value)
    return bytes(hash_value)


def _sender_bytes(tx: Any) -> Optional[bytes]:
    unsigned = getattr(tx, "unsigned", None)
    sender = None
    if unsigned is not None:
        sender = getattr(unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", None)
    if isinstance(sender, str):
        if sender.startswith("0x"):
            try:
                return bytes.fromhex(sender[2:])
            except ValueError:
                return None
        return None
    if isinstance(sender, (bytes, bytearray)):
        return bytes(sender)
    return None


def _sender_hex(sender: Optional[bytes]) -> str:
    if not sender:
        return "0x"
    return "0x" + bytes(sender).hex()


def _tx_nonce(tx: Any) -> Optional[int]:
    unsigned = getattr(tx, "unsigned", None)
    if unsigned is not None:
        nonce = getattr(unsigned, "nonce", None)
    else:
        nonce = getattr(tx, "nonce", None)
    try:
        return int(nonce) if nonce is not None else None
    except Exception:
        return None


def _tx_gas_limit(tx: Any) -> int:
    unsigned = getattr(tx, "unsigned", None)
    if unsigned is not None:
        gas = getattr(unsigned, "gas_limit", None)
    else:
        gas = getattr(tx, "gas_limit", None)
    return int(gas or 0)


def _tx_chain_id(tx: Any) -> Optional[int]:
    unsigned = getattr(tx, "unsigned", None)
    if unsigned is not None:
        chain_id = getattr(unsigned, "chain_id", None)
    else:
        chain_id = getattr(tx, "chain_id", None)
    try:
        return int(chain_id) if chain_id is not None else None
    except Exception:
        return None


class MempoolService:
    def __init__(
        self,
        *,
        pool: Pool,
        chain_id: int,
        min_gas_price_wei: int,
        state_db: Any | None,
        tx_index: Any | None,
    ) -> None:
        self.pool = pool
        self.chain_id = int(chain_id)
        self.min_gas_price_wei = int(min_gas_price_wei)
        self.state_db = state_db
        self.tx_index = tx_index

    @classmethod
    def create(
        cls,
        *,
        chain_id: int,
        min_gas_price_wei: int,
        state_db: Any | None,
        tx_index: Any | None,
        config: MempoolConfig | None = None,
    ) -> "MempoolService":
        mp_cfg = config or load_mempool_config()
        pool_cfg = PoolConfig(
            max_txs=mp_cfg.limits.max_txs,
            max_bytes=mp_cfg.limits.max_bytes,
            target_util=mp_cfg.gas.target_utilization,
            accept_below_floor_for_local=True,
        )
        pool = Pool(cfg=pool_cfg)
        return cls(
            pool=pool,
            chain_id=chain_id,
            min_gas_price_wei=min_gas_price_wei,
            state_db=state_db,
            tx_index=tx_index,
        )

    def has_hash(self, tx_hash_hex: str) -> bool:
        try:
            return self.pool.get(_normalize_hash_bytes(tx_hash_hex)) is not None
        except Exception:
            return False

    def submit(
        self,
        *,
        tx: Any,
        raw: bytes,
        tx_hash_hex: str | None = None,
        local: bool = True,
    ) -> str:
        if tx_hash_hex is None:
            tx_hash_hex = "0x" + sha3_256(raw).hex()
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)

        if self.has_hash(tx_hash_hex):
            return tx_hash_hex

        chain_id = _tx_chain_id(tx)
        if chain_id is not None and chain_id != self.chain_id:
            raise AdmissionError(
                f"chain_id mismatch: tx={chain_id}, node={self.chain_id}",
                context={"tx_hash": tx_hash_hex, "chain_id": chain_id},
            )

        sender = _sender_bytes(tx)
        nonce = _tx_nonce(tx)
        if sender is None or nonce is None:
            raise AdmissionError(
                "missing sender or nonce",
                context={"tx_hash": tx_hash_hex},
            )

        if self.state_db is not None and hasattr(self.state_db, "get_nonce"):
            try:
                expected = int(self.state_db.get_nonce(sender))
            except Exception as exc:
                log.debug("mempool nonce check failed; skipping", exc_info=exc)
            else:
                if nonce < expected:
                    raise AdmissionError(
                        f"nonce too low: expected {expected}, got {nonce}",
                        context={
                            "tx_hash": tx_hash_hex,
                            "sender": _sender_hex(sender),
                            "expected_nonce": expected,
                            "got_nonce": nonce,
                        },
                    )
                if nonce > expected:
                    raise NonceGap(
                        expected_nonce=expected,
                        got_nonce=nonce,
                        sender=_sender_hex(sender),
                        tx_hash=tx_hash_hex,
                    )

        gas_limit = _tx_gas_limit(tx)
        if gas_limit <= 0:
            raise AdmissionError(
                "gas_limit must be > 0",
                context={"tx_hash": tx_hash_hex},
            )

        fee = EffectiveFee.from_tx(tx)
        offered = int(fee.effective_gas_price(None))
        if self.min_gas_price_wei and offered < self.min_gas_price_wei:
            raise FeeTooLow(
                offered_gas_price_wei=offered,
                min_required_wei=self.min_gas_price_wei,
                tx_hash=tx_hash_hex,
                sender=_sender_hex(sender),
            )

        meta = TxMeta(
            sender=_sender_hex(sender),
            nonce=nonce,
            gas_limit=gas_limit,
            size_bytes=len(raw),
            first_seen=time.time(),
            local=local,
            effective_fee_wei=offered,
        )
        pool_tx = PoolTx(
            tx=tx,
            tx_hash=tx_hash_bytes,
            raw=raw,
            meta=meta,
            fee=fee,
        )
        self.pool.add(pool_tx, meta, is_local=local)
        return tx_hash_hex

    def snapshot(self, *, limit: int = 1000) -> MempoolSnapshot:
        raw_by_hash: dict[str, bytes] = {}
        entries: list[PendingTxEntry] = []
        total = len(self.pool)

        seen_hashes: set[str] = set()
        try:
            for tx_item, meta in self.pool.iter_ready():  # type: ignore[misc]
                pool_tx = tx_item
                raw = getattr(pool_tx, "raw", b"")
                tx = getattr(pool_tx, "tx", pool_tx)
                tx_hash_value = getattr(pool_tx, "tx_hash", None) or getattr(
                    meta, "tx_hash", None
                )
                if tx_hash_value is None:
                    continue
                tx_hash_hex = _normalize_hash_hex(
                    "0x" + _normalize_hash_bytes(tx_hash_value).hex()
                )
                if tx_hash_hex in seen_hashes:
                    continue
                seen_hashes.add(tx_hash_hex)
                raw_by_hash[tx_hash_hex] = raw
                entries.append(
                    PendingTxEntry(
                        hash_hex=tx_hash_hex,
                        raw=raw,
                        tx=tx,
                        received_at=getattr(meta, "first_seen", None),
                        expires_at=getattr(meta, "expires_at", None),
                    )
                )
                if len(entries) >= limit:
                    return MempoolSnapshot(entries=entries, raw_by_hash=raw_by_hash, total=total)
        except Exception as exc:
            log.debug("mempool ready snapshot failed", exc_info=exc)

        held_entries: list[tuple[float, PendingTxEntry, bytes]] = []
        for hash_bytes, entry in self.pool.index.all_items():
            tx_item = entry.tx
            meta = entry.meta
            pool_tx = tx_item
            raw = getattr(pool_tx, "raw", b"")
            tx = getattr(pool_tx, "tx", pool_tx)
            tx_hash_hex = _normalize_hash_hex(
                "0x" + _normalize_hash_bytes(hash_bytes).hex()
            )
            if tx_hash_hex in seen_hashes:
                continue
            seen_hashes.add(tx_hash_hex)
            received_at = getattr(meta, "first_seen", None)
            pending_entry = PendingTxEntry(
                hash_hex=tx_hash_hex,
                raw=raw,
                tx=tx,
                received_at=received_at,
                expires_at=getattr(meta, "expires_at", None),
            )
            held_entries.append((float(received_at or 0.0), pending_entry, raw))

        held_entries.sort(key=lambda item: item[0])
        for _ts, entry, raw in held_entries:
            raw_by_hash[entry.hash_hex] = raw
            entries.append(entry)
            if len(entries) >= limit:
                break

        return MempoolSnapshot(entries=entries, raw_by_hash=raw_by_hash, total=total)

    def list_pending(self, *, limit: int = 1000) -> list[str]:
        snapshot = self.snapshot(limit=limit)
        return [entry.hash_hex for entry in snapshot.entries]

    def stats(self) -> dict[str, int]:
        stats = self.pool.stats()
        return {
            "count": int(getattr(stats, "total_txs", len(self.pool))),
            "totalBytes": int(getattr(stats, "total_bytes", 0)),
            "totalGas": int(getattr(stats, "total_gas", 0)),
        }

    def remove_included(self, tx_hashes: Iterable[str]) -> int:
        removed = 0
        for h in tx_hashes:
            try:
                if self.has_hash(h):
                    self.pool.remove_included([_normalize_hash_bytes(h)])
                    removed += 1
            except Exception:
                continue
        return removed

    def revalidate(self) -> dict[str, int]:
        snapshot = self.snapshot(limit=len(self.pool) + 1)
        if not snapshot.entries:
            return {"evicted": 0}

        selection = select_for_block(
            head_state={"chain_id": self.chain_id},
            limits={"max_gas": 10**18, "max_bytes": 10**18, "max_txs": None},
            pending=snapshot.entries,
            decode=None,
            state_db=self.state_db,
            policy={"min_gas_price": self.min_gas_price_wei},
            tx_index=self.tx_index,
            signature_validator=None,
        )

        evicted = 0
        for hash_hex, reason in selection.rejected_by_hash.items():
            if reason in {"exceeds_block_gas"}:
                continue
            try:
                self.pool.remove_included([_normalize_hash_bytes(hash_hex)])
                evicted += 1
            except Exception:
                continue
        return {"evicted": evicted}

    def pending_nonce(self, sender_bytes: bytes) -> Optional[int]:
        sender_hex = _sender_hex(sender_bytes)
        max_nonce: Optional[int] = None
        for _hash_bytes, entry in self.pool.index.all_items():
            meta = entry.meta
            if getattr(meta, "sender", None) != sender_hex:
                continue
            nonce = int(getattr(meta, "nonce", 0))
            if max_nonce is None or nonce > max_nonce:
                max_nonce = nonce
        if max_nonce is None:
            return None
        return max_nonce + 1

    def diagnose(self, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
        snapshot = self.snapshot(limit=limit)
        if not snapshot.entries:
            return {}

        selection = select_for_block(
            head_state={"chain_id": self.chain_id},
            limits={"max_gas": 10**18, "max_bytes": 10**18, "max_txs": None},
            pending=snapshot.entries,
            decode=None,
            state_db=self.state_db,
            policy={"min_gas_price": self.min_gas_price_wei},
            tx_index=self.tx_index,
            signature_validator=None,
        )

        diagnostics: dict[str, dict[str, Any]] = {}
        selected = set(selection.selected_hashes)
        for entry in snapshot.entries:
            if entry.hash_hex in selected:
                diagnostics[entry.hash_hex] = {"status": "eligible", "reason": None}
            else:
                reason = selection.rejected_by_hash.get(entry.hash_hex, "unknown")
                diagnostics[entry.hash_hex] = {"status": "rejected", "reason": reason}
        return diagnostics


__all__ = ["MempoolService", "MempoolSnapshot"]
