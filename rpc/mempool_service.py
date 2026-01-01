from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from core.utils.hash import sha3_256
from core.utils.tx import normalize_tx_bytes
from mempool.config import MempoolConfig, load_config as load_mempool_config
from mempool.errors import AdmissionError, FeeTooLow, NonceGap
from mempool.pool import Pool, PoolConfig
from mempool.select import PendingTxEntry, select_for_block
from mempool.types import EffectiveFee, PoolTx, TxMeta

try:
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover - runtime fallback when core not available
    class Tx:  # type: ignore
        pass

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


def _tx_body(tx: Any) -> Optional[dict]:
    if not isinstance(tx, dict):
        return None
    body = tx.get("body")
    if isinstance(body, dict):
        return body
    nested = tx.get("tx")
    if isinstance(nested, dict):
        return nested
    return tx


def _sender_bytes(tx: Any) -> Optional[bytes]:
    sender = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "from" first (CLI uses this), then "sender"
        sender = body.get("from") or body.get("sender")

    # Handle Tx dataclass format
    if sender is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            sender = getattr(unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", None)

    # Convert sender to bytes
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
    nonce = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        nonce = body.get("nonce")

    # Handle Tx dataclass format
    if nonce is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            nonce = getattr(unsigned, "nonce", None)
    if nonce is None:
        nonce = getattr(tx, "nonce", None)

    try:
        return int(nonce) if nonce is not None else None
    except Exception:
        return None


def _tx_gas_limit(tx: Any) -> int:
    gas = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "gasLimit" first (CLI uses this), then "gas_limit"
        gas = body.get("gasLimit") or body.get("gas_limit")
        if gas is None:
            gas = body.get("gas")
        if isinstance(gas, dict):
            gas = gas.get("limit")

    # Handle Tx dataclass format
    if gas is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            gas = getattr(unsigned, "gas_limit", None)
    if gas is None:
        gas = getattr(tx, "gas_limit", None)

    return int(gas or 0)


def _tx_chain_id(tx: Any) -> Optional[int]:
    chain_id = None

    # Handle dict envelope with "body"/"tx" key (CLI/SDK format)
    body = _tx_body(tx)
    if isinstance(body, dict):
        # Try "chainId" first (CLI uses this), then "chain_id"
        chain_id = body.get("chainId") or body.get("chain_id")

    # Handle Tx dataclass format
    if chain_id is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            chain_id = getattr(unsigned, "chain_id", None)
    if chain_id is None:
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
        data_dir: str | Path | None = None,
        persist_enabled: bool = True,
        persist_ttl_s: int = 0,
    ) -> None:
        self.pool = pool
        self.chain_id = int(chain_id)
        self.min_gas_price_wei = int(min_gas_price_wei)
        self.state_db = state_db
        self.tx_index = tx_index
        self._persist_enabled = bool(persist_enabled)
        self._persist_ttl_s = int(persist_ttl_s) if int(persist_ttl_s) > 0 else 0
        self._persist_lock = threading.RLock()
        self._persist_path: Path | None = None
        if data_dir:
            self._persist_path = Path(data_dir).expanduser() / "mempool" / "pending.jsonl"
        self._restoring = False
        self._rejection_ttl_s = int(
            os.getenv("ANIMICA_MEMPOOL_REJECTION_TTL_S", "300") or 300
        )
        self._last_rejections: dict[str, dict[str, Any]] = {}
        if self._persist_enabled:
            self._load_persisted()

    def _record_rejection(
        self, tx_hash_hex: str, reason: str, details: dict[str, Any] | None = None
    ) -> None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        self._last_rejections[tx_hash_hex] = {
            "reason": reason,
            "details": details or {},
            "ts": time.time(),
        }
        self._prune_rejections()

    def _prune_rejections(self) -> None:
        if not self._last_rejections:
            return
        cutoff = time.time() - float(self._rejection_ttl_s)
        expired = [k for k, v in self._last_rejections.items() if v.get("ts", 0) < cutoff]
        for k in expired:
            self._last_rejections.pop(k, None)

    def get_rejection(self, tx_hash_hex: str) -> dict[str, Any] | None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        self._prune_rejections()
        return self._last_rejections.get(tx_hash_hex)

    @classmethod
    def create(
        cls,
        *,
        chain_id: int,
        min_gas_price_wei: int,
        state_db: Any | None,
        tx_index: Any | None,
        data_dir: str | Path | None = None,
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
        persist_env = os.getenv("ANIMICA_MEMPOOL_PERSIST")
        persist_enabled = True
        if persist_env is not None:
            persist_enabled = persist_env.strip().lower() in {"1", "true", "yes", "on"}
        persist_ttl_s = int(
            os.getenv("ANIMICA_MEMPOOL_PERSIST_TTL_S", str(mp_cfg.ttls.pending_seconds))
            or mp_cfg.ttls.pending_seconds
        )
        return cls(
            pool=pool,
            chain_id=chain_id,
            min_gas_price_wei=min_gas_price_wei,
            state_db=state_db,
            tx_index=tx_index,
            data_dir=data_dir,
            persist_enabled=persist_enabled,
            persist_ttl_s=persist_ttl_s,
        )

    def _persist_snapshot(self) -> None:
        if not self._persist_enabled or self._persist_path is None:
            return
        snapshot = self.snapshot(limit=len(self.pool) + 1)
        now = time.time()
        ttl_s = self._persist_ttl_s or 0
        entries: list[dict[str, Any]] = []
        for entry in snapshot.entries:
            raw = entry.raw
            if not raw:
                continue
            first_seen = (
                float(entry.received_at) if entry.received_at is not None else now
            )
            expires_at = (
                float(entry.expires_at)
                if entry.expires_at is not None
                else (first_seen + ttl_s if ttl_s else None)
            )
            if expires_at is not None and expires_at <= now:
                continue
            sender = _sender_hex(_sender_bytes(entry.tx))
            nonce = _tx_nonce(entry.tx)
            gas_limit = _tx_gas_limit(entry.tx)
            fee = EffectiveFee.from_tx(entry.tx)
            entries.append(
                {
                    "hash": entry.hash_hex,
                    "raw": raw.hex(),
                    "first_seen": first_seen,
                    "expires_at": expires_at,
                    "sender": sender,
                    "nonce": int(nonce) if nonce is not None else None,
                    "gas_limit": int(gas_limit) if gas_limit is not None else None,
                    "fee_wei": int(fee.effective_gas_price(None)),
                    "chain_id": int(self.chain_id),
                }
            )
        if self._persist_path.parent:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._persist_path.with_suffix(".tmp")
        with self._persist_lock:
            with tmp_path.open("wt", encoding="utf-8") as fh:
                for entry in entries:
                    fh.write(json.dumps(entry) + "\n")
            tmp_path.replace(self._persist_path)

    def _load_persisted(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            lines = self._persist_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            log.warning("Failed to read mempool persistence file", exc_info=exc)
            return
        restored = 0
        self._restoring = True
        now = time.time()
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("chain_id") not in (None, self.chain_id, int(self.chain_id)):
                continue
            expires_at = entry.get("expires_at")
            if expires_at is not None and float(expires_at) <= now:
                continue
            raw_hex = entry.get("raw") or ""
            if not isinstance(raw_hex, str) or not raw_hex:
                continue
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError:
                continue
            if not raw:
                continue
            try:
                tx_obj = (
                    Tx.from_cbor(raw)  # type: ignore[attr-defined]
                    if hasattr(Tx, "from_cbor")
                    else None
                )
            except Exception:
                tx_obj = None
            if tx_obj is None:
                continue
            try:
                self.submit(tx=tx_obj, raw=raw, local=True)
            except Exception:
                continue
            restored += 1
        self._restoring = False
        if restored:
            log.info("Restored mempool entries", extra={"count": restored})
            self._persist_snapshot()

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
        origin_peer: str | None = None,
    ) -> str:
        try:
            raw_bytes = normalize_tx_bytes(raw)
        except Exception as exc:
            tx_hash_hex = tx_hash_hex or "0x" + sha3_256(bytes(raw)).hex()
            self._record_rejection(
                tx_hash_hex,
                "decode_error",
                {"step": "normalize_raw", "error": str(exc)},
            )
            raise AdmissionError(
                "invalid raw tx bytes",
                context={"tx_hash": tx_hash_hex, "error": str(exc)},
            ) from exc

        if tx_hash_hex is None:
            tx_hash_hex = "0x" + sha3_256(raw_bytes).hex()
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)
        expected_hash = "0x" + sha3_256(raw_bytes).hex()
        if tx_hash_hex != expected_hash:
            self._record_rejection(
                tx_hash_hex,
                "hash_mismatch",
                {"expected": expected_hash, "got": tx_hash_hex},
            )
            raise AdmissionError(
                "tx hash mismatch for raw bytes",
                context={
                    "tx_hash": tx_hash_hex,
                    "expected": expected_hash,
                },
            )

        if isinstance(tx, dict):
            try:
                raw_from_dict = normalize_tx_bytes(tx)
            except Exception as exc:
                self._record_rejection(
                    tx_hash_hex,
                    "decode_error",
                    {"step": "normalize_envelope", "error": str(exc)},
                )
                raise AdmissionError(
                    "tx envelope missing canonical raw bytes",
                    context={"tx_hash": tx_hash_hex, "error": str(exc)},
                ) from exc
            if raw_from_dict != raw_bytes:
                self._record_rejection(
                    tx_hash_hex,
                    "raw_mismatch",
                    {
                        "expected_hash": expected_hash,
                        "dict_hash": tx_hash_hex,
                    },
                )
                raise AdmissionError(
                    "raw bytes mismatch between envelope and admission",
                    context={"tx_hash": tx_hash_hex},
                )

        origin_label = "local" if local else f"peer:{origin_peer or 'unknown'}"
        log.info(
            "MempoolService.submit: entry, tx_hash=%s, local=%s, pool_size=%d",
            tx_hash_hex,
            local,
            len(self.pool),
        )

        if self.has_hash(tx_hash_hex):
            log.info(
                "MempoolService.submit: duplicate (already in pool), tx_hash=%s",
                tx_hash_hex,
            )
            return tx_hash_hex

        chain_id = _tx_chain_id(tx)
        if chain_id is not None and chain_id != self.chain_id:
            self._record_rejection(
                tx_hash_hex,
                "chain_id_mismatch",
                {"expected": self.chain_id, "got": chain_id},
            )
            raise AdmissionError(
                f"chain_id mismatch: tx={chain_id}, node={self.chain_id}",
                context={"tx_hash": tx_hash_hex, "chain_id": chain_id},
            )

        sender = _sender_bytes(tx)
        nonce = _tx_nonce(tx)
        if sender is None or nonce is None:
            self._record_rejection(
                tx_hash_hex,
                "missing_sender_or_nonce",
                {"sender": _sender_hex(sender), "nonce": nonce},
            )
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
                    self._record_rejection(
                        tx_hash_hex,
                        "nonce_too_low",
                        {"expected": expected, "got": nonce},
                    )
                    raise AdmissionError(
                        f"nonce too low: expected {expected}, got {nonce}",
                        context={
                            "tx_hash": tx_hash_hex,
                            "sender": _sender_hex(sender),
                            "expected_nonce": expected,
                            "got_nonce": nonce,
                        },
                    )

                pending_next = None
                if nonce > expected:
                    try:
                        pending_next = self.pending_nonce(sender)
                    except Exception:
                        pending_next = None
                    if pending_next is None or nonce > pending_next:
                        self._record_rejection(
                            tx_hash_hex,
                            "nonce_gap",
                            {
                                "expected": expected,
                                "pending_next": pending_next,
                                "got": nonce,
                            },
                        )
                        raise NonceGap(
                            expected_nonce=expected if pending_next is None else pending_next,
                            got_nonce=nonce,
                            sender=_sender_hex(sender),
                            tx_hash=tx_hash_hex,
                        )

        gas_limit = _tx_gas_limit(tx)
        if gas_limit <= 0:
            self._record_rejection(
                tx_hash_hex,
                "invalid_gas_limit",
                {"gas_limit": gas_limit},
            )
            raise AdmissionError(
                "gas_limit must be > 0",
                context={"tx_hash": tx_hash_hex},
            )

        fee = EffectiveFee.from_tx(tx)
        offered = int(fee.effective_gas_price(None))
        if self.min_gas_price_wei and offered < self.min_gas_price_wei:
            self._record_rejection(
                tx_hash_hex,
                "fee_too_low",
                {"offered": offered, "min_required": self.min_gas_price_wei},
            )
            raise FeeTooLow(
                offered_gas_price_wei=offered,
                min_required_wei=self.min_gas_price_wei,
                tx_hash=tx_hash_hex,
                sender=_sender_hex(sender),
            )

        tx_to_store: Any = tx
        if not isinstance(tx, Tx):
            try:
                if hasattr(Tx, "from_cbor"):
                    tx_to_store = Tx.from_cbor(raw_bytes)  # type: ignore[attr-defined]
            except Exception:
                tx_to_store = tx

        meta = TxMeta(
            sender=_sender_hex(sender),
            nonce=nonce,
            gas_limit=gas_limit,
            size_bytes=len(raw_bytes),
            first_seen=time.time(),
            local=local,
            effective_fee_wei=offered,
            origin=origin_label,
            peer_id=origin_peer,
        )
        pool_tx = PoolTx(
            tx=tx_to_store,
            tx_hash=tx_hash_bytes,
            raw=raw_bytes,
            meta=meta,
            fee=fee,
        )
        
        log.info(
            "MempoolService.submit: calling pool.add(), tx_hash=%s, sender=%s, nonce=%d",
            tx_hash_hex,
            meta.sender,
            meta.nonce,
        )
        try:
            self.pool.add(pool_tx, meta, is_local=local)
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex,
                "pool_reject",
                {"error": str(exc)},
            )
            raise
        
        # Verify tx was actually added to pool
        if not self.has_hash(tx_hash_hex):
            log.error(
                "MempoolService.submit: CRITICAL - pool.add() succeeded but tx not in pool, tx_hash=%s",
                tx_hash_hex,
            )
            self._record_rejection(
                tx_hash_hex,
                "pool_missing",
                {"tx_hash": tx_hash_hex},
            )
            raise AdmissionError(
                "pool.add succeeded but tx not in pool",
                context={"tx_hash": tx_hash_hex},
            )
        
        log.info(
            "MempoolService.submit: SUCCESS - tx added and verified in pool, tx_hash=%s, pool_size=%d",
            tx_hash_hex,
            len(self.pool),
        )
        log.info(
            "mempool.accepted",
            extra={
                "hash": tx_hash_hex,
                "from": meta.sender,
                "nonce": meta.nonce,
                "origin": origin_label,
                "peer": origin_peer,
            },
        )
        if not self._restoring:
            self._persist_snapshot()
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

    def get_raw(self, tx_hash_hex: str) -> bytes | None:
        try:
            tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)
        except Exception:
            return None
        entry = self.pool.index.get(tx_hash_bytes)
        if entry is None:
            return None

        # Index entries can be either IndexEntry(tx=PoolTx, meta=TxMeta) or PoolTx.
        raw = getattr(entry, "raw", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)

        tx_obj = getattr(entry, "tx", entry)
        raw = getattr(tx_obj, "raw", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        raw = getattr(tx_obj, "raw_cbor", None)
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        if hasattr(tx_obj, "to_cbor"):
            try:
                raw_bytes = tx_obj.to_cbor()
            except Exception:
                raw_bytes = None
            if isinstance(raw_bytes, (bytes, bytearray)):
                return bytes(raw_bytes)
        return None

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
        if removed and not self._restoring:
            self._persist_snapshot()
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
        if evicted and not self._restoring:
            self._persist_snapshot()
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
