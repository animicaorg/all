from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from core.utils.hash import sha3_256


@dataclass(frozen=True)
class InstantTxBlock:
    """Micro-confirmation receipt for one or more txids anchored to canonical PoW head."""

    version: int
    anchor_hash: str
    txids: list[str]
    timestamp: int
    mempool_state_root: str | None = None

    def canonical_payload(self) -> bytes:
        payload = {
            "version": int(self.version),
            "anchor_hash": self.anchor_hash,
            "txids": sorted(self.txids),
            "timestamp": int(self.timestamp),
            "mempool_state_root": self.mempool_state_root,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def block_id(self) -> str:
        return "0x" + sha3_256(self.canonical_payload()).hex()


class InstantTxService:
    """Stores and manages short-lived instant tx receipts in separate storage.

    Backward compatibility strategy:
    - Use capability negotiation with feature bit/capability string INSTANT_TXBLOCK_V1.
    - Unknown/unsupported peers never receive txblock messages; legacy peers remain on canonical PoW sync.
    """

    def __init__(self, *, data_root: Path, chain_id: int, ttl_s: int = 1800) -> None:
        self._ttl_s = int(ttl_s)
        self._dir = Path(data_root) / f"chain-{int(chain_id)}" / "instant_blocks"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._tx_index_path = self._dir / "tx_index.json"
        self._blocks_path = self._dir / "blocks.json"
        self._lock = threading.RLock()
        self._tx_index: dict[str, dict[str, Any]] = {}
        self._blocks: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if self._tx_index_path.exists():
                try:
                    self._tx_index = json.loads(self._tx_index_path.read_text("utf-8"))
                except Exception:
                    self._tx_index = {}
            if self._blocks_path.exists():
                try:
                    self._blocks = json.loads(self._blocks_path.read_text("utf-8"))
                except Exception:
                    self._blocks = {}

    def _flush(self) -> None:
        tmp1 = self._tx_index_path.with_suffix(".json.tmp")
        tmp2 = self._blocks_path.with_suffix(".json.tmp")
        tmp1.write_text(json.dumps(self._tx_index, sort_keys=True), "utf-8")
        tmp2.write_text(json.dumps(self._blocks, sort_keys=True), "utf-8")
        tmp1.replace(self._tx_index_path)
        tmp2.replace(self._blocks_path)

    def _prune_locked(self, now: int | None = None) -> None:
        now_i = int(now if now is not None else time.time())
        expired = []
        for block_id, rec in self._blocks.items():
            ts = int(rec.get("timestamp") or 0)
            if ts + self._ttl_s < now_i and not rec.get("finalized_in_pow"):
                expired.append(block_id)
        for block_id in expired:
            rec = self._blocks.pop(block_id, None) or {}
            for txid in rec.get("txids") or []:
                cur = self._tx_index.get(txid)
                if cur and cur.get("block_id") == block_id:
                    self._tx_index.pop(txid, None)

    def emit_local(self, *, txid: str, anchor_hash: str, timestamp: int | None = None) -> dict[str, Any]:
        ts = int(timestamp if timestamp is not None else time.time())
        block = InstantTxBlock(version=1, anchor_hash=anchor_hash, txids=[txid], timestamp=ts)
        block_id = block.block_id()
        rec = {
            "block_id": block_id,
            "version": 1,
            "anchor_hash": anchor_hash,
            "txids": [txid],
            "timestamp": ts,
            "instant_confirmed": False,
            "finalized_in_pow": False,
            "revoked": False,
            "reason": "pending_mempool",
            "source": "local",
        }
        with self._lock:
            self._prune_locked(ts)
            self._blocks[block_id] = rec
            self._tx_index[txid] = {
                "block_id": block_id,
                "instant_confirmed": False,
                "finalized_in_pow": False,
                "revoked": False,
                "reason": "pending_mempool",
                "anchor_hash": anchor_hash,
                "timestamp": ts,
            }
            self._flush()
        return rec

    def ingest_remote(self, rec: dict[str, Any]) -> bool:
        txids = [str(t) for t in (rec.get("txids") or [])]
        if not txids:
            return False
        block_id = str(rec.get("block_id") or "")
        if not block_id.startswith("0x"):
            return False
        with self._lock:
            if block_id in self._blocks:
                return False
            self._blocks[block_id] = dict(rec)
            for txid in txids:
                remote_instant = bool(rec.get("instant_confirmed"))
                self._tx_index[txid] = {
                    "block_id": block_id,
                    "instant_confirmed": remote_instant,
                    "finalized_in_pow": bool(rec.get("finalized_in_pow")),
                    "revoked": bool(rec.get("revoked")),
                    "reason": rec.get("reason") or ("instant_confirmed" if remote_instant else "pending_mempool"),
                    "anchor_hash": rec.get("anchor_hash"),
                    "timestamp": int(rec.get("timestamp") or time.time()),
                }
            self._prune_locked()
            self._flush()
        return True

    def mark_finalized(self, txids: list[str]) -> None:
        with self._lock:
            for txid in txids:
                row = self._tx_index.get(txid)
                if not row:
                    continue
                row["finalized_in_pow"] = True
                row["reason"] = "included_in_pow"
                block_id = row.get("block_id")
                if block_id in self._blocks:
                    self._blocks[block_id]["finalized_in_pow"] = True
            self._flush()

    def get_receipt(self, txid: str) -> dict[str, Any] | None:
        with self._lock:
            self._prune_locked()
            row = self._tx_index.get(txid)
            if row is None:
                return None
            block = self._blocks.get(str(row.get("block_id")) or "", {})
            out = dict(row)
            out["txid"] = txid
            out["block"] = dict(block) if block else None
            return out

    def get_block(self, block_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._prune_locked()
            blk = self._blocks.get(block_id)
            return dict(blk) if blk else None


_singleton: Optional[InstantTxService] = None


def set_instant_tx_service_singleton(svc: InstantTxService) -> None:
    global _singleton
    _singleton = svc


def get_instant_tx_service_singleton() -> Optional[InstantTxService]:
    return _singleton


def instant_enabled() -> bool:
    return os.environ.get("ANIMICA_INSTANT_TXBLOCK_ENABLED", "1").lower() not in {
        "0",
        "false",
        "off",
    }
