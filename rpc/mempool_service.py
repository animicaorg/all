from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from rpc import deps
from mempool.tx_hash import normalized_tx_bytes, tx_hash_hex as _tx_hash_hex
from core.utils.tx import normalize_tx_envelope, TxNormalizationError
from core.utils.address_codec import account_key_from_pubkey, account_key_from_raw, AccountKeyError
from mempool.config import MempoolConfig, load_config as load_mempool_config
from mempool.errors import (
    AdmissionError,
    Expired,
    FeeTooLow,
    InsufficientFundsPending,
    NonceGap,
    NonceTooLow,
    NotYetValid,
    PersistenceFailed,
    Replay,
    ReplacementUnsupported,
)
from mempool.pool import Pool, PoolConfig
from mempool.select import PendingTxEntry, select_for_block
from mempool.types import EffectiveFee, PoolTx, TxMeta
from mempool.accounting import estimate_max_spend

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


def _sender_from_signature(tx: Any) -> Optional[bytes]:
    sig = None
    if hasattr(tx, "sigs"):
        sigs = getattr(tx, "sigs", None)
        if isinstance(sigs, (list, tuple)) and sigs:
            sig = sigs[0]
    if sig is None and isinstance(tx, dict):
        sigs = tx.get("sigs")
        if isinstance(sigs, list) and sigs:
            sig = sigs[0]
        elif "sig" in tx:
            sig = tx.get("sig")
        elif "signature" in tx:
            sig = tx.get("signature")

    if sig is None:
        return None

    if isinstance(sig, dict):
        alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId")
        pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    else:
        alg_id = getattr(sig, "alg_id", getattr(sig, "alg", None))
        pubkey = getattr(sig, "pubkey", getattr(sig, "pub", None))

    if pubkey is None:
        return None
    if isinstance(pubkey, str):
        if pubkey.startswith("0x"):
            try:
                pubkey = bytes.fromhex(pubkey[2:])
            except ValueError:
                return None
        else:
            return None
    try:
        return account_key_from_pubkey(bytes(pubkey), int(alg_id) if alg_id is not None else None)
    except (AccountKeyError, ValueError, TypeError):
        return None


def _sender_from_payload(tx: Any) -> Optional[bytes]:
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
        try:
            return account_key_from_raw(bytes(sender))
        except AccountKeyError:
            return None
    return None


def _sender_bytes(tx: Any) -> Optional[bytes]:
    sender_sig = _sender_from_signature(tx)
    sender_payload = _sender_from_payload(tx)
    return sender_sig or sender_payload


def _sender_hex(sender: Optional[bytes]) -> str:
    if not sender:
        return "0x"
    return "0x" + bytes(sender).hex()


def _tx_valid_after(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    valid_after = None
    if isinstance(body, dict):
        if "validAfter" in body:
            valid_after = body.get("validAfter")
        elif "valid_after" in body:
            valid_after = body.get("valid_after")
    if valid_after is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            valid_after = getattr(unsigned, "valid_after", None)
    if valid_after is None:
        valid_after = getattr(tx, "valid_after", None)
    try:
        return int(valid_after) if valid_after is not None else None
    except Exception:
        return None


def _tx_valid_until(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    valid_until = None
    if isinstance(body, dict):
        if "validUntil" in body:
            valid_until = body.get("validUntil")
        elif "valid_until" in body:
            valid_until = body.get("valid_until")
    if valid_until is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            valid_until = getattr(unsigned, "valid_until", None)
    if valid_until is None:
        valid_until = getattr(tx, "valid_until", None)
    try:
        return int(valid_until) if valid_until is not None else None
    except Exception:
        return None


def _tx_nonce(tx: Any) -> Optional[int]:
    body = _tx_body(tx)
    nonce = None
    if isinstance(body, dict):
        nonce = body.get("nonce")
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


def _tx_salt(tx: Any) -> Optional[bytes]:
    body = _tx_body(tx)
    salt = None
    if isinstance(body, dict):
        salt = body.get("salt")
    if salt is None:
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            salt = getattr(unsigned, "salt", None)
    if salt is None:
        salt = getattr(tx, "salt", None)
    if isinstance(salt, (bytes, bytearray)):
        return bytes(salt)
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


def _current_height() -> int:
    try:
        ctx = deps.get_ctx()
    except Exception:
        return 0
    head = None
    try:
        getter = getattr(ctx, "get_head", None)
        if callable(getter):
            head = getter()
    except Exception:
        head = None
    if isinstance(head, dict):
        height = head.get("height")
        if height is not None:
            try:
                return int(height)
            except Exception:
                return 0
    return 0


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
        self._replay_window_blocks = int(
            os.getenv("ANIMICA_REPLAY_WINDOW_BLOCKS", "200") or 200
        )
        self._recent_txids: dict[str, int] = {}
        # Per-sender admission locks to serialize balance/pending accounting
        self._sender_locks: dict[str, threading.RLock] = {}
        self._sender_locks_lock = threading.RLock()
        # Optional P2P broadcast callback - set by P2P service to trigger tx propagation
        self._p2p_broadcast_callback: Optional[Any] = None
        if self._persist_enabled:
            self._load_persisted()
    
    def set_p2p_broadcast_callback(self, callback: Any) -> None:
        """Set callback to trigger P2P broadcast when tx is accepted to mempool."""
        self._p2p_broadcast_callback = callback

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

    def _prune_recent_txids(self, current_height: int) -> None:
        if not self._recent_txids:
            return
        expired = [h for h, until in self._recent_txids.items() if until < current_height]
        for h in expired:
            self._recent_txids.pop(h, None)

    def get_rejection(self, tx_hash_hex: str) -> dict[str, Any] | None:
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        self._prune_rejections()
        return self._last_rejections.get(tx_hash_hex)

    def _confirmed_nonce(self, sender_bytes: bytes) -> int | None:
        if self.state_db is None:
            return None
        if hasattr(self.state_db, "get_nonce"):
            try:
                return int(self.state_db.get_nonce(sender_bytes))
            except Exception:
                pass
        if hasattr(self.state_db, "get_account"):
            try:
                acct = self.state_db.get_account(sender_bytes)
            except Exception:
                acct = None
            if acct is not None:
                if hasattr(acct, "nonce"):
                    return int(acct.nonce)
                if isinstance(acct, dict) and "nonce" in acct:
                    return int(acct["nonce"])
        if hasattr(self.state_db, "get"):
            try:
                acct = self.state_db.get(sender_bytes)
            except Exception:
                acct = None
            if acct is not None:
                if hasattr(acct, "nonce"):
                    return int(acct.nonce)
                if isinstance(acct, dict) and "nonce" in acct:
                    return int(acct["nonce"])
        return None

    def _pending_entries_by_sender(self, sender_hex: str) -> list[Any]:
        try:
            return list(self.pool.index.get_by_sender(sender_hex))
        except Exception:
            return []

    def pending_nonces(self, sender_bytes: bytes) -> set[int]:
        sender_hex = _sender_hex(sender_bytes)
        pending = set()
        for entry in self._pending_entries_by_sender(sender_hex):
            nonce = getattr(entry.meta, "nonce", None)
            if nonce is None:
                tx_obj = getattr(entry.tx, "tx", entry.tx)
                nonce = _tx_nonce(tx_obj)
            if nonce is None:
                continue
            pending.add(int(nonce))
        return pending

    def pending_nonce(self, sender_bytes: bytes, confirmed_nonce: int | None = None) -> int | None:
        pending = self.pending_nonces(sender_bytes)
        if not pending:
            return None
        highest_pending = max(pending)
        if confirmed_nonce is None:
            return highest_pending + 1
        return max(int(confirmed_nonce), highest_pending + 1)

    def get_next_nonce(self, sender_bytes: bytes, confirmed_nonce: int) -> int:
        pending_next = self.pending_nonce(sender_bytes, confirmed_nonce)
        return int(pending_next) if pending_next is not None else int(confirmed_nonce)

    def _pending_by_nonce(self, sender_hex: str) -> dict[int, str]:
        by_nonce: dict[int, str] = {}
        for entry in self._pending_entries_by_sender(sender_hex):
            tx_obj = getattr(entry.tx, "tx", entry.tx)
            nonce = getattr(entry.meta, "nonce", None)
            if nonce is None:
                nonce = _tx_nonce(tx_obj)
            if nonce is None:
                continue
            tx_hash = getattr(entry, "tx_hash", None) or getattr(entry.tx, "tx_hash", None)
            if tx_hash is None:
                continue
            by_nonce[int(nonce)] = "0x" + _normalize_hash_bytes(tx_hash).hex()
        return by_nonce

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
            valid_after = _tx_valid_after(entry.tx)
            valid_until = _tx_valid_until(entry.tx)
            salt = _tx_salt(entry.tx)
            gas_limit = _tx_gas_limit(entry.tx)
            fee = EffectiveFee.from_tx(entry.tx)
            entries.append(
                {
                    "hash": entry.hash_hex,
                    "raw": raw.hex(),
                    "first_seen": first_seen,
                    "expires_at": expires_at,
                    "sender": sender,
                    "valid_after": int(valid_after) if valid_after is not None else None,
                    "valid_until": int(valid_until) if valid_until is not None else None,
                    "salt": "0x" + bytes(salt).hex() if salt is not None else None,
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
        bad_entries: list[str] = []
        self._restoring = True
        now = time.time()
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                bad_entries.append(line)
                continue
            if entry.get("chain_id") not in (None, self.chain_id, int(self.chain_id)):
                continue
            expires_at = entry.get("expires_at")
            if expires_at is not None and float(expires_at) <= now:
                continue
            raw_hex = entry.get("raw") or ""
            if not isinstance(raw_hex, str) or not raw_hex:
                bad_entries.append(line)
                continue
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError:
                bad_entries.append(line)
                continue
            if not raw:
                bad_entries.append(line)
                continue
            try:
                normalized_env = normalize_tx_envelope(raw)
                raw = normalized_env.get("raw") or raw
            except TxNormalizationError:
                bad_entries.append(line)
                continue
            try:
                tx_obj = (
                    Tx.from_cbor(raw)  # type: ignore[attr-defined]
                    if hasattr(Tx, "from_cbor")
                    else None
                )
            except Exception:
                bad_entries.append(line)
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
        if bad_entries and self._persist_path is not None:
            quarantine = self._persist_path.with_suffix(".bad.jsonl")
            try:
                quarantine.write_text("\n".join(bad_entries) + "\n", encoding="utf-8")
            except Exception:
                pass
            log.warning(
                "Dropped invalid persisted mempool entries",
                extra={"count": len(bad_entries), "quarantine": str(quarantine)},
            )
        if restored or bad_entries:
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
            raw_bytes = normalized_tx_bytes(raw)
        except Exception as exc:
            if tx_hash_hex is None:
                try:
                    tx_hash_hex = _tx_hash_hex(bytes(raw))
                except Exception:
                    tx_hash_hex = "0x" + hashlib.sha3_256(bytes(raw)).hexdigest()
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
            tx_hash_hex = _tx_hash_hex(raw_bytes)
        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
        tx_hash_bytes = _normalize_hash_bytes(tx_hash_hex)
        expected_hash = _tx_hash_hex(raw_bytes)
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

        try:
            normalized_env = normalize_tx_envelope(raw_bytes)
        except TxNormalizationError as exc:
            self._record_rejection(
                tx_hash_hex,
                exc.reason or "decode_error",
                {"step": "normalize_envelope", "error": str(exc)},
            )
            raise AdmissionError(
                "tx envelope normalization failed",
                context={"tx_hash": tx_hash_hex, "error": str(exc)},
            ) from exc

        env_hash = normalized_env.get("hash")
        if isinstance(env_hash, str) and env_hash.lower() != tx_hash_hex:
            self._record_rejection(
                tx_hash_hex,
                "hash_mismatch",
                {"expected": tx_hash_hex, "got": env_hash},
            )
            raise AdmissionError(
                "tx hash mismatch after normalization",
                context={"tx_hash": tx_hash_hex, "expected": tx_hash_hex, "got": env_hash},
            )

        current_height = _current_height()
        self._prune_recent_txids(current_height)

        if isinstance(tx, dict):
            try:
                raw_from_dict = normalized_tx_bytes(tx)
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

        sender_sig = _sender_from_signature(tx)
        sender_payload = _sender_from_payload(tx)
        sender = sender_sig or sender_payload
        env_sender = None
        if isinstance(normalized_env.get("tx"), dict):
            env_sender = normalized_env["tx"].get("from")
        if isinstance(env_sender, str) and env_sender.startswith("0x"):
            try:
                env_sender = bytes.fromhex(env_sender[2:])
            except ValueError:
                env_sender = None
        if isinstance(env_sender, (bytes, bytearray)):
            sender = bytes(env_sender)
        if sender_sig is not None and sender is not None and sender_sig != sender:
            self._record_rejection(
                tx_hash_hex,
                "sender_mismatch",
                {"sig_sender": _sender_hex(sender_sig), "payload_sender": _sender_hex(sender)},
            )
            raise AdmissionError(
                "sender mismatch between signature and payload",
                context={"tx_hash": tx_hash_hex},
            )
        if sender is None:
            self._record_rejection(
                tx_hash_hex,
                "missing_sender",
                {"sender": _sender_hex(sender)},
            )
            raise AdmissionError(
                "missing sender",
                context={"tx_hash": tx_hash_hex},
            )

        sender_hex = _sender_hex(sender)

        try:
            if hasattr(Tx, "from_obj"):
                Tx.from_obj(  # type: ignore[attr-defined]
                    {"tx": normalized_env.get("tx"), "sigs": normalized_env.get("sigs", [])}
                )
            elif hasattr(Tx, "from_cbor"):
                Tx.from_cbor(raw_bytes)  # type: ignore[attr-defined]
        except Exception as exc:
            self._record_rejection(
                tx_hash_hex,
                "decode_error",
                {"step": "decode_envelope", "error": str(exc)},
            )
            raise AdmissionError(
                "tx envelope could not be decoded",
                context={"tx_hash": tx_hash_hex, "error": str(exc)},
            ) from exc

        if self.tx_index is not None and hasattr(self.tx_index, "exists"):
            try:
                if self.tx_index.exists(tx_hash_bytes):
                    self._record_rejection(
                        tx_hash_hex,
                        "replay",
                        {"tx_hash": tx_hash_hex},
                    )
                    raise Replay(tx_hash=tx_hash_hex, sender=sender_hex)
            except Replay:
                raise
            except Exception:
                pass

        if tx_hash_hex in self._recent_txids:
            self._record_rejection(
                tx_hash_hex,
                "replay",
                {"tx_hash": tx_hash_hex},
            )
            raise Replay(tx_hash=tx_hash_hex, sender=sender_hex)

        # Acquire per-sender lock to prevent TOCTOU race between getNextNonce and admission
        sender_lock = self._get_sender_lock(sender_hex)

        with sender_lock:
            tx_for_meta = normalized_env if isinstance(tx, dict) else tx
            valid_after = _tx_valid_after(tx_for_meta)
            valid_until = _tx_valid_until(tx_for_meta)
            salt = _tx_salt(tx_for_meta)
            tx_version = 1
            if isinstance(normalized_env.get("tx"), dict):
                try:
                    tx_version = int(normalized_env["tx"].get("v", 1))
                except Exception:
                    tx_version = 1

            if tx_version == 2 and (valid_after is None or valid_until is None or salt is None):
                missing = []
                if valid_after is None:
                    missing.append("validAfter")
                if valid_until is None:
                    missing.append("validUntil")
                if salt is None:
                    missing.append("salt")
                self._record_rejection(
                    tx_hash_hex,
                    "missing_validity_window",
                    {
                        "valid_after": valid_after,
                        "valid_until": valid_until,
                        "missing": missing,
                        "expected_location": "tx.body",
                    },
                )
                raise AdmissionError(
                    "missing validity window fields",
                    context={
                        "tx_hash": tx_hash_hex,
                        "sender": sender_hex,
                        "nonce": normalized_env.get("nonce"),
                        "missing": missing,
                        "expected_location": "tx.body",
                    },
                )

            nonce = normalized_env.get("nonce")
            if tx_version == 1:
                if nonce is None:
                    self._record_rejection(
                        tx_hash_hex,
                        "missing_nonce",
                        {"sender": sender_hex},
                    )
                    raise AdmissionError(
                        "missing nonce",
                        context={"tx_hash": tx_hash_hex, "sender": sender_hex},
                    )

                confirmed_nonce = self._confirmed_nonce(sender)
                expected_nonce = self.get_next_nonce(sender, confirmed_nonce or 0)
                pending_by_nonce = self._pending_by_nonce(sender_hex)
                if nonce in pending_by_nonce:
                    existing_hash = pending_by_nonce[nonce]
                    if existing_hash == tx_hash_hex:
                        return tx_hash_hex
                    self._record_rejection(
                        tx_hash_hex,
                        "replacement_unsupported",
                        {
                            "sender": sender_hex,
                            "nonce": int(nonce),
                            "existing_tx_hash": existing_hash,
                            "expected_nonce": expected_nonce,
                        },
                    )
                    raise ReplacementUnsupported(
                        sender=sender_hex,
                        nonce=int(nonce),
                        tx_hash_new=tx_hash_hex,
                        tx_hash_old=existing_hash,
                    )

                if nonce < expected_nonce:
                    self._record_rejection(
                        tx_hash_hex,
                        "nonce_too_low",
                        {
                            "expected": expected_nonce,
                            "got": int(nonce),
                            "confirmed": confirmed_nonce,
                        },
                    )
                    raise NonceTooLow(
                        expected_nonce=int(expected_nonce),
                        got_nonce=int(nonce),
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )

                if nonce > expected_nonce:
                    self._record_rejection(
                        tx_hash_hex,
                        "nonce_gap",
                        {
                            "expected": expected_nonce,
                            "got": int(nonce),
                            "confirmed": confirmed_nonce,
                        },
                    )
                    raise NonceGap(
                        expected_nonce=int(expected_nonce),
                        got_nonce=int(nonce),
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )
            else:
                nonce = None

            if tx_version == 2 and valid_after is not None and valid_until is not None:
                max_ttl_blocks = int(os.getenv("ANIMICA_MAX_TX_TTL_BLOCKS", "200") or 200)
                if valid_until - valid_after > max_ttl_blocks:
                    self._record_rejection(
                        tx_hash_hex,
                        "ttl_too_long",
                        {"valid_after": valid_after, "valid_until": valid_until},
                    )
                    raise AdmissionError(
                        "validity window exceeds maximum TTL",
                        context={"tx_hash": tx_hash_hex, "max_ttl_blocks": max_ttl_blocks},
                    )

                current_height = _current_height()
                if current_height < valid_after:
                    self._record_rejection(
                        tx_hash_hex,
                        "not_yet_valid",
                        {"valid_after": valid_after, "current_height": current_height},
                    )
                    raise NotYetValid(
                        valid_after=valid_after,
                        current_height=current_height,
                        sender=sender_hex,
                        tx_hash=tx_hash_hex,
                    )
                if current_height > valid_until:
                    self._record_rejection(
                        tx_hash_hex,
                        "expired",
                        {"valid_until": valid_until, "current_height": current_height},
                    )
                    raise Expired(
                        valid_until=valid_until,
                        current_height=current_height,
                        sender=sender_hex,
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
                    sender=sender_hex,
                )

            # Pending debit accounting (per-sender)
            if self.state_db is not None and hasattr(self.state_db, "get_balance"):
                try:
                    balance = int(self.state_db.get_balance(sender))
                except Exception:
                    balance = None
                if balance is not None:
                    try:
                        new_spend = int(estimate_max_spend(tx).total_max_spend)
                    except Exception:
                        new_spend = 0

                    pending_entries = []
                    try:
                        pending_entries = list(self.pool.index.get_by_sender(sender_hex))
                    except Exception:
                        pending_entries = []

                    pending_spend = 0
                    pending_by_fee: list[tuple[int, bytes, int]] = []
                    for entry in pending_entries:
                        try:
                            spend = int(estimate_max_spend(entry.tx).total_max_spend)
                        except Exception:
                            spend = 0
                        pending_spend += spend
                        fee_val = int(getattr(entry.meta, "effective_fee_wei", 0))
                        pending_by_fee.append((fee_val, entry.tx_hash, spend))

                    available = balance - pending_spend
                    if available < new_spend:
                        pending_by_fee.sort(key=lambda item: (item[0], item[1].hex()))
                        for fee_val, tx_hash_bytes, spend in pending_by_fee:
                            if available >= new_spend:
                                break
                            if offered <= fee_val:
                                break
                            self.pool.remove_included([tx_hash_bytes])
                            pending_spend -= spend
                            available = balance - pending_spend

                        if available < new_spend:
                            self._record_rejection(
                                tx_hash_hex,
                                "insufficient_funds_pending",
                                {"required": new_spend, "available": max(0, available)},
                            )
                            raise InsufficientFundsPending(
                                sender=sender_hex,
                                tx_hash=tx_hash_hex,
                                required=new_spend,
                                available=max(0, available),
                            )

            tx_to_store: Any = tx
            if not isinstance(tx, Tx):
                try:
                    if hasattr(Tx, "from_cbor"):
                        tx_to_store = Tx.from_cbor(raw_bytes)  # type: ignore[attr-defined]
                except Exception:
                    tx_to_store = tx

            meta = TxMeta(
                sender=sender_hex,
                nonce=int(nonce) if nonce is not None else None,
                gas_limit=gas_limit,
                size_bytes=len(raw_bytes),
                first_seen=time.time(),
                local=local,
                effective_fee_wei=offered,
                origin=origin_label,
                peer_id=origin_peer,
                valid_after=valid_after,
                valid_until=valid_until,
                salt=salt,
            )
            pool_tx = PoolTx(
                tx=tx_to_store,
                tx_hash=tx_hash_bytes,
                raw=raw_bytes,
                meta=meta,
                fee=fee,
            )
            
            log.info(
                "MempoolService.submit: calling pool.add(), tx_hash=%s, sender=%s",
                tx_hash_hex,
                meta.sender,
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

        if not self._restoring:
            try:
                self._persist_snapshot()
            except Exception as exc:
                try:
                    self.pool.remove_included([tx_hash_bytes])
                except Exception:
                    pass
                self._record_rejection(
                    tx_hash_hex,
                    "persistence_failed",
                    {"tx_hash": tx_hash_hex, "error": str(exc)},
                )
                raise PersistenceFailed(tx_hash=tx_hash_hex, error=str(exc)) from exc

        self._recent_txids[tx_hash_hex] = current_height + self._replay_window_blocks

        log.info(
            "MempoolService.submit: SUCCESS - tx added and persisted, tx_hash=%s, pool_size=%d",
            tx_hash_hex,
            len(self.pool),
        )
        log.info(
            "mempool.accepted",
            extra={
                "hash": tx_hash_hex,
                "from": meta.sender,
                "origin": origin_label,
                "peer": origin_peer,
            },
        )
        
        # Trigger P2P broadcast for local txs (best-effort, non-blocking)
        if local and self._p2p_broadcast_callback is not None:
            try:
                import asyncio
                callback = self._p2p_broadcast_callback
                # Try to schedule in the event loop without blocking
                try:
                    loop = asyncio.get_running_loop()
                    # We're in a sync context but want to trigger async broadcast
                    asyncio.ensure_future(callback(tx_hash_bytes, raw_bytes), loop=loop)
                    log.debug(
                        "P2P broadcast scheduled for tx",
                        extra={"tx_hash": tx_hash_hex, "trigger": "mempool_submit"}
                    )
                except RuntimeError:
                    # No running loop; callback will need to handle this
                    log.debug(
                        "No running event loop for P2P broadcast",
                        extra={"tx_hash": tx_hash_hex}
                    )
            except Exception as e:
                log.debug(
                    "P2P broadcast callback failed (non-fatal)",
                    extra={"tx_hash": tx_hash_hex, "error": str(e)}
                )
        
        return tx_hash_hex

    def submit_atomic(
        self,
        *,
        tx: Any,
        raw: bytes,
        tx_hash_hex: str | None = None,
        local: bool = True,
        origin_peer: str | None = None,
    ) -> tuple[bool, str | None, str]:
        """
        Atomically submit a transaction and report admission status.

        Returns (accepted, reason, tx_hash_hex). On rejection, reason is a
        short string suitable for RPC error surfaces.
        """
        computed_hash = tx_hash_hex
        if computed_hash is None:
            try:
                computed_hash = _tx_hash_hex(raw)
            except Exception:
                computed_hash = "0x" + bytes(raw).hex()

        try:
            admitted_hash = self.submit(
                tx=tx,
                raw=raw,
                tx_hash_hex=computed_hash,
                local=local,
                origin_peer=origin_peer,
            )
        except Exception as exc:
            return False, str(exc), computed_hash

        if not self.has_hash(admitted_hash):
            return False, "pool_missing", admitted_hash

        return True, None, admitted_hash

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
            head_state={"chain_id": self.chain_id, "height": _current_height()},
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

    
    def _get_sender_lock(self, sender_hex: str) -> threading.RLock:
        """
        Get or create a lock for a specific sender to prevent TOCTOU races.
        
        Args:
            sender_hex: The sender address as hex string
            
        Returns:
            A reentrant lock for this sender
        """
        with self._sender_locks_lock:
            if sender_hex not in self._sender_locks:
                self._sender_locks[sender_hex] = threading.RLock()
            return self._sender_locks[sender_hex]

    def diagnose(self, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
        snapshot = self.snapshot(limit=limit)
        if not snapshot.entries:
            return {}

        selection = select_for_block(
            head_state={"chain_id": self.chain_id, "height": _current_height()},
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

    async def admit_tx(
        self,
        raw: bytes,
        local: bool | None = False,
        origin_peer: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Async wrapper for submit() to be used by P2P TX relay.
        
        Args:
            raw: Raw transaction bytes (canonical CBOR)
            local: Whether this is a locally-submitted tx
            origin_peer: Peer ID that provided the tx (if not local)
        
        Returns:
            (accepted, reason) tuple
            - accepted: True if tx was admitted to mempool
            - reason: None if accepted, error code/message if rejected
        """
        try:
            # Parse tx structure from raw bytes
            from core.encoding.cbor import loads as cbor_loads
            
            try:
                tx_obj = cbor_loads(raw)
            except Exception as exc:
                return False, f"cbor_decode_error:{type(exc).__name__}"
            
            # Submit to mempool (synchronous)
            tx_hash = self.submit(
                tx=tx_obj,
                raw=raw,
                tx_hash_hex=None,
                local=bool(local),
                origin_peer=origin_peer,
            )
            
            # If submit succeeded, it returns the hash
            if tx_hash:
                return True, None
            else:
                return False, "submit_failed"
        
        except AdmissionError as exc:
            # Structured admission errors
            reason = str(exc)
            if hasattr(exc, "context"):
                ctx = getattr(exc, "context")
                if isinstance(ctx, dict) and "tx_hash" in ctx:
                    # Extract specific error reasons
                    for key in ["reason", "error", "message"]:
                        if key in ctx:
                            return False, str(ctx[key])
            return False, reason
        
        except Exception as exc:
            # Unexpected errors
            log.error(
                "Unexpected error in admit_tx",
                extra={"error": str(exc), "error_type": type(exc).__name__},
                exc_info=True,
            )
            return False, f"unexpected_error:{type(exc).__name__}"

    def stats(self) -> dict[str, t.Any]:
        """Get mempool statistics."""
        return {
            "pending_count": len(self.pool),
            "pending_bytes": sum(
                len(getattr(getattr(entry, "tx", entry), "raw", b""))
                for entry in (self.pool.entries() if hasattr(self.pool, "entries") else [])
            ),
        }


# Global singleton for easy access from RPC methods
_mempool_service_singleton: MempoolService | None = None


def set_mempool_service_singleton(svc: MempoolService) -> None:
    """Set the global mempool service singleton."""
    global _mempool_service_singleton
    _mempool_service_singleton = svc


def get_mempool_service_singleton() -> MempoolService | None:
    """Get the global mempool service singleton."""
    return _mempool_service_singleton


__all__ = ["MempoolService", "MempoolSnapshot", "set_mempool_service_singleton", "get_mempool_service_singleton"]
