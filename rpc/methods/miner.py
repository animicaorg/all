from __future__ import annotations

import asyncio
import contextlib
import inspect
import hashlib
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, replace
from typing import Any, Callable, Dict, Optional, Tuple

from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx
from core.utils.merkle import merkle_root
from core.utils.tx import TxNormalizationError, normalize_tx, normalize_tx_bytes, normalize_tx_envelope
from mining.adapters.core_chain import CoreChainAdapter
import p2p
from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method
from mempool.tx_hash import tx_hash_bytes as _tx_hash_bytes
from mempool.select import PendingTxEntry, select_for_block

try:  # Optional helper to compute share target from Θ
    from consensus.difficulty import share_microtarget
except Exception:  # pragma: no cover
    share_microtarget = None  # type: ignore[assignment]

# Import THETA_HARD_CAP_MICRO separately to avoid shadowing in function scope
try:
    from consensus.difficulty import THETA_HARD_CAP_MICRO
except Exception:  # pragma: no cover
    THETA_HARD_CAP_MICRO = 3_000_000_000  # Fallback if import fails

try:  # canonical zero constant
    from core.types.hash import ZERO32
except Exception:  # pragma: no cover
    ZERO32 = b"\x00" * 32  # type: ignore[assignment]

# Fallback Θ (µ-nats) if nothing else is available
# Lowered from 3000000 (3.0 nats) to 1000000 (1.0 nat) for easier local/devnet mining
_DEFAULT_THETA_MICRO = int(os.getenv("ANIMICA_DEFAULT_THETA_MICRO", "1000000"))
_DEFAULT_SHARE_TARGET = float(os.getenv("ANIMICA_DEFAULT_SHARE_TARGET", "0.01"))
_DEFAULT_SHA256_BITS = os.getenv("ANIMICA_SHA256_NBITS", "1d00ffff")

# Job cache timeout: how long to keep mining jobs before marking them stale
# Should be at least as long as ANIMICA_MAX_BLOCK_TIME_S to avoid premature eviction
# Default: 3600 seconds (1 hour) - aligns with max block time
_JOB_CACHE_TIMEOUT_S = float(os.getenv("ANIMICA_JOB_CACHE_TIMEOUT_S", "3600"))

log = logging.getLogger("animica.rpc.miner")

# Constants for address and gas calculations
ADDRESS_LEN = 32  # Animica address length (32-byte digest, matches core/types/tx.py)
RECEIPT_ADDRESS_LEN = 32  # Receipt log address length (bytes)
TOPIC_LEN = 32  # Receipt log topic length (bytes)
INTRINSIC_GAS_TRANSFER = 21_000  # Intrinsic gas cost for simple transfers

# Logging display constants
MAX_DISPLAYED_TX_HASHES = 3  # Maximum number of transaction hashes to display in logs

# Mempool drain limits for block building
DEFAULT_BLOCK_GAS_LIMIT = 100_000_000_000  # 100 billion gas (very high limit for devnet)
DEFAULT_BLOCK_BYTE_LIMIT = 1_000_000_000  # 1GB block size limit

# Receipt index prefix (matches PFX_RXI from core/db/block_db.py)
# Used for re-indexing receipts with canonical tx hashes
PFX_RXI = b"\x22"

# Default gas limit for transactions when not specified (same as INTRINSIC_GAS_TRANSFER)
DEFAULT_TX_GAS_LIMIT = INTRINSIC_GAS_TRANSFER  # 21,000 gas for simple transfers

# In-memory job cache for miner.getWork / miner.submitWork flows
_JOB_CACHE: dict[str, dict[str, Any]] = {}
_LOCAL_HEAD: dict[str, Any] = {}
_HEAD_STATE: dict[str, Any] = {"height": None, "hash": None, "generation": 0}
_AUTO_MINE: bool = False
_AUTO_TASK: asyncio.Task | None = None

# Dynamic theta adjustment state for mining operations
# Tracks timing of recent blocks to adapt difficulty
_MINING_STATE: dict[str, Any] = {
    "last_block_time": None,  # timestamp of last mined block
    "block_times": [],         # Recent block intervals (seconds) for EMA calculation
    "theta_state": None,       # RetargetState from consensus.difficulty
    "adjustment_enabled": True, # Whether to dynamically adjust theta during mining
    "last_network_height": None,  # last observed chain head height
    "last_network_timestamp": None,  # last observed chain head timestamp
}

# Hash tracking map for transactions from adapter and fallback pending cache
# Maps id(tx_obj) -> (tx_hash_hex, raw_bytes)
# Used to track original hashes (from _FALLBACK_PENDING dict keys) when evicting from mempool
# This is necessary because Tx dataclasses are frozen and we can't store the hash as an attribute
#
# NOTE: Using id(tx_obj) as key has a small collision risk if Python reuses IDs after GC,
# but this is acceptable because:
# 1. The map is short-lived (only during mining)
# 2. Entries are cleaned up immediately after use (success or failure)
# 3. Collision would only cause fallback to txid_bytes() computation (safe degradation)
#
# Thread safety: This global is not thread-safe, but it's acceptable because:
# 1. RPC methods are called sequentially within the same FastAPI worker process
# 2. Mining operations (_mine_once) are synchronous and complete before next RPC call
# 3. If concurrent mining is needed in the future, add threading.Lock
_TX_HASH_MAP: dict[int, tuple[str, bytes]] = {}

# Block template cache for submit binding (template_id -> metadata)
_TEMPLATE_CACHE: dict[str, dict[str, Any]] = {}
_TEMPLATE_TTL_S = float(os.getenv("ANIMICA_TEMPLATE_TTL_S", "30"))
_HEAD_RW_LOCK = threading.RLock()
_TEMPLATE_CACHE_LOCK = threading.RLock()
_METRICS: dict[str, int] = {
    "templates_issued": 0,
    "templates_submitted": 0,
    "stale_rejects": 0,
    "imports_ok": 0,
    "imports_failed": 0,
    "head_advances": 0,
}
_LAST_PROGRESS_TS = time.time()
_MEMPOOL_DEBUG = os.getenv("ANIMICA_MEMPOOL_DEBUG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_MINER_DEBUG = os.getenv("ANIMICA_MINER_DEBUG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_MEMPOOL_BINDINGS_LOGGED: set[str] = set()

# Mining audit trail (in-memory)
# Tracks blocks mined locally for debugging and verification
# Structure: list of {height, hash, parent_hash, miner_address, expected_reward, credited_reward, state_root, timestamp}
_MINING_AUDIT_TRAIL: list[dict[str, Any]] = []
_MINING_AUDIT_MAX_SIZE = int(os.getenv("ANIMICA_MINING_AUDIT_MAX_SIZE", "1000"))

# Minimum theta for forced blocks (when previous block exceeds max_block_time_s)
# This value matches theta_min_micro used in _adjust_theta_for_mining() for consistency
# 100K µ-nats = 0.1 nats = very easy mining (allows quick block production)
_FORCED_BLOCK_MIN_THETA_MICRO = 100_000
_WATCHDOG_INTERVAL_S = float(os.getenv("ANIMICA_MINER_WATCHDOG_INTERVAL_S", "30"))
_WATCHDOG_STALL_S = float(os.getenv("ANIMICA_MINER_WATCHDOG_STALL_S", "120"))
_WATCHDOG_MAX_ATTEMPTS = int(os.getenv("ANIMICA_MINER_WATCHDOG_MAX_ATTEMPTS", "2"))
_WATCHDOG_RESTART_NODE = os.getenv("ANIMICA_MINER_WATCHDOG_RESTART_NODE", "").lower() in {"1", "true", "yes", "on"}
_WATCHDOG_TASK: asyncio.Task | None = None
_WATCHDOG_ATTEMPTS = 0


def _metric_inc(name: str, delta: int = 1) -> None:
    _METRICS[name] = int(_METRICS.get(name, 0)) + int(delta)


def _mark_head_progress() -> None:
    global _LAST_PROGRESS_TS
    _LAST_PROGRESS_TS = time.time()


async def _watchdog_loop() -> None:
    global _WATCHDOG_ATTEMPTS
    while _AUTO_MINE:
        await asyncio.sleep(max(1.0, _WATCHDOG_INTERVAL_S))
        stalled_for = time.time() - float(_LAST_PROGRESS_TS)
        if stalled_for < _WATCHDOG_STALL_S:
            _WATCHDOG_ATTEMPTS = 0
            continue
        _WATCHDOG_ATTEMPTS += 1
        log.warning(
            "stall_detected",
            extra={
                "stalled_for_s": int(stalled_for),
                "attempt": _WATCHDOG_ATTEMPTS,
                "metrics": dict(_METRICS),
            },
        )
        _prune_template_cache()
        if _WATCHDOG_ATTEMPTS <= _WATCHDOG_MAX_ATTEMPTS:
            continue
        if _WATCHDOG_RESTART_NODE:
            log.error("watchdog_restart_requested", extra={"attempt": _WATCHDOG_ATTEMPTS})
        _WATCHDOG_ATTEMPTS = 0


def _record_mining_audit(
    height: int,
    block_hash: bytes,
    parent_hash: bytes,
    miner_address: bytes,
    expected_reward: int,
    credited_reward: int,
    state_root: bytes,
) -> None:
    """
    Record a mining operation in the audit trail.
    
    Args:
        height: Block height
        block_hash: Block hash
        parent_hash: Parent block hash
        miner_address: Miner/coinbase address
        expected_reward: Expected reward amount (from consensus rules)
        credited_reward: Actual credited amount (from state query)
        state_root: State root after block application
    """
    global _MINING_AUDIT_TRAIL
    
    record = {
        "height": height,
        "hash": "0x" + block_hash.hex(),
        "parent_hash": "0x" + parent_hash.hex(),
        "miner_address": "0x" + miner_address.hex(),
        "expected_reward": expected_reward,
        "credited_reward": credited_reward,
        "state_root": "0x" + state_root.hex(),
        "timestamp": int(time.time()),
    }
    
    _MINING_AUDIT_TRAIL.append(record)
    
    # Trim to max size (keep most recent)
    if len(_MINING_AUDIT_TRAIL) > _MINING_AUDIT_MAX_SIZE:
        _MINING_AUDIT_TRAIL = _MINING_AUDIT_TRAIL[-_MINING_AUDIT_MAX_SIZE:]
    
    log.debug(f"Mining audit recorded: height={height}, expected={expected_reward}, credited={credited_reward}")



def _tracked(tx: Any) -> tuple[str, bytes] | None:
    """
    Check if tx has a tracked hash in _TX_HASH_MAP.
    
    Returns:
        (tx_hash_hex, raw_bytes) if tracked, None otherwise
    """
    return _TX_HASH_MAP.get(id(tx))


def _resolve_chain_id_for_sig(ctx: Any) -> int:
    chain_id = getattr(getattr(ctx, "cfg", None), "chain_id", None)
    if chain_id is None:
        try:
            chain_id = int(deps.get_chain_id())
        except Exception:
            chain_id = 1
    return int(chain_id)


def _log_mempool_binding(component: str, ctx: Any, mempool_service: Any) -> None:
    try:
        key = f"{component}:{id(mempool_service)}"
        if key in _MEMPOOL_BINDINGS_LOGGED:
            return
        pending_path = getattr(mempool_service, "_persist_path", None)
        chain_id = getattr(getattr(ctx, "cfg", None), "chain_id", None)
        log.info(
            "Mempool binding",
            extra={
                "component": component,
                "chain_id": chain_id,
                "mempool_id": hex(id(mempool_service)),
                "pending_path": str(pending_path) if pending_path else None,
            },
        )
        _MEMPOOL_BINDINGS_LOGGED.add(key)
    except Exception:
        return


def _is_mempool_service(candidate: Any) -> bool:
    if candidate is None:
        return False
    return bool(
        hasattr(candidate, "submit")
        or hasattr(candidate, "admit")
        or hasattr(candidate, "add_raw")
        or hasattr(candidate, "has_hash")
    )


def _resolve_mempool_service(ctx: Any) -> Any | None:
    mempool_service = None
    if ctx is not None:
        candidates = [
            "mempool",
            "mempool_service",
            "mempoolSvc",
            "mempool_manager",
            "mempool_mgr",
            "txpool",
            "tx_pool",
            "pool",
        ]
        for name in candidates:
            svc = getattr(ctx, name, None)
            if svc is None:
                continue
            if _is_mempool_service(svc):
                mempool_service = svc
                break
            inner = getattr(svc, "mempool", None) or getattr(svc, "service", None)
            if _is_mempool_service(inner):
                mempool_service = inner
                break
    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        tx_methods = None  # type: ignore[assignment]

    canonical = None
    if tx_methods is not None and hasattr(tx_methods, "_get_mempool_service"):
        try:
            canonical = tx_methods._get_mempool_service()  # type: ignore[attr-defined]
        except Exception:
            canonical = None

    if mempool_service is None and canonical is not None:
        mempool_service = canonical
        with contextlib.suppress(Exception):
            ctx.mempool = canonical

    if (
        canonical is not None
        and mempool_service is not None
        and canonical is not mempool_service
    ):
        log.error(
            "Mempool service mismatch",
            extra={
                "ctx_mempool_id": hex(id(mempool_service)),
                "canonical_mempool_id": hex(id(canonical)),
            },
        )
        mempool_service = canonical
        with contextlib.suppress(Exception):
            ctx.mempool = canonical

    return mempool_service


def _canonical_txid_hex(tx: Any) -> str:
    """
    Get the canonical txid (hex) from a tx object.
    
    Canonical rule: TxID = sha3_256(raw_cbor_bytes) for the signed envelope.
    This matches the rule used by rpc/methods/tx.py in tx.sendRawTransaction.
    
    Returns:
        Hex string with "0x" prefix, e.g., "0x6e23..."
    """
    # First, try to get the tracked hash (if tx came from _TX_HASH_MAP)
    tracked = _tracked(tx)
    if tracked:
        return tracked[0]
    
    # Fall back to txid_bytes helper
    try:
        tx_hash_bytes = txid_bytes(tx)
        return "0x" + tx_hash_bytes.hex()
    except Exception as e:
        log.warning(f"Failed to compute canonical txid: {e}")
        return "0x" + (b"\x00" * 32).hex()


def _normalize_excluded_reasons(rejected: dict[str, int]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    reason_map = {
        "insufficient_funds": "insufficient_balance",
        "invalid_format": "decode_error",
        "missing_sender": "decode_error",
        "missing_nonce": "decode_error",
    }
    for reason, count in rejected.items():
        mapped_reason = reason_map.get(reason, reason)
        mapped[mapped_reason] = mapped.get(mapped_reason, 0) + int(count)
    return mapped


def _maybe_log_mempool_debug(
    *,
    phase: str,
    pending_total: int,
    candidate_count: int,
    included_count: int,
    rejected: dict[str, int],
    rejected_details_by_hash: dict[str, dict[str, Any]],
) -> None:
    if not _MEMPOOL_DEBUG:
        return
    excluded_by_reason = _normalize_excluded_reasons(rejected)
    excluded_samples = list(rejected_details_by_hash.items())[:10]
    log.info(
        "mempool selection debug",
        extra={
            "phase": phase,
            "mempool_size": pending_total,
            "candidate_count": candidate_count,
            "included_count": included_count,
            "excluded_by_reason": excluded_by_reason,
            "excluded_samples": [
                {"hash": tx_hash, **(detail or {})} for tx_hash, detail in excluded_samples
            ],
        },
    )


def _coerce_selected_txs(
    *,
    selected: list[Any],
    selected_hashes: list[str],
    pending_raw_by_hash: dict[str, bytes],
    decode_fn: Callable[[bytes], Any] | None,
) -> tuple[list[Tx], list[str], dict[str, int], dict[str, str], dict[str, dict[str, Any]]]:
    coerced: list[Tx] = []
    included_hashes: list[str] = []
    dropped_counts: dict[str, int] = {}
    dropped_by_hash: dict[str, str] = {}
    dropped_details: dict[str, dict[str, Any]] = {}

    for tx_obj, hash_hex in zip(selected, selected_hashes):
        hash_hex = _normalize_hash_hex(hash_hex)
        raw = pending_raw_by_hash.get(hash_hex, b"")
        tx: Tx | None = None
        decoded_obj: dict[str, Any] | None = None
        normalize_error: str | None = None
        normalize_reason: str | None = None

        if raw and not isinstance(raw, (bytes, bytearray)):
            try:
                raw = normalize_tx(raw)
            except TxNormalizationError as exc:
                normalize_error = str(exc)
                normalize_reason = exc.reason
                raw = b""
            except Exception as exc:
                normalize_error = str(exc)
                normalize_reason = "decode_error"
                raw = b""

        if isinstance(tx_obj, Tx):
            tx = tx_obj
        elif isinstance(tx_obj, dict):
            decoded_obj = tx_obj
            if raw and decode_fn is not None:
                try:
                    decoded = decode_fn(raw)
                    if isinstance(decoded, tuple):
                        tx_candidate = decoded[0]
                        decoded_obj = decoded[1] if isinstance(decoded[1], dict) else decoded_obj
                    else:
                        tx_candidate = decoded
                        decoded_obj = decoded if isinstance(decoded, dict) else decoded_obj
                    if isinstance(tx_candidate, Tx):
                        tx = tx_candidate
                    elif isinstance(tx_candidate, dict):
                        decoded_obj = tx_candidate
                except Exception as exc:
                    normalize_error = str(exc)
            if not raw:
                try:
                    raw = normalize_tx(tx_obj)
                except Exception as exc:
                    normalize_error = str(exc)
        elif raw and decode_fn is not None:
            decoded = decode_fn(raw)
            if isinstance(decoded, tuple):
                tx_candidate = decoded[0]
                decoded_obj = decoded[1] if isinstance(decoded[1], dict) else None
            else:
                tx_candidate = decoded
                decoded_obj = decoded if isinstance(decoded, dict) else None
            if isinstance(tx_candidate, Tx):
                tx = tx_candidate
            elif isinstance(tx_candidate, dict):
                decoded_obj = tx_candidate

        if tx is None and raw and decode_fn is not None:
            try:
                decoded = decode_fn(raw)
                if isinstance(decoded, tuple):
                    tx_candidate = decoded[0]
                    if decoded_obj is None and isinstance(decoded[1], dict):
                        decoded_obj = decoded[1]
                else:
                    tx_candidate = decoded
                    if decoded_obj is None and isinstance(decoded, dict):
                        decoded_obj = decoded
                if isinstance(tx_candidate, Tx):
                    tx = tx_candidate
                elif isinstance(tx_candidate, dict):
                    decoded_obj = tx_candidate
            except Exception as exc:
                normalize_error = normalize_error or str(exc)

        if tx is None and raw and hasattr(Tx, "from_cbor"):
            try:
                tx = Tx.from_cbor(raw)  # type: ignore[attr-defined]
            except Exception as exc:
                normalize_error = normalize_error or str(exc)

        if tx is None and decoded_obj is not None:
            try:
                normalized = _normalize_tx_envelope(decoded_obj)
                tx = _construct_tx_from_dict(normalized)
            except Exception as exc:
                normalize_error = normalize_error or str(exc)

        if tx is None:
            reason = normalize_reason or "decode_error"
            dropped_counts[reason] = dropped_counts.get(reason, 0) + 1
            dropped_by_hash[hash_hex] = reason
            error_details = {"type": type(tx_obj).__name__}
            if normalize_error:
                error_details["normalize_error"] = normalize_error
            if decoded_obj:
                error_details.update(
                    {
                        "has_tx_field": "tx" in decoded_obj,
                        "has_sigs_field": "sigs" in decoded_obj,
                        "decoded_keys": list(decoded_obj.keys())[:10],
                    }
                )
            dropped_details[hash_hex] = {
                "reason": reason,
                "details": error_details,
            }
            continue

        if not raw:
            raw = getattr(tx, "raw_cbor", None) or b""
            if not raw and hasattr(tx, "to_cbor"):
                try:
                    raw = tx.to_cbor()
                except Exception:
                    raw = b""
        if raw:
            pending_raw_by_hash[hash_hex] = raw
        _TX_HASH_MAP[id(tx)] = (hash_hex, raw)
        coerced.append(tx)
        included_hashes.append(hash_hex)

    return coerced, included_hashes, dropped_counts, dropped_by_hash, dropped_details


def _normalize_hash_hex(hash_hex: str) -> str:
    if not hash_hex:
        return hash_hex
    normalized = hash_hex if hash_hex.startswith("0x") else f"0x{hash_hex}"
    return normalized.lower()


def _decode_cbor_loose(raw: bytes) -> dict | None:
    """
    Safely decode CBOR bytes to a dict.
    
    Returns:
        Decoded dict or None if cbor2 is unavailable or decoding fails
    """
    try:
        import cbor2
        obj = cbor2.loads(raw)
        return obj if isinstance(obj, dict) else None
    except ImportError:
        return None
    except Exception as e:
        log.debug(f"CBOR decode failed: {e}")
        return None


def _as_bytes32_addr(val: Any) -> bytes:
    """
    Convert address value to 32-byte format.
    
    Handles:
    - bytes/bytearray: pad or truncate to 32 bytes
    - hex string: decode and pad/truncate
    - bech32 string (anim1...): decode using _decode_bech32_address
    
    Returns:
        32-byte address
    """
    if val is None:
        return ZERO32
    
    if isinstance(val, (bytes, bytearray)):
        addr_bytes = bytes(val)
    elif isinstance(val, str):
        # Try bech32 decode first for anim1... addresses
        if val.startswith("anim1"):
            try:
                return _decode_bech32_address(val)
            except Exception:
                pass
        
        # Try hex decode
        try:
            hex_str = val[2:] if val.startswith("0x") else val
            if len(hex_str) % 2:
                hex_str = "0" + hex_str
            addr_bytes = bytes.fromhex(hex_str)
        except Exception:
            # Fall back to zero address for invalid input
            return ZERO32
    else:
        return ZERO32
    
    # Pad or truncate to 32 bytes
    if len(addr_bytes) < ADDRESS_LEN:
        addr_bytes = addr_bytes.rjust(ADDRESS_LEN, b"\x00")
    elif len(addr_bytes) > ADDRESS_LEN:
        addr_bytes = addr_bytes[-ADDRESS_LEN:]
    
    return addr_bytes


def _validate_payout_address(addr: Any) -> str:
    """
    Validate payout address input for miner.getBlockTemplate.

    Accepts bech32 anim1... addresses or 0x-prefixed 32-byte hex strings.
    Returns a normalized string (hex is 0x-prefixed) or raises InvalidParams.
    """
    if not isinstance(addr, str) or not addr.strip():
        raise rpc_errors.InvalidParams("address must be a non-empty string")
    value = addr.strip()
    if value.lower().startswith("anim"):
        try:
            _decode_bech32_address(value)
        except Exception as exc:
            raise rpc_errors.InvalidParams(
                "address must be a valid anim bech32 address"
            ) from exc
        return value

    hex_str = value[2:] if value.startswith("0x") else value
    if len(hex_str) != 64:
        raise rpc_errors.InvalidParams(
            "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        )
    try:
        bytes.fromhex(hex_str)
    except Exception as exc:
        raise rpc_errors.InvalidParams(
            "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        ) from exc
    return "0x" + hex_str


def _derive_sender_from_envelope_raw(raw: bytes) -> bytes | None:
    """
    Derive sender address from raw CBOR envelope by extracting pubkey and alg_id.
    
    Uses the signature envelope to reconstruct the bech32m address, then converts
    to 32-byte raw address format.
    
    Returns:
        32-byte sender address or None if derivation fails
    """
    # Decode the envelope
    obj = _decode_cbor_loose(raw)
    if obj is None:
        return None
    
    # Extract signature info (try various envelope formats)
    sigs = obj.get("sigs")
    sig = None
    
    if sigs and isinstance(sigs, list) and len(sigs) > 0:
        sig = sigs[0]
    elif "sig" in obj:
        sig = obj["sig"]
    elif "signature" in obj:
        sig = obj["signature"]
    
    if not isinstance(sig, dict):
        return None
    
    # Extract alg_id and pubkey
    alg_id = sig.get("alg") or sig.get("alg_id") or sig.get("algId")
    pubkey = sig.get("pubkey") or sig.get("pub") or sig.get("pk")
    
    if alg_id is None or pubkey is None:
        return None
    
    # Convert pubkey to bytes if needed
    if isinstance(pubkey, str):
        try:
            hex_str = pubkey[2:] if pubkey.startswith("0x") else pubkey
            pubkey = bytes.fromhex(hex_str)
        except Exception:
            return None
    
    # Derive bech32m address from pubkey
    try:
        from pq.py.address import address_from_pubkey
        bech32_addr = address_from_pubkey(pubkey, alg_id)
        # Convert bech32m to 32-byte raw address
        return _decode_bech32_address(bech32_addr)
    except Exception as e:
        log.debug(f"Failed to derive sender from envelope: {e}")
        return None


def _has_valid_sender(tx: Any) -> bool:
    """
    Check if a tx object has a valid (non-zero) sender.
    
    Args:
        tx: Transaction object (Tx instance or dict-like)
        
    Returns:
        True if tx has a non-zero sender, False otherwise
    """
    sender = None
    if hasattr(tx, "unsigned"):
        sender = getattr(tx.unsigned, "sender", None)
    if sender is None:
        sender = getattr(tx, "sender", None)
    
    return sender is not None and sender != ZERO32


def _attach_sender_if_possible(tx: Tx) -> Tx:
    """
    Attach sender to a Tx object if possible.
    
    If the tx already has a sender, returns it unchanged.
    If the tx is missing sender, tries to derive it from the tracked raw envelope.
    
    Returns:
        Updated Tx with sender attached, or original tx if derivation fails
    """
    # Check if tx already has valid sender
    if _has_valid_sender(tx):
        # Tx already has valid sender
        return tx
    
    # Try to derive sender from tracked raw envelope
    tracked = _tracked(tx)
    if tracked is None:
        return tx
    
    tx_hash_hex, raw = tracked
    derived_sender = _derive_sender_from_envelope_raw(raw)
    
    if derived_sender is None:
        return tx
    
    # Attach sender to tx by reconstructing with updated unsigned field
    try:
        if hasattr(tx, "unsigned"):
            # Tx dataclass with nested unsigned field
            unsigned_updated = replace(tx.unsigned, sender=derived_sender)
            tx_updated = replace(tx, unsigned=unsigned_updated)
            log.debug(f"Attached sender to tx {tx_hash_hex[:16]}...: {derived_sender.hex()[:16]}...")
            return tx_updated
        else:
            # Flat tx structure - can't easily update frozen dataclass
            # Return original tx unchanged
            log.debug(f"Cannot attach sender to flat tx structure for {tx_hash_hex[:16]}...")
            return tx
    except Exception as e:
        log.warning(f"Failed to attach sender to tx {tx_hash_hex[:16]}...: {e}")
        return tx


def _attach_sender_from_raw_if_missing(tx: Tx, raw: bytes) -> Tx:
    """
    Attach sender to a Tx object using raw envelope bytes when sender is missing.

    This is used during mempool snapshot collection to ensure selection sees a
    non-zero sender even when the tx body omits it (signature-derived sender).
    """
    if _has_valid_sender(tx):
        return tx
    if not raw:
        return tx
    derived_sender = _derive_sender_from_envelope_raw(raw)
    if derived_sender is None or derived_sender == ZERO32:
        return tx
    try:
        if hasattr(tx, "unsigned"):
            unsigned_updated = replace(tx.unsigned, sender=derived_sender)
            tx_updated = replace(tx, unsigned=unsigned_updated)
            log.debug(
                "Attached sender from raw envelope",
                extra={
                    "hash": _canonical_txid_hex(tx)[:16],
                    "sender": derived_sender.hex()[:16],
                },
            )
            return tx_updated
    except Exception as e:
        log.warning(f"Failed to attach sender from raw envelope: {e}")
    return tx


def _to_hex(b: bytes | None) -> str | None:
    return None if b is None else "0x" + b.hex()


def _bytes32(val: Any) -> bytes:
    if isinstance(val, (bytes, bytearray)):
        b = bytes(val)
    elif isinstance(val, str):
        s = val[2:] if val.startswith("0x") else val
        if len(s) % 2:
            s = "0" + s
        b = bytes.fromhex(s)
    else:
        return ZERO32
    if len(b) < 32:
        b = b.rjust(32, b"\x00")
    return b[:32]


def txid_bytes(tx: Tx | dict | bytes, raw: bytes | None = None) -> bytes:
    """
    Canonical txid helper used everywhere in mining/block assembly.
    
    Computes transaction hash (txid) from various tx representations:
    - Core `Tx` objects (uses `.hash()` or `.txid()` method)
    - Decoded dict/envelope objects (uses hash field or computes from raw)
    - Raw tx bytes (computes sha3_256 directly)
    
    The canonical rule is: TxID = sha3_256(raw_cbor_bytes) for the signed envelope.
    This matches the rule used by `rpc/methods/tx.py` in `tx.sendRawTransaction`.
    
    Args:
        tx: Transaction object (Tx instance, dict, or bytes)
        raw: Optional raw CBOR bytes (used to compute hash if tx is dict without hash field)
        
    Returns:
        32-byte transaction hash
        
    Raises:
        ValueError: If hash cannot be computed from any available source
    """
    # Try 1: tx.hash() method (Tx dataclass)
    if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
        try:
            h = tx.hash()
            if isinstance(h, bytes) and len(h) == 32:
                return h
        except Exception as e:
            log.debug(f"txid_bytes: tx.hash() failed: {e}")
    
    # Try 2: tx.txid() method (alternative Tx method name)
    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        try:
            h = tx.txid()
            if isinstance(h, bytes) and len(h) == 32:
                return h
        except Exception as e:
            log.debug(f"txid_bytes: tx.txid() failed: {e}")
    
    # Try 3: Attributes (tx.tx_hash, tx.txid, tx.hash as bytes or hex)
    for attr_name in ("tx_hash", "txid", "hash"):
        if hasattr(tx, attr_name):
            val = getattr(tx, attr_name)
            if isinstance(val, bytes) and len(val) == 32:
                return val
            elif isinstance(val, str):
                # Try to decode hex string
                try:
                    hex_str = val[2:] if val.startswith("0x") else val
                    h = bytes.fromhex(hex_str)
                    if len(h) == 32:
                        return h
                except Exception:
                    pass
    
    # Try 4: Dict keys (for envelope objects)
    if isinstance(tx, dict):
        for key_name in ("hash", "tx_hash", "txid"):
            if key_name in tx:
                val = tx[key_name]
                if isinstance(val, bytes) and len(val) == 32:
                    return val
                elif isinstance(val, str):
                    try:
                        hex_str = val[2:] if val.startswith("0x") else val
                        h = bytes.fromhex(hex_str)
                        if len(h) == 32:
                            return h
                    except Exception:
                        pass
    
    # Try 5: Compute from raw bytes if available
    if raw is not None and isinstance(raw, (bytes, bytearray)):
        try:
            return _tx_hash_bytes(raw)
        except Exception:
            return hashlib.sha3_256(bytes(raw)).digest()
    
    # Try 6: If tx is raw bytes, compute directly
    if isinstance(tx, (bytes, bytearray)):
        try:
            return _tx_hash_bytes(tx)
        except Exception:
            return hashlib.sha3_256(bytes(tx)).digest()
    
    # Try 7: If tx is a Tx dataclass, serialize to CBOR and hash
    if hasattr(tx, "to_cbor") and callable(getattr(tx, "to_cbor")):
        try:
            cbor_bytes = tx.to_cbor()
            try:
                return _tx_hash_bytes(cbor_bytes)
            except Exception:
                return hashlib.sha3_256(cbor_bytes).digest()
        except Exception as e:
            log.debug(f"txid_bytes: tx.to_cbor() failed: {e}")
    
    # Failed to compute hash from any source
    raise ValueError(
        f"Cannot compute txid from tx: type={type(tx).__name__}, "
        f"has_hash={hasattr(tx, 'hash')}, has_txid={hasattr(tx, 'txid')}, "
        f"has_to_cbor={hasattr(tx, 'to_cbor')}, raw_provided={raw is not None}"
    )


def _resolve_theta() -> int:
    # Try the live consensus state if available
    try:
        from consensus.state import consensus_state  # type: ignore

        st = consensus_state()
        if st and getattr(st, "theta_micro", None):
            return int(st.theta_micro)
    except Exception:
        pass
    return _DEFAULT_THETA_MICRO


def _ctx():
    try:
        return deps.get_ctx()
    except Exception:
        # In tests the FastAPI lifecycle may not have run yet; fall back to a
        # one-off context.
        return deps.build_context()


def _target_block_time_s(default: float = 300.0) -> float:
    log.info("template_submit", extra={"template_id": template_id, "parent_hash": parent_hash_hex, "head_hash_at_submit": head_snapshot.get("hash"), "head_height_at_submit": head_snapshot.get("height"), "head_hash_at_issue": (cached or {}).get("head_hash_at_issue") if isinstance(cached, dict) else None, "issued_at": (cached or {}).get("issued_at") if isinstance(cached, dict) else None, "expires_at": (cached or {}).get("expires_at") if isinstance(cached, dict) else None, "submitted_at": time.time()})

    try:
        ctx = _ctx()
        params = getattr(ctx, "params", {}) or {}
        monetary = params.get("monetary") or {}
        issuance = monetary.get("issuance") or {}
        target_ms = issuance.get("target_block_interval_ms")
        if target_ms is not None:
            return float(target_ms) / 1000.0
        block = params.get("block") or params.get("blocks") or {}
        if "target_seconds" in block:
            return float(block["target_seconds"])
    except Exception:
        pass
    return float(os.getenv("ANIMICA_TARGET_BLOCK_TIME_S", default))


def _min_block_spacing_s() -> float:
    default_spacing_ms = 0
    try:
        ctx = _ctx()
        params = getattr(ctx, "params", {}) or {}
        monetary = params.get("monetary") or {}
        issuance = monetary.get("issuance") or {}
        if "min_block_spacing_ms" in issuance:
            default_spacing_ms = int(issuance.get("min_block_spacing_ms") or 0)
        else:
            defaults = params.get("defaults") or {}
            default_spacing_ms = int(defaults.get("min_block_spacing_ms") or 0)
    except Exception:
        default_spacing_ms = 0
    try:
        override = int(os.getenv("ANIMICA_MIN_BLOCK_SPACING_MS", str(default_spacing_ms)))
    except ValueError:
        override = default_spacing_ms
    if override < 0:
        return 0.0
    return float(override) / 1000.0


def _head_timestamp_seconds() -> Optional[int]:
    snap = _current_head_snapshot()
    header = snap.get("header") if isinstance(snap, dict) else None
    ts = getattr(header, "timestamp", None) if header is not None else None
    if ts is not None:
        try:
            return int(ts)
        except Exception:
            return None
    hash_hex = snap.get("hash") if isinstance(snap, dict) else None
    if not hash_hex:
        return None
    try:
        h_bytes = bytes.fromhex(hash_hex[2:] if hash_hex.startswith("0x") else hash_hex)
        ctx = _ctx()
        if hasattr(ctx, "block_db") and ctx.block_db:
            header = ctx.block_db.get_header_by_hash(h_bytes)
            ts = getattr(header, "timestamp", None) if header is not None else None
            if ts is not None:
                return int(ts)
    except Exception:
        return None
    return None


def _adjust_theta_for_mining(dt_seconds: float | None = None) -> int:
    """
    Dynamically adjust theta micro during mining based on observed block times.
    
    This implements micro-adjustment of the acceptance threshold Θ to accommodate
    network stress and hash rate changes. Uses EMA-based retargeting from
    consensus.difficulty module.
    
    Theta (Θ) represents mining difficulty:
    - Higher theta → harder mining (fewer valid blocks)
    - Lower theta → easier mining (more valid blocks)
    - Fast blocks (dt < target) → increase theta
    - Slow blocks (dt > target) → decrease theta
    
    Args:
        dt_seconds: Time elapsed since last block (seconds). If None, returns current theta.
        
    Returns:
        int: Adjusted theta_micro value for next mining iteration (in micro-nats)
    """
    global _MINING_STATE
    
    # If adjustment is disabled, return baseline theta
    if not _MINING_STATE.get("adjustment_enabled", True):
        return _resolve_theta()
    
    # If no dt provided, return current theta (initialization case)
    if dt_seconds is None:
        # Initialize state if needed
        if _MINING_STATE.get("theta_state") is None:
            try:
                from consensus.difficulty import RetargetParams, init_state
                
                # Get current theta from consensus
                current_theta = _resolve_theta()
                
                # Initialize retarget params with mining-friendly settings
                # Use faster response for mining (smaller half-life, higher gain)
                # Theta is capped at THETA_HARD_CAP_MICRO (3B µ-nats = 3,000 nats)
                # to maintain network stability and prevent runaway values
                # Stability is ensured by hard cap, step_clamp_micro, and overflow protection
                # Adjusted parameters for gentler difficulty increases in devnet/testing scenarios
                target_block_time_s = _target_block_time_s()
                params = RetargetParams(
                    target_block_time_s=target_block_time_s,
                    half_life_blocks=12.0,           # Slower adaptation (was 8.0)
                    gain_beta=0.75,                  # Less aggressive response (was 0.9)
                    step_clamp_micro=1_000_000,      # Smaller steps (~1.0 nats per update, was 2.0)
                    theta_min_micro=50_000,          # Even lower minimum (~0.05 nats, was 0.1)
                    theta_max_micro=None,            # None = use hard cap (3B µ-nats)
                )

                head_snapshot = _current_head_snapshot()
                if int(head_snapshot.get("height") or 0) == 0:
                    current_theta = max(current_theta, params.theta_min_micro)
                
                _MINING_STATE["theta_state"] = init_state(params, current_theta)
                # Display effective maximum (hard cap when None)
                effective_max = params.theta_max_micro if params.theta_max_micro is not None else THETA_HARD_CAP_MICRO
                max_display = f"{effective_max / 1e6:.1f} nats"
                log.info(
                    f"Initialized dynamic theta adjustment for mining: "
                    f"theta={current_theta/1e6:.3f} nats, target_time={params.target_block_time_s}s, "
                    f"range=[{params.theta_min_micro/1e6:.1f}, {max_display}]"
                )
            except Exception as e:
                log.warning(f"Failed to initialize theta adjustment: {e}")
                _MINING_STATE["adjustment_enabled"] = False
                return _resolve_theta()
        
        # Return current theta from state
        state = _MINING_STATE.get("theta_state")
        if state:
            return int(state.theta_micro)
        return _resolve_theta()
    
    # Update theta based on observed block time
    try:
        from consensus.difficulty import update_theta
        
        state = _MINING_STATE.get("theta_state")
        if state is None:
            # Initialize state first
            _adjust_theta_for_mining(dt_seconds=None)
            state = _MINING_STATE.get("theta_state")
            if state is None:
                return _resolve_theta()
        
        # Validate dt_seconds (reject invalid and extreme values)
        # Upper bound: 1 hour = 3600s (prevents overflow and unreasonable adjustments)
        if dt_seconds <= 0 or not math.isfinite(dt_seconds) or dt_seconds > 3600.0:
            log.warning(
                f"Invalid dt_seconds for theta adjustment: {dt_seconds}, skipping update "
                f"(must be in range (0, 3600] seconds)"
            )
            return int(state.theta_micro)
        
        # Clamp dt_seconds to prevent extreme difficulty increases during rapid mining
        # When mining blocks very quickly (e.g., local devnet, testing), dt_seconds can be < 1s
        # This causes ln(dt/T) to be very negative, leading to exponential theta growth
        # Clamp to 10% of target to prevent runaway difficulty while still allowing adjustment
        target_time = state.params.target_block_time_s
        min_dt_threshold = max(1.0, target_time * 0.1)  # At least 1s or 10% of target
        
        if dt_seconds < min_dt_threshold:
            original_dt = dt_seconds
            dt_seconds = min_dt_threshold
            log.debug(
                f"Clamped dt_seconds for theta adjustment: {original_dt:.3f}s → {dt_seconds:.1f}s "
                f"(min threshold: {min_dt_threshold:.1f}s) to prevent extreme difficulty increases"
            )
        
        # Apply retargeting update
        new_state = update_theta(state, dt_seconds, blocks_skipped=1)
        _MINING_STATE["theta_state"] = new_state
        
        # Track block times for monitoring (keep last 20)
        # Use list for simplicity; for production consider collections.deque(maxlen=20)
        block_times = _MINING_STATE.get("block_times")
        if block_times is None:
            # Initialize with deque for automatic size management
            from collections import deque
            block_times = deque(maxlen=20)
            _MINING_STATE["block_times"] = block_times
        block_times.append(dt_seconds)
        
        # Log adjustment if significant change
        old_theta = state.theta_micro
        new_theta = new_state.theta_micro
        
        # Check for cap warning
        effective_max = state.params.theta_max_micro if state.params.theta_max_micro is not None else THETA_HARD_CAP_MICRO
        
        # Warn if approaching cap (within 10%)
        cap_threshold = effective_max * 0.9
        if new_theta >= cap_threshold and old_theta < cap_threshold:
            log.warning(
                f"Mining theta approaching maximum cap: {new_theta/1e6:.3f} nats "
                f"(cap: {effective_max/1e6:.1f} nats). "
                f"Network is experiencing high load. Theta will stabilize at cap if sustained."
            )
        
        if abs(new_theta - old_theta) > 10_000:  # > 0.01 nats change
            # Calculate average of last 5 block times (deque needs list conversion for slicing)
            recent_times = list(block_times)[-5:] if len(block_times) > 0 else []
            avg_time = sum(recent_times) / len(recent_times) if recent_times else 0.0
            log.info(
                f"Adjusted mining theta: {old_theta/1e6:.3f} → {new_theta/1e6:.3f} nats "
                f"(dt={dt_seconds:.2f}s, avg_5={avg_time:.2f}s, target={state.params.target_block_time_s}s)"
            )
        
        return int(new_theta)
        
    except Exception as e:
        log.error(f"Failed to adjust theta for mining: {e}", exc_info=True)
        # Disable adjustment on error to prevent cascading failures
        _MINING_STATE["adjustment_enabled"] = False
        return _resolve_theta()


def _network_block_interval(head_height: int, head_timestamp: int) -> float | None:
    if head_height <= 0 or head_timestamp <= 0:
        _MINING_STATE["last_network_height"] = head_height
        _MINING_STATE["last_network_timestamp"] = head_timestamp
        return None

    last_height = _MINING_STATE.get("last_network_height")
    last_timestamp = _MINING_STATE.get("last_network_timestamp")
    if last_height is None or last_timestamp is None:
        _MINING_STATE["last_network_height"] = head_height
        _MINING_STATE["last_network_timestamp"] = head_timestamp
        return None
    if int(last_height) == head_height:
        return None

    dt_seconds = int(head_timestamp) - int(last_timestamp)
    _MINING_STATE["last_network_height"] = head_height
    _MINING_STATE["last_network_timestamp"] = head_timestamp
    if dt_seconds <= 0:
        return None
    return float(dt_seconds)


def _mining_gate(
    *, allow_offline_mining: bool = False, allow_unsynced: bool = False
) -> tuple[bool, str | None]:
    """
    Determine if mining/template generation is allowed based on LOCAL execution state.
    
    Key principle: Mining readiness should depend on whether we have an executable chain tip
    (exec_head with valid state), NOT on whether we're still syncing headers from peers.
    
    The node can mine even while sync_phase is "headers" or "blocks", as long as:
    - We have a valid executed head (height >= 0, state available)
    - State DB is accessible and consistent
    - No fatal errors
    
    Args:
        allow_offline_mining: Requested offline override (ignored; unsafe overrides disabled)
        allow_unsynced: Requested unsynced override (ignored; unsafe overrides disabled)
        
    Returns:
        (allowed: bool, reason: str | None)
        - (True, None) if mining is allowed
        - (False, reason) if mining is blocked
    """
    # Unsafe override flags are disabled; log and ignore if requested.
    if os.getenv("ANIMICA_MINING_FORCE", "").lower() in ("1", "true", "yes", "on"):
        log.warning(
            "Unsafe mining override ANIMICA_MINING_FORCE requested; ignoring.",
            extra={"override": "ANIMICA_MINING_FORCE"},
        )
    if allow_offline_mining or os.getenv("ANIMICA_ALLOW_OFFLINE_MINING", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        log.warning(
            "Unsafe offline mining override requested; ignoring.",
            extra={
                "override": "allow_offline_mining",
                "requested": bool(allow_offline_mining),
            },
        )
    if allow_unsynced or os.getenv("ANIMICA_ALLOW_UNSYNCED_MINING", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        log.warning(
            "Unsafe unsynced mining override requested; ignoring.",
            extra={
                "override": "allow_unsynced_mining",
                "requested": bool(allow_unsynced),
            },
        )
    allow_offline_mining = False
    allow_unsynced = False
    
    # Check P2P service availability (optional - graceful degradation)
    try:
        import p2p
        svc = p2p.get_service()
    except Exception:
        svc = None
    
    # If no P2P service, allow mining (standalone/testing mode)
    if svc is None:
        return True, None
    
    # Get P2P and sync status
    try:
        p2p_status = svc.status_snapshot().to_dict()
        sync_status = svc.sync_status_snapshot().to_dict()
    except Exception:
        # Status unavailable - allow mining (graceful degradation)
        return True, None
    
    # Check minimum peer requirement (connectivity check, not execution check)
    outbound = int(p2p_status.get("peers_outbound", 0))
    peers_total = int(p2p_status.get("peers_total", 0))
    min_peers = int(os.getenv("ANIMICA_MINING_MIN_PEERS", "1"))
    
    if min_peers > 0 and peers_total < min_peers:
        if allow_offline_mining:
            log.warning(
                "MINER_ALLOW_OFFLINE",
                extra={"peers": peers_total, "min_peers": min_peers},
            )
        else:
            return False, "insufficient_peers"
    
    if not allow_offline_mining and outbound <= 0 and peers_total <= 0:
        return False, "offline_no_outbound_peers"
    
    # NEW: Execution head readiness check (replaces sync_phase check)
    # Mining is allowed if we have an executable head with valid state.
    # The sync_phase (headers/blocks/verifying) reflects P2P state, NOT execution state.
    # We can mine on exec_head even while headers are still being synced.
    
    # Get execution head height (currently same as head_height, but conceptually distinct)
    exec_head_height = int(sync_status.get("head_height") or 0)
    best_header_height = int(sync_status.get("best_header_height") or 0)
    best_block_height = int(sync_status.get("best_block_height") or 0)
    
    # Use best_block_height as proxy for exec_head (blocks we've fully applied)
    # In the future, track exec_head separately from header_head
    exec_head = max(exec_head_height, best_block_height)
    
    # Check if execution head is initialized (height >= 0 means we have genesis or later)
    if exec_head < 0:
        return False, "exec_head_uninitialized"
    
    # Check for fatal sync errors (indicates corrupted state or DB issues)
    fatal_error = sync_status.get("fatal_error")
    if fatal_error:
        log.error(
            "MINER_BLOCKED_FATAL_ERROR",
            extra={"fatal_error": fatal_error, "exec_head": exec_head},
        )
        return False, f"fatal_error:{fatal_error}"
    
    # Disable mining completely if we are 100 blocks or more behind the highest known height
    # This prevents mining on a fork or during initial sync when significantly behind
    header_lag = best_header_height - exec_head
    if header_lag >= 100:
        log.info(
            "MINER_TOO_FAR_BEHIND",
            extra={
                "exec_head": exec_head,
                "best_header_height": best_header_height,
                "lag": header_lag,
            },
        )
        return False, f"too_far_behind:{header_lag}_blocks"
    
    # Check execution lag: if exec_head is too far behind best known headers,
    # we may be in "headers-only" mode (headers synced but blocks not executed yet)
    max_lag = int(os.getenv("ANIMICA_MINING_MAX_LAG", "10"))  # Increased default from 2 to 10
    
    if header_lag > max_lag:
        # Execution is lagging significantly behind headers
        if allow_unsynced:
            log.warning(
                "MINER_ALLOW_EXEC_LAG",
                extra={
                    "exec_head": exec_head,
                    "best_header_height": best_header_height,
                    "lag": header_lag,
                    "max_lag": max_lag,
                },
            )
        else:
            # Note: This is the ONLY case where we block mining based on sync state.
            # We're blocking because execution is actually lagging, not because
            # of the sync_phase value (which we now ignore).
            log.info(
                "MINER_EXEC_HEAD_LAGGING",
                extra={
                    "exec_head": exec_head,
                    "best_header_height": best_header_height,
                    "lag": header_lag,
                    "max_lag": max_lag,
                },
            )
            return False, f"exec_head_lagging:{header_lag}_blocks"
    
    # REMOVED: sync_phase checks (lines 1093-1116 in original)
    # The sync_phase (idle/syncing/headers/blocks/verifying/synced) reflects
    # P2P header/block synchronization state, NOT local execution readiness.
    # We can mine even while phase is "headers" or "blocks", as long as
    # exec_head is available and not too far behind.
    #
    # This fixes the bug where the miner spams:
    # "sync_phase:headers; waiting for a synced block template"
    # even though mining is working fine.
    
    # Mining is allowed: exec_head is available and execution is not lagging
    return True, None


def _mining_disabled_payload(reason: str | None) -> dict[str, Any]:
    head = _current_head_snapshot()
    return {
        "disabled": True,
        "miningEnabled": False,
        "reason": reason,
        "head": {"height": head.get("height"), "hash": head.get("hash")},
    }


def _parent_within_canonical_window(
    *,
    block_db: Any,
    parent_hash: bytes,
    head_height: int,
    window: int,
) -> bool:
    if window <= 0:
        return False
    getter = getattr(block_db, "get_canonical_hash", None)
    if not callable(getter):
        return False
    start = max(0, head_height - window)
    for height in range(head_height, start - 1, -1):
        try:
            h = getter(height)
        except Exception:
            h = None
        if h is None:
            continue
        if bytes(h) == parent_hash:
            return True
    return False


def _current_head_snapshot() -> dict[str, Any]:
    with _HEAD_RW_LOCK:
        ctx = _ctx()
        snap = ctx.get_head()
        if _LOCAL_HEAD and isinstance(_LOCAL_HEAD, dict):
            local_h = int(_LOCAL_HEAD.get("height", 0))
            snap_h = int(snap.get("height", 0)) if isinstance(snap, dict) else 0
            if local_h > snap_h:
                snap = _LOCAL_HEAD
        if (snap.get("height") is None or snap.get("hash") is None) and _LOCAL_HEAD:
            snap = _LOCAL_HEAD

        header = snap.get("header") if isinstance(snap, dict) else None
        height = int(snap.get("height") or 0)
        hash_hex = snap.get("hash") if isinstance(snap, dict) else None
        if hash_hex is None and header is not None:
            header_hash = getattr(header, "hash", None)
            if callable(header_hash):
                hash_hex = "0x" + header_hash().hex()
            elif isinstance(header_hash, (bytes, bytearray)):
                hash_hex = "0x" + bytes(header_hash).hex()

        if hash_hex != _HEAD_STATE.get("hash") or height != _HEAD_STATE.get("height"):
            if height > int(_HEAD_STATE.get("height") or 0):
                _metric_inc("head_advances")
                _mark_head_progress()
            _HEAD_STATE["hash"] = hash_hex
            _HEAD_STATE["height"] = height
            _HEAD_STATE["generation"] = int(_HEAD_STATE.get("generation", 0)) + 1
        return {
            "height": height,
            "hash": hash_hex,
            "header": header,
            "generation": int(_HEAD_STATE.get("generation", 0)),
        }


def _template_head_state(
    *, ctx: Any, adapter: CoreChainAdapter, phase: str
) -> dict[str, Any]:
    snapshot = _current_head_snapshot()
    snap_height = int(snapshot.get("height") or 0)
    snap_hash = snapshot.get("hash")
    adapter_height = 0
    adapter_hash = None
    try:
        adapter_head = adapter.get_head()
        if isinstance(adapter_head, dict):
            adapter_height = int(adapter_head.get("height") or 0)
            adapter_hash = adapter_head.get("hash") or adapter_head.get("hash_hex")
    except Exception:
        adapter_head = None
    selected_height = snap_height
    selected_hash = snap_hash
    if selected_height == 0 and adapter_height > 0:
        log.error(
            "Template builder height desync: template_height=0 but chain_height=%d",
            adapter_height,
            extra={
                "phase": phase,
                "snapshot_height": snap_height,
                "snapshot_hash": snap_hash,
                "adapter_height": adapter_height,
                "adapter_hash": adapter_hash,
            },
        )
        selected_height = adapter_height
        if selected_hash is None and adapter_hash is not None:
            selected_hash = adapter_hash
    if _MINER_DEBUG:
        log.info(
            "template head state",
            extra={
                "phase": phase,
                "chain_id": ctx.cfg.chain_id,
                "snapshot_height": snap_height,
                "snapshot_hash": snap_hash,
                "adapter_height": adapter_height,
                "adapter_hash": adapter_hash,
                "selected_height": selected_height,
                "selected_hash": selected_hash,
            },
        )
    return {"chain_id": ctx.cfg.chain_id, "height": selected_height, "hash": selected_hash}


def _head_info() -> Tuple[bytes, int, bytes, int, bytes]:
    snap = _current_head_snapshot()
    header = snap.get("header")
    height = int(snap.get("height") or 0)
    chain_id = int(getattr(header, "chain_id", None) or _ctx().cfg.chain_id)

    parent_hash_hex = snap.get("hash")
    if parent_hash_hex and isinstance(parent_hash_hex, str):
        parent_hash = bytes.fromhex(
            parent_hash_hex[2:] if parent_hash_hex.startswith("0x") else parent_hash_hex
        )
    else:
        header_hash = getattr(header, "hash", None)
        parent_hash = header_hash() if callable(header_hash) else header_hash or ZERO32
    if len(parent_hash) < 32:
        parent_hash = parent_hash.rjust(32, b"\x00")
    parent_mix_seed = getattr(header, "mix_seed", None) or ZERO32
    parent_state_root = getattr(header, "state_root", None) or ZERO32
    return parent_hash, height or 0, parent_mix_seed, chain_id, parent_state_root


def _policy_roots() -> Tuple[bytes, bytes]:
    ctx = _ctx()
    pow_params = (ctx.params or {}).get("pow", {}) if hasattr(ctx, "params") else {}
    pq_root = pow_params.get("pqAlgPolicyRoot") or ZERO32
    poies_root = pow_params.get("poiesPolicyRoot") or ZERO32
    if isinstance(pq_root, str):
        pq_root = bytes.fromhex(pq_root[2:] if pq_root.startswith("0x") else pq_root)
    if isinstance(poies_root, str):
        poies_root = bytes.fromhex(
            poies_root[2:] if poies_root.startswith("0x") else poies_root
        )
    if not isinstance(pq_root, (bytes, bytearray)):
        pq_root = ZERO32
    if not isinstance(poies_root, (bytes, bytearray)):
        poies_root = ZERO32
    return bytes(pq_root), bytes(poies_root)


def _beacon() -> bytes:
    try:
        from randomness.beacon import get_beacon_bytes  # type: ignore

        return get_beacon_bytes() or b""
    except Exception:
        return b""


def _decode_bech32_address(address: str) -> bytes:
    """
    Decode a bech32 address to 32-byte raw address.
    
    Args:
        address: Bech32 address string (e.g., "anim1...")
        
    Returns:
        bytes: 32-byte address (digest padded to 32 bytes)
        
    Raises:
        Exception: If address cannot be decoded
    """
    from pq.py.address import decode_address  # type: ignore[import-not-found]
    
    addr_record = decode_address(address)
    digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    return digest[:32].ljust(32, b"\x00")


def _get_miner_address() -> bytes:
    """
    Determine the default miner address for block rewards.
    
    Priority:
    1. Environment variable ANIMICA_MINER_ADDRESS (bech32 address)
    2. Genesis premine address for the chain (if available)
    3. Zero address (fallback)
    
    Returns:
        bytes: 32-byte miner address
    """
    # Try environment variable first
    env_addr = os.getenv("ANIMICA_MINER_ADDRESS", "").strip()
    if env_addr:
        try:
            # Try to decode bech32 address to raw bytes
            return _decode_bech32_address(env_addr)
        except Exception as e:
            log.debug(f"Failed to decode ANIMICA_MINER_ADDRESS as bech32: {e}")
            # If bech32 decode fails, try hex
            try:
                if env_addr.startswith("0x"):
                    env_addr = env_addr[2:]
                addr_bytes = bytes.fromhex(env_addr)
                return addr_bytes[:32].ljust(32, b"\x00")
            except Exception as hex_err:
                log.warning(f"Failed to decode ANIMICA_MINER_ADDRESS as hex: {hex_err}")
    
    # Try to get premine address from consensus.rewards
    try:
        from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION  # type: ignore[import-not-found]
        
        ctx = _ctx()
        chain_id = ctx.cfg.chain_id
        
        # For mainnet (chain_id=1) or devnet (chain_id=1337), use first premine address
        if chain_id in (1, 1337) and MAINNET_PREMINE_DISTRIBUTION:
            premine_addr = MAINNET_PREMINE_DISTRIBUTION[0][0]  # First address in distribution
            try:
                return _decode_bech32_address(premine_addr)
            except Exception as e:
                log.warning(f"Failed to decode premine address: {e}")
    except Exception as e:
        log.debug(f"Could not load premine address: {e}")
    
    # Fallback to zero address
    log.warning("No miner address configured; using zero address for block rewards")
    return ZERO32


def _build_coinbase_transactions(ctx: Any, height: int, payout_address: bytes | None = None, instant_block: bool = False, canonical_height: int | None = None) -> tuple[list, int]:
    """
    Build coinbase transactions for block rewards.
    
    Args:
        ctx: RPC context with chain_id and params
        height: Block height for reward calculation
        payout_address: Optional 32-byte payout address. If None, uses default miner address.
        instant_block: If True, this is an instant block with zero rewards (default: False)
        canonical_height: Height counting only non-instant blocks, used for halving calculation
        
    Returns:
        tuple: (list of Tx objects for rewards, total miner reward amount)
    """
    try:
        from core.types.tx import Tx, UnsignedTx, TxKind  # type: ignore[import-not-found]
        from consensus.rewards import compute_block_reward  # type: ignore[import-not-found]
        
        # Get miner address (use custom payout address if provided)
        miner_address = payout_address if payout_address is not None else _get_miner_address()
        
        chain_id = ctx.cfg.chain_id
        params = getattr(ctx, "params", None) or {}
        
        rewards = compute_block_reward(
            chain_id=chain_id, 
            height=height, 
            params=params, 
            instant_block=instant_block,
            canonical_height=canonical_height
        )
        
        # Log warning if rewards are empty when they shouldn't be (height >= 1 and not instant block)
        if not rewards and height >= 1 and not instant_block:
            log.warning(
                f"Block reward at height {height} (canonical_height={canonical_height}) is empty. "
                f"This may indicate missing/invalid consensus params. "
                f"Check that spec/params.yaml defines proper emission schedule for chain_id={chain_id}."
            )
        
        coinbase_txs = []
        miner_reward_amount = 0
        
        # Create coinbase transaction for each reward
        for idx, (reward_addr, amount) in enumerate(rewards):
            if amount <= 0:
                continue
                
            # Override first reward (miner) with actual payout address
            if idx == 0:
                reward_addr_bytes = miner_address
                miner_reward_amount = amount
            else:
                # Convert bech32 address to bytes if needed (for aicf/treasury rewards)
                if isinstance(reward_addr, str):
                    try:
                        reward_addr_bytes = _decode_bech32_address(reward_addr)
                    except Exception:
                        log.warning(f"Could not decode reward address {reward_addr}; skipping")
                        continue
                else:
                    reward_addr_bytes = reward_addr[:32].ljust(32, b"\x00")
            
            # Build coinbase transaction
            unsigned_tx = UnsignedTx.build_coinbase(
                chain_id=chain_id,
                height=height,
                to=reward_addr_bytes,
                amount=amount,
            )
            
            # Create signed tx with empty signatures (coinbase txs don't need signatures)
            coinbase_tx = Tx(unsigned=unsigned_tx, sigs=tuple())
            coinbase_txs.append(coinbase_tx)
            
            log.info(
                f"Created coinbase tx: height={height}, "
                f"to={reward_addr_bytes.hex()[:16]}..., "
                f"amount={amount}"
            )
        
        return coinbase_txs, miner_reward_amount
    except Exception as e:
        # Don't fail mining if coinbase creation has issues
        log.error(f"Failed to build coinbase transactions at height {height}: {e}", exc_info=True)
        return [], 0


def _apply_block_reward(ctx: Any, height: int, payout_address: bytes | None = None, instant_block: bool = False) -> int:
    """
    Apply block reward to the miner's address in state.
    
    Args:
        ctx: RPC context with state_db access
        height: Block height for reward calculation
        payout_address: Optional 32-byte payout address. If None, uses default miner address.
        instant_block: If True, this is an instant block with zero rewards (default: False)
        
    Returns:
        int: Total miner reward amount (in nANM) credited to payout address, or 0 if none
    """
    try:
        # Get miner address (use custom payout address if provided)
        miner_address = payout_address if payout_address is not None else _get_miner_address()
        
        # Calculate canonical_height (count of non-instant blocks) for halving
        # This is the current canonical_height + 1 if this is a mining block, or unchanged if instant
        canonical_height = None
        try:
            block_db = getattr(ctx, "block_db", None)
            if block_db is not None and hasattr(block_db, "get_canonical_height"):
                current_canonical = block_db.get_canonical_height()
                if current_canonical is not None:
                    # For mining blocks, this will be the next canonical height
                    # For instant blocks, we still pass current + 1 but rewards will be 0 anyway
                    canonical_height = current_canonical + (0 if instant_block else 1)
        except Exception:
            pass
        
        # Compute block reward (returns list of (address, amount) tuples)
        from consensus.rewards import compute_block_reward  # type: ignore[import-not-found]
        
        chain_id = ctx.cfg.chain_id
        params = getattr(ctx, "params", None) or {}
        
        rewards = compute_block_reward(
            chain_id=chain_id, 
            height=height, 
            params=params, 
            instant_block=instant_block,
            canonical_height=canonical_height
        )
        
        # Log warning if rewards are empty when they shouldn't be (height >= 1 and not instant block)
        if not rewards and height >= 1 and not instant_block:
            log.warning(
                f"Block reward at height {height} (canonical_height={canonical_height}) is empty. "
                f"This may indicate missing/invalid consensus params. "
                f"Check that spec/params.yaml defines proper emission schedule for chain_id={chain_id}."
            )
        
        # Track miner reward amount for return
        miner_reward_amount = 0
        
        # If rewards are specified, apply them
        if rewards:
            from execution.state.apply_balance import credit  # type: ignore[import-not-found]
            
            state_db = ctx.state_db
            # Apply block rewards to state (miner, aicf, treasury)
            # For the first reward (miner), use the provided payout address (or default miner address)
            for idx, (reward_addr, amount) in enumerate(rewards):
                # Override first reward (miner) with actual payout address
                # Do this BEFORE trying to decode, since the first address may be a placeholder
                if idx == 0:
                    # Always use miner_address for first reward (miner reward)
                    # miner_address was set to payout_address if provided, else _get_miner_address()
                    reward_addr_bytes = miner_address
                else:
                    # Convert bech32 address to bytes if needed (for aicf/treasury rewards)
                    if isinstance(reward_addr, str):
                        try:
                            reward_addr_bytes = _decode_bech32_address(reward_addr)
                        except Exception:
                            log.warning(f"Could not decode reward address {reward_addr}; skipping")
                            continue
                    else:
                        reward_addr_bytes = reward_addr[:32].ljust(32, b"\x00")
                
                if amount > 0:
                    new_balance = credit(state_db, reward_addr_bytes, amount)
                    log.info(
                        f"Applied block reward: height={height}, canonical_height={canonical_height}, "
                        f"address={reward_addr_bytes.hex()[:16]}..., "
                        f"amount={amount}, new_balance={new_balance}"
                    )
                    
                    # Track miner reward (first reward entry)
                    if idx == 0:
                        miner_reward_amount = amount
        
        return miner_reward_amount
    except Exception as e:
        # Don't fail mining if reward application has issues
        log.error(f"Failed to apply block reward at height {height}: {e}", exc_info=True)
        return 0


def _compute_state_root(state_db: Any) -> bytes:
    """Compute a deterministic state root from the current state snapshot."""

    if state_db is None:
        return ZERO32

    try:
        snap = state_db.snapshot()
        if hasattr(snap, "digest"):
            root = snap.digest()
            if isinstance(root, str):
                root = bytes.fromhex(root[2:] if root.startswith("0x") else root)
            return _bytes32(root)
    except Exception as e:
        log.warning("failed to compute state root; returning zero", extra={"err": str(e)})

    return ZERO32


def _bits_to_target(bits_hex: str) -> int:
    from core.utils.pow import compact_bits_to_target

    return compact_bits_to_target(int(bits_hex, 16))


def _theta_to_target(theta_micro: int) -> int:
    """Derive the block target from θ using consensus math."""
    from core.utils.pow import micro_threshold_to_target256

    return micro_threshold_to_target256(int(theta_micro))


def _hex_to_bytes(hex_str: str) -> bytes:
    """
    Convert a hex string to bytes, handling both "0x" prefixed and unprefixed formats.
    
    Args:
        hex_str: Hex string (e.g., "0x6e23..." or "6e23...")
        
    Returns:
        bytes: The decoded bytes
    """
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    # Pad with leading zero if odd length (ensures valid hex pairs)
    if len(s) % 2:
        s = "0" + s
    return bytes.fromhex(s)


def _parse_nonce(nonce: Any) -> bytes:
    if isinstance(nonce, (bytes, bytearray)):
        return bytes(nonce)
    if isinstance(nonce, int):
        if nonce < 0:
            raise ValueError("nonce must be non-negative")
        return nonce.to_bytes(8, "big")
    if isinstance(nonce, str):
        return _hex_to_bytes(nonce)
    raise ValueError("nonce must be hex string, int, or bytes")


def _record_local_block(
    height: int, block_hash: str, header: dict[str, Any] | None = None
) -> None:
    _LOCAL_HEAD.update({"height": height, "hash": block_hash, "header": header})
    if _HEAD_STATE.get("height") != height or _HEAD_STATE.get("hash") != block_hash:
        _HEAD_STATE["height"] = height
        _HEAD_STATE["hash"] = block_hash
        _HEAD_STATE["generation"] = int(_HEAD_STATE.get("generation", 0)) + 1


def _relay_mined_block(block_hash: bytes) -> None:
    svc = p2p.get_service()
    if svc is None or not hasattr(svc, "relay_block"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(svc.relay_block(block_hash))


def auto_mine_enabled() -> bool:
    return _AUTO_MINE


def _get_tx_gas_limit(tx_obj: Any) -> int:
    """
    Extract gas_limit from a Tx object.
    
    Handles both flat and nested structures:
    - Flat: tx.gas_limit or tx.gas
    - Nested: tx.unsigned.gas_limit (Tx dataclass structure)
    
    Args:
        tx_obj: Transaction object (Tx instance or dict-like)
        
    Returns:
        Gas limit as integer, defaults to DEFAULT_TX_GAS_LIMIT (21,000) if not found
    """
    # Try flat gas_limit attribute
    tx_gas = getattr(tx_obj, "gas_limit", None)
    if tx_gas is not None:
        return int(tx_gas)
    
    # Try nested unsigned.gas_limit (Tx dataclass structure)
    if hasattr(tx_obj, "unsigned"):
        tx_gas = getattr(tx_obj.unsigned, "gas_limit", None)
        if tx_gas is not None:
            return int(tx_gas)
    
    # Try flat gas attribute (alternative naming)
    tx_gas = getattr(tx_obj, "gas", None)
    if tx_gas is not None:
        return int(tx_gas)
    
    # Default to intrinsic gas for simple transfers
    return DEFAULT_TX_GAS_LIMIT


def _get_tx_sender_and_nonce(tx: Tx) -> tuple[bytes | None, int | None]:
    """
    Extract sender address and nonce from a Tx object.
    
    Returns:
        tuple[bytes | None, int | None]: (sender_bytes, nonce) or (None, None) if extraction fails
    """
    try:
        # Try to get sender from unsigned field (Tx dataclass)
        sender = None
        nonce = None
        
        if hasattr(tx, "unsigned"):
            sender = getattr(tx.unsigned, "sender", None)
            nonce = getattr(tx.unsigned, "nonce", None)
        
        # Fallback to direct attributes
        if sender is None:
            sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
        if nonce is None:
            nonce = getattr(tx, "nonce", None)
        
        # Convert to bytes if string
        if isinstance(sender, str):
            if sender.startswith("0x"):
                sender = bytes.fromhex(sender[2:])
            elif sender.startswith("anim1"):
                # Decode bech32
                sender = _decode_bech32_address(sender)
            else:
                # Try hex without 0x prefix
                try:
                    sender = bytes.fromhex(sender)
                except ValueError:
                    sender = None
        
        # Ensure sender is bytes
        if sender is not None and not isinstance(sender, (bytes, bytearray)):
            sender = None
        
        # Ensure nonce is int
        if nonce is not None:
            nonce = int(nonce)
        
        return (bytes(sender) if sender else None, nonce)
    except Exception as e:
        log.debug(f"_get_tx_sender_and_nonce: Failed to extract sender/nonce: {e}")
        return (None, None)


def _evict_conflicting_pending_txs(txs: list[Tx]) -> int:
    """
    Remove pending transactions that conflict with included txs (same sender+nonce).

    This keeps the pending pool consistent after block acceptance, even when
    transactions are present in the fallback pool or rpc.pending_pool.
    """
    if not txs:
        return 0

    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        return 0

    included_pairs: set[tuple[bytes, int]] = set()
    for tx in txs:
        sender, nonce = _get_tx_sender_and_nonce(tx)
        if sender is not None and nonce is not None:
            included_pairs.add((sender, nonce))

    if not included_pairs:
        return 0

    pending_items: list[tuple[str, bytes]] = []
    pend = getattr(tx_methods, "_PEND", None)
    if pend is not None:
        if hasattr(pend, "list_raw") and callable(pend.list_raw):
            try:
                pending_items = list(pend.list_raw())
            except Exception:
                pending_items = []
        elif hasattr(pend, "items") and callable(pend.items):
            try:
                pending_items = list(pend.items())
            except Exception:
                pending_items = []

    if not pending_items:
        fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
        pending_items = list(fallback.items())

    removed = 0
    for pending_hash, raw in pending_items:
        try:
            decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
            tx_obj: Tx | None = None
            if isinstance(decoded, Tx):
                tx_obj = decoded
            elif isinstance(decoded, dict):
                normalized = _normalize_tx_envelope(decoded)
                tx_obj = _construct_tx_from_dict(normalized)
            if tx_obj is None:
                continue
            sender, nonce = _get_tx_sender_and_nonce(tx_obj)
            if sender is None or nonce is None:
                continue
            if (sender, nonce) not in included_pairs:
                continue
            removed_flag = tx_methods._pending_remove(pending_hash)  # type: ignore[attr-defined]
            if removed_flag:
                removed += 1
        except Exception:
            continue

    return removed


def _adapter() -> CoreChainAdapter:
    """
    Create a CoreChainAdapter with mempool feed for block building.
    
    This adapter connects the miner to both the chain state (via block_db/state_db)
    and the mempool (via miner_feed) so that pending transactions can be included
    in newly mined blocks.
    """
    ctx = _ctx()
    
    # Try to attach a miner_feed that drains from the RPC fallback pending cache
    # This allows get_mempool_snapshot() to return pending transactions
    miner_feed = None
    try:
        from mempool.adapters.miner_feed import MinerFeed
        
        # Import tx_methods to access _FALLBACK_PENDING
        # This is where transactions are stored when submitted via RPC
        try:
            from rpc.methods import tx as tx_methods
        except ImportError:
            tx_methods = None  # type: ignore[assignment]
        
        if tx_methods is not None:
            def drain_fn(max_gas: int, max_bytes: int):
                """
                Drain function that selects transactions from the pending pool.
                
                Priority order:
                1. ctx.mempool service (authoritative source where tx.sendRawTransaction submits)
                2. rpc.methods.tx._PEND (if available)
                3. _FALLBACK_PENDING (last resort)
                
                Returns a list of Tx objects. Uses _tx_hash_map to track original hashes.
                """
                log.info(f"drain_fn: ENTRY with max_gas={max_gas}, max_bytes={max_bytes}")
                txs = []
                try:
                    pending_map = {}
                    
                    # PRIORITY 1: Query the authoritative mempool service from context
                    # This is where tx.sendRawTransaction submits transactions
                    try:
                        ctx = _ctx()
                        mempool_svc = _resolve_mempool_service(ctx)
                        if mempool_svc is not None:
                            log.info(f"drain_fn: Found ctx.mempool service (id={hex(id(mempool_svc))})")
                            # Try to get a snapshot from the mempool service
                            if hasattr(mempool_svc, "snapshot") and callable(mempool_svc.snapshot):
                                try:
                                    # Calculate reasonable limit based on max_gas (avoid division by zero)
                                    tx_limit = max(1000, int(max_gas // 21000)) if max_gas > 0 else 1000
                                    snapshot = mempool_svc.snapshot(limit=tx_limit)
                                    entries = getattr(snapshot, "entries", [])
                                    raw_by_hash = getattr(snapshot, "raw_by_hash", {})
                                    log.info(f"drain_fn: Got {len(entries)} transactions from mempool.snapshot()")
                                    for entry in entries:
                                        hash_hex = getattr(entry, "hash_hex", None)
                                        if hash_hex:
                                            # Explicit None check to handle empty bytes correctly
                                            raw = raw_by_hash.get(hash_hex)
                                            if raw is None:
                                                raw = getattr(entry, "raw", None)
                                            if raw and isinstance(raw, (bytes, bytearray)):
                                                pending_map[hash_hex] = bytes(raw)
                                except Exception as e:
                                    log.warning(f"drain_fn: mempool.snapshot() failed: {e}", exc_info=True)
                        else:
                            log.info("drain_fn: No mempool service found in context")
                    except Exception as e:
                        log.warning(f"drain_fn: Failed to access mempool service: {e}", exc_info=True)
                    
                    # PRIORITY 2: Check _PEND (legacy pool)
                    if not pending_map:
                        pend = getattr(tx_methods, "_PEND", None)
                        if pend is not None:
                            log.info("drain_fn: Using _PEND pool")
                            # Try to get items from _PEND (depends on its interface)
                            if hasattr(pend, "items") and callable(pend.items):
                                try:
                                    pending_map = dict(pend.items())  # dict[str, bytes]
                                    log.info(f"drain_fn: Got {len(pending_map)} txs from _PEND.items()")
                                except Exception as e:
                                    log.warning(f"drain_fn: _PEND.items() failed: {e}")
                            elif hasattr(pend, "list_raw") and callable(pend.list_raw):
                                try:
                                    items = pend.list_raw()  # Iterable[(str, bytes)]
                                    pending_map = dict(items)
                                    log.info(f"drain_fn: Got {len(pending_map)} txs from _PEND.list_raw()")
                                except Exception as e:
                                    log.warning(f"drain_fn: _PEND.list_raw() failed: {e}")
                            else:
                                log.warning("drain_fn: _PEND exists but has no items() or list_raw() method")
                    
                    # PRIORITY 3: Fallback to _FALLBACK_PENDING if nothing else worked
                    if not pending_map:
                        fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
                        pending_map = fallback
                        log.info(f"drain_fn: Using _FALLBACK_PENDING with {len(pending_map)} txs")
                    
                    if pending_map:
                        # Log first few tx hashes for debugging
                        sample_hashes = list(pending_map.keys())[:3]
                        log.info(f"drain_fn: Sample pending tx hashes: {sample_hashes}")
                    
                    if not pending_map:
                        log.info("drain_fn: No pending transactions in any pool")
                        return []
                    
                    total_gas = 0
                    total_bytes = 0
                    
                    for tx_hash_hex, raw in pending_map.items():
                        try:
                            log.debug(f"drain_fn: Processing tx {tx_hash_hex}, raw_len={len(raw)}")
                            # Decode the raw CBOR transaction
                            decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                            log.debug(f"drain_fn: Decoded tx {tx_hash_hex}, type={type(decoded).__name__}")
                            
                            # Try to construct a Tx instance
                            tx_obj = None
                            if isinstance(decoded, Tx):
                                tx_obj = decoded
                                log.debug(f"drain_fn: tx {tx_hash_hex} is already Tx instance")
                            elif isinstance(decoded, dict):
                                # Normalize envelope format and try to construct Tx
                                normalized = _normalize_tx_envelope(decoded)
                                log.debug(f"drain_fn: Normalized tx {tx_hash_hex}, keys={list(normalized.keys())}")
                                tx_obj = _construct_tx_from_dict(normalized)
                                if tx_obj:
                                    log.debug(f"drain_fn: Successfully constructed Tx from dict for {tx_hash_hex}")
                                else:
                                    log.warning(f"drain_fn: Failed to construct Tx from dict for {tx_hash_hex}")
                            
                            if tx_obj is None:
                                log.error(f"drain_fn: Skipping tx {tx_hash_hex} - could not construct Tx instance (decoded type={type(decoded).__name__}, keys={list(decoded.keys()) if isinstance(decoded, dict) else 'N/A'})")
                                continue
                            
                            # Check gas and byte limits
                            tx_gas = _get_tx_gas_limit(tx_obj)
                            tx_bytes = len(raw)
                            
                            if total_gas + tx_gas > max_gas or total_bytes + tx_bytes > max_bytes:
                                # Skip this tx if it would exceed limits
                                log.debug(f"drain_fn: Skipping tx {tx_hash_hex} - would exceed limits (gas: {total_gas + tx_gas} > {max_gas} or bytes: {total_bytes + tx_bytes} > {max_bytes})")
                                continue
                            
                            # Store the original tx hash in the global mapping (keyed by object id)
                            # This ensures we can remove the correct entry from _FALLBACK_PENDING later
                            # even though Tx dataclasses are frozen and we can't set attributes
                            _TX_HASH_MAP[id(tx_obj)] = (tx_hash_hex, raw)
                            
                            txs.append(tx_obj)
                            total_gas += tx_gas
                            total_bytes += tx_bytes
                            log.debug(f"drain_fn: Added tx {tx_hash_hex} to batch (total: {len(txs)}, gas: {total_gas}, bytes: {total_bytes})")
                            
                        except Exception as e:
                            log.warning(f"drain_fn: Failed to decode tx {tx_hash_hex}: {e}", exc_info=True)
                            continue
                    
                    if txs:
                        log.info(f"drain_fn returning {len(txs)} transactions from fallback pending cache (total_gas={total_gas}, total_bytes={total_bytes})")
                    else:
                        log.warning(f"drain_fn: Processed {len(pending_map)} pending txs but returned 0 (all failed or exceeded limits)")
                    return txs
                    
                except Exception as e:
                    log.error(f"drain_fn failed with exception: {e}", exc_info=True)
                    return []
            
            # Create the MinerFeed with the drain function
            base_feed = MinerFeed(drain=drain_fn, notifier=None)
            
            # Wrap the MinerFeed to provide the peek_ready interface expected by CoreChainAdapter
            # CoreChainAdapter expects: peek_ready(limit, gas_limit) -> Iterable[Tx]
            class MinerFeedAdapter:
                """Adapter that provides peek_ready interface for CoreChainAdapter."""
                def __init__(self, feed: "MinerFeed"):
                    self._feed = feed
                
                def peek_ready(self, limit: int = 1000, gas_limit: int | None = None):
                    """Return an iterable of ready transactions (without removing them from pool)."""
                    log.info(f"MinerFeedAdapter.peek_ready called with limit={limit}, gas_limit={gas_limit}")
                    # Convert gas_limit to max_gas (use constant if None)
                    max_gas = gas_limit if gas_limit is not None else DEFAULT_BLOCK_GAS_LIMIT
                    max_bytes = DEFAULT_BLOCK_BYTE_LIMIT
                    
                    # Use next_batch() instead of private _drain() method
                    # next_batch returns a MinerTxBatch with a txs attribute
                    log.debug(f"MinerFeedAdapter.peek_ready calling next_batch(max_gas={max_gas}, max_bytes={max_bytes})")
                    batch = self._feed.next_batch(max_gas, max_bytes, wait_s=0)
                    txs = batch.txs if hasattr(batch, 'txs') else []
                    log.info(f"MinerFeedAdapter.peek_ready: next_batch returned {len(txs)} transactions")
                    
                    # Return up to limit transactions
                    result = txs[:limit] if limit else txs
                    log.info(f"MinerFeedAdapter.peek_ready returning {len(result)} transactions")
                    return result
            
            miner_feed = MinerFeedAdapter(base_feed)
            log.info("Created MinerFeed connected to RPC fallback pending cache")
    except Exception as e:
        # If anything fails, continue without a miner_feed
        # The adapter will still work but will use the inline fallback in _mine_once
        log.error(f"Failed to attach miner_feed to adapter: {e}", exc_info=True)
        miner_feed = None
    
    return CoreChainAdapter(
        kv=ctx.kv,
        block_db=ctx.block_db,
        state_db=getattr(ctx, "state_db", None),
        miner_feed=miner_feed,
    )


def _collect_mempool_entries(
    *,
    ctx: Any,
    adapter: CoreChainAdapter,
    limit: int = 1000,
) -> tuple[list[PendingTxEntry], dict[str, bytes], int]:
    pending_entries: list[PendingTxEntry] = []
    pending_raw_by_hash: dict[str, bytes] = {}
    total = 0

    mempool_service = _resolve_mempool_service(ctx)
    if mempool_service is not None:
        _log_mempool_binding("miner", ctx, mempool_service)
        snapshot = mempool_service.snapshot(limit=limit)
        total = int(snapshot.total)
        for entry in snapshot.entries:
            raw_candidate = snapshot.raw_by_hash.get(entry.hash_hex, entry.raw)
            if not raw_candidate and isinstance(entry.tx, dict):
                raw_candidate = entry.tx
            if not raw_candidate and hasattr(entry.tx, "to_cbor"):
                try:
                    raw_candidate = entry.tx.to_cbor()
                except Exception:
                    raw_candidate = None
            try:
                raw_bytes = normalize_tx(raw_candidate)
            except TxNormalizationError as exc:
                remover = getattr(mempool_service, "remove_included", None)
                if callable(remover):
                    remover([entry.hash_hex])
                recorder = getattr(mempool_service, "_record_rejection", None)
                if callable(recorder):
                    recorder(entry.hash_hex, exc.reason, exc.details)
                continue
            except Exception as exc:
                remover = getattr(mempool_service, "remove_included", None)
                if callable(remover):
                    remover([entry.hash_hex])
                recorder = getattr(mempool_service, "_record_rejection", None)
                if callable(recorder):
                    recorder(
                        entry.hash_hex,
                        "decode_error",
                        {"error": str(exc), "step": "normalize_tx"},
                    )
                continue
            tx_obj = entry.tx
            if isinstance(tx_obj, Tx) and raw_bytes:
                tx_obj = _attach_sender_from_raw_if_missing(tx_obj, raw_bytes)
                _TX_HASH_MAP[id(tx_obj)] = (
                    _normalize_hash_hex(entry.hash_hex),
                    raw_bytes,
                )
            pending_raw_by_hash[entry.hash_hex] = raw_bytes
            pending_entries.append(
                PendingTxEntry(
                    hash_hex=entry.hash_hex,
                    raw=raw_bytes,
                    tx=tx_obj,
                    received_at=entry.received_at,
                    expires_at=entry.expires_at,
                )
            )
        log.debug(
            "_collect_mempool_entries: using ctx.mempool service",
            extra={
                "mempool_id": id(mempool_service),
                "total": total,
                "entries": len(snapshot.entries),
            },
        )
        return pending_entries, pending_raw_by_hash, total

    try:
        snapshot = list(adapter.get_mempool_snapshot(limit=limit))
        total = len(snapshot)
        log.debug(
            "_collect_mempool_entries: using adapter.get_mempool_snapshot()",
            extra={"total": total, "entries": len(snapshot)},
        )
        try:
            from rpc.methods import tx as tx_methods
        except Exception:
            tx_methods = None  # type: ignore[assignment]

        for tx in snapshot:
            tracked = _tracked(tx)
            raw = b""
            if tracked:
                tx_hash_hex, raw = tracked
            else:
                raw = getattr(tx, "raw_cbor", None) or b""
                if not raw and hasattr(tx, "to_cbor"):
                    try:
                        raw = tx.to_cbor()
                    except Exception:
                        raw = b""
                if raw:
                    from mempool.tx_hash import tx_hash_hex as _tx_hash_hex

                    tx_hash_hex = _tx_hash_hex(raw)
                else:
                    tx_hash_hex = _canonical_txid_hex(tx)
                if raw:
                    _TX_HASH_MAP[id(tx)] = (_normalize_hash_hex(tx_hash_hex), raw)
            tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
            raw_candidate = raw if raw else (tx if isinstance(tx, dict) else None)
            if raw_candidate:
                try:
                    raw = normalize_tx(raw_candidate)
                except TxNormalizationError as exc:
                    if tx_methods is not None and hasattr(tx_methods, "_pending_remove"):
                        tx_methods._pending_remove(tx_hash_hex)  # type: ignore[attr-defined]
                    continue
                except Exception:
                    if tx_methods is not None and hasattr(tx_methods, "_pending_remove"):
                        tx_methods._pending_remove(tx_hash_hex)  # type: ignore[attr-defined]
                    continue
            if raw:
                pending_raw_by_hash[tx_hash_hex] = raw
            tx_obj = tx
            if isinstance(tx_obj, Tx) and raw:
                tx_obj = _attach_sender_from_raw_if_missing(tx_obj, raw)
                _TX_HASH_MAP[id(tx_obj)] = (_normalize_hash_hex(tx_hash_hex), raw)
            pending_entries.append(
                PendingTxEntry(
                    hash_hex=tx_hash_hex,
                    raw=raw or b"",
                    tx=tx_obj,
                )
            )
    except Exception as e:
        log.warning(
            f"mempool snapshot unavailable; falling back to in-process cache: {e}",
            exc_info=True,
        )

    return pending_entries, pending_raw_by_hash, total


def _request_missing_mempool_txs(
    *, limit: int = 128, wait_s: float = 0.25
) -> int:
    try:
        ctx = _ctx()
    except Exception:
        return 0
    p2p_service = getattr(ctx, "p2p_service", None)
    if p2p_service is None:
        return 0
    fn = getattr(p2p_service, "request_missing_txids", None)
    if not callable(fn):
        return 0
    try:
        running_loop = None
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        loop = running_loop or getattr(p2p_service, "loop", None)
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(fn(limit=limit), loop)
            return int(future.result(timeout=wait_s))
        return int(asyncio.run(fn(limit=limit)))
    except Exception:
        return 0


def _sync_all_peer_mempools(*, timeout_s: float = 2.0) -> int:
    """
    Sync mempools from all connected peers before building a block template.
    
    This ensures that when a miner builds a block, it includes transactions
    from all other nodes in the network, not just its local mempool.
    
    Args:
        timeout_s: Maximum time to wait for sync completion
        
    Returns:
        Number of peers successfully synced
    """
    try:
        ctx = _ctx()
    except Exception:
        return 0
    p2p_service = getattr(ctx, "p2p_service", None)
    if p2p_service is None:
        return 0
    fn = getattr(p2p_service, "sync_all_peer_mempools", None)
    if not callable(fn):
        return 0
    try:
        running_loop = None
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        loop = running_loop or getattr(p2p_service, "loop", None)
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(fn(timeout_s=timeout_s), loop)
            return int(future.result(timeout=timeout_s + 0.5))
        return int(asyncio.run(fn(timeout_s=timeout_s)))
    except Exception as e:
        log.warning(f"Failed to sync peer mempools: {e}")
        return 0


def _build_child_header(
    parent_height: int, parent_hash: bytes, parent_header: Any, *, coinbase: bytes | None = None, instant_block: bool = False
) -> Header:
    timestamp_min, timestamp_max, timestamp = _timestamp_bounds(parent_header)
    theta = getattr(
        parent_header, "thetaMicro", getattr(parent_header, "theta_micro", None)
    )
    mix_seed = getattr(
        parent_header, "mixSeed", getattr(parent_header, "mix_seed", None)
    )
    state_root = getattr(
        parent_header, "stateRoot", getattr(parent_header, "state_root", None)
    )
    pq_root, poies_root = _policy_roots()
    
    # Encode coinbase and instant_block flag in extra field
    # Format: CBOR({coinbase: bytes, instant_block: bool})
    extra_data = b""
    extra_dict = {}
    if coinbase is not None and coinbase != ZERO32:
        extra_dict["coinbase"] = coinbase
    if instant_block:
        extra_dict["instant_block"] = instant_block
    
    if extra_dict:
        try:
            import cbor2
            extra_data = cbor2.dumps(extra_dict)
        except Exception as e:
            log.warning(f"Failed to encode extra field: {e}")
            extra_data = b""
    
    return Header(
        v=1,
        chainId=_ctx().cfg.chain_id,
        height=parent_height + 1,
        parentHash=_bytes32(parent_hash),
        timestamp=timestamp,
        stateRoot=_bytes32(state_root or ZERO32),
        txsRoot=ZERO32,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=_bytes32(mix_seed or ZERO32),
        poiesPolicyRoot=poies_root,
        pqAlgPolicyRoot=pq_root,
        thetaMicro=int(theta or _resolve_theta()),
        nonce=0,
        extra=extra_data,
    )


def _prune_template_cache(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    with _TEMPLATE_CACHE_LOCK:
        expired = [
            key
            for key, meta in _TEMPLATE_CACHE.items()
            if float(meta.get("expires_at", meta.get("created_at", 0.0) + _TEMPLATE_TTL_S)) < now
        ]
        for key in expired:
            _TEMPLATE_CACHE.pop(key, None)


def _timestamp_bounds(parent_header: Any) -> tuple[int, int | None, int]:
    parent_ts = int(getattr(parent_header, "timestamp", 0) or 0)
    min_spacing_ms = int(os.getenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0"))
    min_delta = int(math.ceil(min_spacing_ms / 1000)) if min_spacing_ms > 0 else 0
    timestamp_min = parent_ts + min_delta if parent_ts else int(time.time())
    now = int(time.time())
    max_future = int(os.getenv("ANIMICA_MAX_FUTURE_SECONDS", "5"))
    timestamp_max = now + max_future if max_future > 0 else None
    if timestamp_max is not None and parent_ts > timestamp_max:
        timestamp_max = parent_ts
    candidate = max(now, timestamp_min)
    if timestamp_max is not None and candidate > timestamp_max:
        candidate = max(timestamp_min, timestamp_max)
    return timestamp_min, timestamp_max, candidate


def _cleanup_tracked_txs(txs: list[Any]) -> None:
    try:
        for tx in txs:
            _TX_HASH_MAP.pop(id(tx), None)
    except Exception as e:
        log.warning(f"Failed to clean up hash mapping: {e}")


def _convert_receipts_dict_to_objects(receipts_dict: list[dict[str, Any]]) -> list:
    """
    Convert dict receipts from _execute_transactions to Receipt objects.
    
    Args:
        receipts_dict: List of receipt dicts with keys: status, gasUsed, logs
        
    Returns:
        List of Receipt objects with proper types (ReceiptStatus enum, Log objects)
    """
    from core.types.receipt import Receipt, ReceiptStatus, Log
    
    receipts = []
    for r_dict in receipts_dict:
        # Convert status int to ReceiptStatus enum
        # Status codes: 0 = REVERT, 1 = SUCCESS, 2 = OOG
        status_val = r_dict.get("status", 0)
        if status_val == 1:
            status = ReceiptStatus.SUCCESS
        elif status_val == 2:
            status = ReceiptStatus.OOG
        else:
            status = ReceiptStatus.REVERT
        
        gas_used = int(r_dict.get("gasUsed", 0))
        
        # Convert logs (may be empty list or list of log-like objects)
        logs_out = []
        for log_item in r_dict.get("logs", []):
            # If log_item is already a Log object, use it directly
            if isinstance(log_item, Log):
                logs_out.append(log_item)
            elif isinstance(log_item, dict):
                # Convert dict log to Log object
                addr = log_item.get("address", b"\x00" * RECEIPT_ADDRESS_LEN)
                if isinstance(addr, (bytes, bytearray)):
                    addr_bytes = bytes(addr)
                else:
                    addr_bytes = b"\x00" * RECEIPT_ADDRESS_LEN
                # Pad to RECEIPT_ADDRESS_LEN bytes
                if len(addr_bytes) < RECEIPT_ADDRESS_LEN:
                    addr_bytes = addr_bytes.ljust(RECEIPT_ADDRESS_LEN, b"\x00")
                elif len(addr_bytes) > RECEIPT_ADDRESS_LEN:
                    addr_bytes = addr_bytes[:RECEIPT_ADDRESS_LEN]
                
                topics = log_item.get("topics", [])
                topics_tuple = tuple(
                    bytes(t)[:TOPIC_LEN].ljust(TOPIC_LEN, b"\x00") if isinstance(t, (bytes, bytearray)) else b"\x00" * TOPIC_LEN
                    for t in topics
                )
                data = bytes(log_item.get("data", b""))
                
                logs_out.append(Log(address=addr_bytes, topics=topics_tuple, data=data))
        
        receipts.append(Receipt(
            status=status,
            gas_used=gas_used,
            logs=tuple(logs_out)
        ))
    
    return receipts


def _execute_transactions(
    *,
    txs: list[Any],
    state_db: Any,
    block_env: Any,
    logger: Any,
) -> list[dict[str, Any]]:
    # Import execution runtime modules once at function level
    try:
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import make_tx_env
    except ImportError:
        # If execution runtime is not available, return empty receipts
        logger.warning("execution.runtime not available; cannot execute transactions")
        return [{"status": 0, "gasUsed": 0, "logs": []} for _ in txs]
    
    receipts: list[dict[str, Any]] = []

    for idx, tx in enumerate(txs):
        # --------------------------
        # Extract sender from tx
        # --------------------------
        sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
        if sender is None:
            unsigned = getattr(tx, "unsigned", None)
            if unsigned is not None:
                sender = (
                    getattr(unsigned, "sender", None)
                    or getattr(unsigned, "from", None)
                    or getattr(unsigned, "from_addr", None)
                    or getattr(unsigned, "frm", None)
                )

        sender_bytes = None
        if sender is None:
            logger.info(
                "Transaction %s missing sender; attempting signature-based execution",
                idx,
            )
        else:
            # Normalize sender to bytes (handles bech32, hex, and raw bytes)
            # Use _as_bytes32_addr for comprehensive address normalization
            try:
                sender_bytes = _as_bytes32_addr(sender)
            except Exception as e:
                logger.warning(f"Transaction {idx} sender normalization failed: {e}")
                receipts.append({"status": 0, "gasUsed": 0, "logs": []})
                continue

            # Validate sender is not zero address (except for coinbase transactions)
            # Coinbase transactions are protocol-generated and have sender = ZERO_ADDRESS
            is_coinbase = False
            if hasattr(tx, "unsigned"):
                unsigned = tx.unsigned
                kind = getattr(unsigned, "kind", None)
                if kind is not None:
                    kind_int = int(kind) if hasattr(kind, "__int__") else kind
                    is_coinbase = (kind_int == 3)  # TxKind.COINBASE = 3
            
            if not is_coinbase and (sender_bytes == ZERO32 or not any(sender_bytes)):
                logger.warning(
                    "Transaction %s has zero/invalid sender address; "
                    "attempting signature-based execution",
                    idx,
                )
                sender_bytes = None

        try:
            # Get recipient address for logging
            to_addr = getattr(tx, "to", None)
            if to_addr is None and hasattr(tx, "unsigned"):
                payload = getattr(tx.unsigned, "payload", None)
                if payload is not None:
                    to_addr = getattr(payload, "to", None)
            
            # Log transaction execution attempt
            to_hex = to_addr.hex()[:16] if isinstance(to_addr, bytes) else str(to_addr)
            from_hex = (
                f"{sender_bytes.hex()[:16]}..." if sender_bytes is not None else "unknown"
            )
            logger.info(
                f"Executing transaction {idx}/{len(txs)}: "
                f"from={from_hex} to={to_hex}"
            )
            
            # Extract gas_price from tx
            # Try canonical Tx dataclass structure first (tx.unsigned.gas_price)
            gas_price = 1
            if hasattr(tx, "unsigned"):
                gas_price = getattr(tx.unsigned, "gas_price", 1)
            # Fall back to flat attributes (for non-canonical formats)
            if gas_price == 1:
                gas_price = getattr(tx, "gas_price", getattr(tx, "gasPrice", getattr(tx, "tip", 1)))
            
            tx_env = make_tx_env(
                tx,
                block_env,
                sender=sender_bytes,
                gas_price=int(gas_price),
            )

            # Execute state transition (use keyword arguments for clarity)
            res = apply_transfer(tx=tx, state=state_db, block_env=block_env, tx_env=tx_env)

            # Log execution result
            status_str = "SUCCESS" if res.is_success else "FAILED"
            logger.info(
                f"Transaction {idx} executed: status={status_str}, "
                f"gasUsed={res.gas_used}, logs={len(res.logs or [])}"
            )

            # Receipt-like view
            # Note: res.is_success checks if res.status == TxStatus.SUCCESS
            receipts.append(
                {
                    "status": 1 if res.is_success else 0,
                    "gasUsed": int(res.gas_used or 0),
                    "logs": res.logs or [],
                }
            )
        except Exception as e:
            logger.exception(f"Transaction {idx} execution failed: %s", e)
            receipts.append({"status": 0, "gasUsed": 0, "logs": []})

    return receipts


def _normalize_tx_envelope(decoded: dict) -> dict:
    return normalize_tx_envelope(decoded)


def _construct_tx_from_dict(normalized: dict) -> Tx | None:
    """
    Try to construct a Tx instance from a normalized dict.
    
    Tries multiple constructor methods in order:
    1. Tx.from_obj() (preferred)
    2. Tx.from_dict() (fallback)
    
    Returns Tx instance or None if construction fails or no constructor available.
    """
    if hasattr(Tx, "from_obj"):
        try:
            return Tx.from_obj(normalized)  # type: ignore[attr-defined]
        except Exception as e:
            # Log detailed error with normalized structure for debugging
            log.error(
                f"_construct_tx_from_dict: Tx.from_obj failed: {e}",
                extra={
                    "error_type": type(e).__name__,
                    "error_msg": str(e),
                    "normalized_keys": list(normalized.keys()),
                    "has_tx": "tx" in normalized,
                    "has_sigs": "sigs" in normalized,
                    "tx_keys": list(normalized.get("tx", {}).keys()) if "tx" in normalized and isinstance(normalized.get("tx"), dict) else None,
                }
            )
            log.debug(f"_construct_tx_from_dict: Full normalized object: {normalized}", exc_info=True)
            return None
    elif hasattr(Tx, "from_dict"):
        try:
            return Tx.from_dict(normalized)  # type: ignore[attr-defined]
        except Exception as e:
            log.error(f"_construct_tx_from_dict: Tx.from_dict failed: {e}", exc_info=True)
            return None
    else:
        log.error("_construct_tx_from_dict: Tx class has no from_obj or from_dict method")
        return None


def _mine_once(
    payout_address: bytes | None = None,
    workers: int | None = None,
    *,
    include_mempool: bool = True,
    allow_offline_mining: bool = False,
    allow_unsynced_mining: bool = False,
    instant_block: bool = False,
    verbose: bool = False,
) -> tuple[bool, int, dict[str, Any]]:
    """
    Mine a single block with proof-of-work.
    
    This function performs actual mining by iterating through nonces until a valid
    block hash is found that meets the difficulty target. The target is derived from
    the current theta (acceptance threshold) parameter.
    
    Key operations:
    1. Select pending transactions from mempool snapshot
    2. Build candidate block header with txs merkle root
    3. Find nonce that satisfies PoW target (hash <= target)
    4. Execute transactions to update state (balances, nonces)
    5. Generate transaction receipts (status, gasUsed, logs)
    6. Apply block reward to coinbase/payout address
    7. Persist block with receipts and update canonical head
    
    Base units: 1 ANM = 1_000_000_000 nANM (nano-ANM)
    Block numbering: 0-based (genesis at height 0)
    Mempool selection: Snapshot at time of mining (non-deterministic ordering OK)
    
    Args:
        payout_address: Optional 32-byte payout address. If None, uses default miner address.
        workers: Number of parallel worker processes to use for nonce search (default: CPU count)
        
    Returns:
        tuple[bool, int, dict[str, Any]]: (success, reward_amount, selection_summary) where:
            - success: True if block was mined and accepted, False otherwise
            - reward_amount: Miner reward in nANM (0 if mining failed or no reward)
            - selection_summary: Dict with mempool selection statistics (pending, selected, rejected, etc.)
    """
    # Default workers to CPU count (minus one) for optimal multi-core mining
    from mining.parallel_nonce_search import resolve_worker_count

    workers = resolve_worker_count(workers)
    allowed, reason = _mining_gate(
        allow_offline_mining=allow_offline_mining,
        allow_unsynced=allow_unsynced_mining,
    )
    if not allowed:
        log.warning("Mining disabled", extra={"reason": reason})
        return (False, 0, _mining_disabled_payload(reason))

    ctx = _ctx()
    adapter = _adapter()
    mempool_service = getattr(ctx, "mempool", None)
    pending_entries: list[PendingTxEntry] = []
    pending_raw_by_hash: dict[str, bytes] = {}
    selection_summary: dict[str, Any] = {
        "pending": 0,
        "selected": 0,
        "rejected": {},
        "rejectedByHash": {},
        "mempoolEnabled": include_mempool,
    }

    if include_mempool:
        log.info("_mine_once: Starting transaction collection from mempool adapter")
        log.info(f"_mine_once: Adapter has miner_feed: {adapter.miner_feed is not None}")
        pending_entries, pending_raw_by_hash, pending_total = _collect_mempool_entries(
            ctx=ctx,
            adapter=adapter,
            limit=1000,
        )
        log.info(
            "_mine_once: mempool collection summary",
            extra={
                "entries": len(pending_entries),
                "total": pending_total,
                "source": "service" if mempool_service is not None else "adapter",
            },
        )
        if not pending_entries:
            requested = _request_missing_mempool_txs(limit=128, wait_s=0.25)
            if requested:
                log.info(
                    "_mine_once: requested missing mempool txids",
                    extra={"requested": requested},
                )
                time.sleep(0.25)
                pending_entries, pending_raw_by_hash, pending_total = (
                    _collect_mempool_entries(
                        ctx=ctx,
                        adapter=adapter,
                        limit=1000,
                    )
                )
                log.info(
                    "_mine_once: mempool collection summary (after fetch)",
                    extra={
                        "entries": len(pending_entries),
                        "total": pending_total,
                        "source": "service" if mempool_service is not None else "adapter",
                    },
                )
    else:
        pending_total = 0
        log.info("_mine_once: Mempool inclusion disabled; mining payout-only block")

    if include_mempool and not pending_entries and mempool_service is None:
        log.info("_mine_once: No transactions from adapter, trying fallback direct read")
        try:
            from rpc.methods import tx as tx_methods

            pend = getattr(tx_methods, "_PEND", None)
            pending_map = {}

            if pend is not None:
                log.info("_mine_once fallback: Using _PEND pool")
                if hasattr(pend, "items") and callable(pend.items):
                    try:
                        pending_map = dict(pend.items())
                        log.info(f"_mine_once fallback: Got {len(pending_map)} txs from _PEND.items()")
                    except Exception as e:
                        log.warning(f"_mine_once fallback: _PEND.items() failed: {e}")
                elif hasattr(pend, "list_raw") and callable(pend.list_raw):
                    try:
                        items = pend.list_raw()
                        pending_map = dict(items)
                        log.info(f"_mine_once fallback: Got {len(pending_map)} txs from _PEND.list_raw()")
                    except Exception as e:
                        log.warning(f"_mine_once fallback: _PEND.list_raw() failed: {e}")

            if not pending_map:
                fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
                pending_map = fallback
                log.info(f"_mine_once fallback: Using _FALLBACK_PENDING with {len(pending_map)} txs")

            for tx_hash_hex, raw in pending_map.items():
                try:
                    decoded, _obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                    tx_obj = None
                    if isinstance(decoded, Tx):
                        tx_obj = decoded
                    elif isinstance(decoded, dict):
                        normalized = _normalize_tx_envelope(decoded)
                        tx_obj = _construct_tx_from_dict(normalized)
                    if tx_obj is None:
                        log.warning(
                            "Could not construct Tx from pending entry; skipping",
                            extra={"hash": tx_hash_hex},
                        )
                        continue
                    tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
                    pending_raw_by_hash[tx_hash_hex] = raw
                    pending_entries.append(
                        PendingTxEntry(hash_hex=tx_hash_hex, raw=raw, tx=tx_obj)
                    )
                except Exception as e:
                    log.warning(
                        "Failed to decode pending tx from fallback cache; skipping",
                        extra={"hash": tx_hash_hex, "err": str(e)},
                        exc_info=True,
                    )
        except Exception as e:
            log.error("Fallback pending pool unavailable", extra={"err": str(e)}, exc_info=True)

    if include_mempool and pending_entries:
        normalized_entries: list[PendingTxEntry] = []
        for entry in pending_entries:
            tx = entry.tx
            if tx is None:
                normalized_entries.append(entry)
                continue
            tx_normalized = _attach_sender_if_possible(tx)
            if tx_normalized is not tx:
                tracked = _tracked(tx)
                if tracked:
                    _TX_HASH_MAP[id(tx_normalized)] = tracked
            normalized_entries.append(
                PendingTxEntry(
                    hash_hex=entry.hash_hex, raw=entry.raw, tx=tx_normalized
                )
            )
        decode_fn = None
        try:
            from rpc.methods import tx as tx_methods

            decode_fn = tx_methods._decode_tx  # type: ignore[attr-defined]
        except Exception:
            decode_fn = None
        min_gas_price = 0
        try:
            min_gas_price = int(ctx.params.get("min_gas_price", 0))
        except Exception:
            min_gas_price = 0
        try:
            from rpc.methods import tx as tx_methods
        except Exception:
            tx_methods = None  # type: ignore[assignment]

        def _signature_validator(tx_obj: Any, decoded_obj: dict[str, Any] | None) -> None:
            if tx_methods is None:
                return
            if decoded_obj is None:
                return
            tx_methods._verify_pq_signature(  # type: ignore[attr-defined]
                tx_obj, decoded_obj, chain_id=_resolve_chain_id_for_sig(ctx)
            )

        selection = select_for_block(
            head_state=_template_head_state(ctx=ctx, adapter=adapter, phase="mine_once"),
            limits={
                "max_gas": DEFAULT_BLOCK_GAS_LIMIT,
                "max_bytes": DEFAULT_BLOCK_BYTE_LIMIT,
                "max_txs": 1000,
            },
            pending=normalized_entries,
            decode=decode_fn,
            state_db=getattr(ctx, "state_db", None),
            policy={"min_gas_price": min_gas_price},
            tx_index=getattr(ctx, "tx_index", None),
            signature_validator=_signature_validator,
        )
        txs, included_hashes, dropped_counts, dropped_by_hash, dropped_details = (
            _coerce_selected_txs(
                selected=list(selection.selected),
                selected_hashes=list(selection.selected_hashes),
                pending_raw_by_hash=pending_raw_by_hash,
                decode_fn=decode_fn,
            )
        )
        merged_rejected = dict(selection.rejected)
        for reason, count in dropped_counts.items():
            merged_rejected[reason] = merged_rejected.get(reason, 0) + int(count)
        merged_rejected_by_hash = dict(selection.rejected_by_hash)
        merged_rejected_by_hash.update(dropped_by_hash)
        merged_rejected_details_by_hash = dict(selection.rejected_details_by_hash)
        merged_rejected_details_by_hash.update(dropped_details)
        selection_summary = {
            "pending": selection.total_pending,
            "candidates": len(pending_entries),
            "mempoolTotal": pending_total,
            "selected": len(txs),
            "rejected": dict(merged_rejected),
            "rejectedCount": sum(int(v) for v in merged_rejected.values()),
            "rejectedByHash": dict(list(merged_rejected_by_hash.items())[:10]),
            "rejectedDetailsByHash": dict(
                list(merged_rejected_details_by_hash.items())[:10]
            ),
            "mempoolEnabled": True,
        }
        log.info(
            "TEMPLATE_BUILD",
            extra={
                "mempool_total": pending_total,
                "included": len(txs),
                "rejected": dict(merged_rejected),
            },
        )
        if selection.total_pending and len(txs) == 0:
            selection_summary["warnings"] = [
                "mempool_pending_but_not_included",
                "top_rejected_reasons="
                + ",".join(sorted(merged_rejected.keys())[:3]),
            ]
        if selection.total_pending and len(txs) == 0:
            selection_summary["warnings"] = [
                "mempool_pending_but_not_included",
                "top_rejected_reasons="
                + ",".join(sorted(merged_rejected.keys())[:3]),
            ]
        _maybe_log_mempool_debug(
            phase="mine_once",
            pending_total=selection.total_pending,
            candidate_count=len(pending_entries),
            included_count=len(txs),
            rejected=merged_rejected,
            rejected_details_by_hash=merged_rejected_details_by_hash,
        )
        log.debug(
            "mempool selection summary",
            extra={
                "pending": selection.total_pending,
                "selected": len(txs),
                "rejected": dict(merged_rejected),
                "rejected_by_hash_sample": dict(
                    list(merged_rejected_by_hash.items())[:10]
                ),
            },
        )
        if merged_rejected:
            log.info(
                "Mining mempool selection rejected candidates",
                extra={
                    "rejected": dict(merged_rejected),
                    "rejected_by_hash_sample": dict(
                        list(merged_rejected_by_hash.items())[:10]
                    ),
                },
            )
        if selection.total_pending and len(txs) == 0:
            log.warning(
                "Mining produced empty block despite pending mempool txs",
                extra={
                    "pending": selection.total_pending,
                    "rejected": dict(merged_rejected),
                },
            )
    else:
        txs = []
        included_hashes = []
        if not include_mempool:
            selection_summary = {
                "pending": 0,
                "selected": 0,
                "rejected": {"mempool_disabled": 0},
                "rejectedByHash": {},
                "mempoolEnabled": False,
            }
        elif selection_summary.get("pending", 0) == 0:
            selection_summary = {
                "pending": 0,
                "selected": 0,
                "rejected": {},
                "rejectedByHash": {},
                "mempoolEnabled": True,
            }
    
    head = adapter.get_head()
    parent_height = int(head.get("height") or 0)
    parent_hash_val = head.get("hash") or head.get("hash_hex")
    parent_header = head.get("obj") or head.get("header")
    start_head_height = parent_height

    if parent_header is None:
        # If the DB is empty, force bootstrap and retry once
        _maybe_bootstrap = getattr(deps, "startup", None)
        if callable(_maybe_bootstrap):
            try:
                # reinitialize context to pick up genesis
                deps.ensure_started(ctx.cfg)
                head = adapter.get_head()
                parent_height = int(head.get("height") or 0)
                parent_hash_val = head.get("hash") or head.get("hash_hex")
                parent_header = head.get("obj") or head.get("header")
                start_head_height = parent_height
            except Exception:
                parent_header = None

    parent_hash_bytes = _bytes32(parent_hash_val or ZERO32)
    start_head_hash_bytes = parent_hash_bytes
    if parent_header is None:
        # Build a minimal synthetic parent header so hashes/roots have sane defaults
        pq_root, poies_root = _policy_roots()
        parent_header = Header(
            v=1,
            chainId=_ctx().cfg.chain_id,
            height=parent_height,
            parentHash=parent_hash_bytes,
            timestamp=int(time.time()),
            stateRoot=ZERO32,
            txsRoot=ZERO32,
            receiptsRoot=ZERO32,
            proofsRoot=ZERO32,
            daRoot=ZERO32,
            mixSeed=ZERO32,
            poiesPolicyRoot=poies_root,
            pqAlgPolicyRoot=pq_root,
            thetaMicro=_resolve_theta(),
            nonce=0,
            extra=b"",
        )

    # Build child header template (nonce will be updated in mining loop). Update the
    # txsRoot to reflect any pending transactions we plan to include.
    timestamp_min, timestamp_max, _ = _timestamp_bounds(parent_header)
    
    # Use provided payout_address or fall back to default miner address
    coinbase_bytes = payout_address if payout_address is not None else _get_miner_address()
    
    header_template = _build_child_header(parent_height, parent_hash_bytes, parent_header, coinbase=coinbase_bytes, instant_block=instant_block)
    
    # Check if previous block is older than max_block_time_s (force block if needed)
    # This ensures the chain progresses even when no miners are active for extended periods
    force_block_due_to_time = False
    max_block_time_s = float(os.getenv("ANIMICA_MAX_BLOCK_TIME_S", "3600"))  # 1 hour default
    if parent_header is not None and max_block_time_s > 0:
        parent_timestamp = int(getattr(parent_header, "timestamp", 0) or 0)
        current_time = int(time.time())
        if parent_timestamp > 0:
            time_since_last_block = current_time - parent_timestamp
            if time_since_last_block > max_block_time_s:
                force_block_due_to_time = True
                log.warning(
                    f"Previous block is {time_since_last_block:.0f}s old "
                    f"(exceeds max_block_time_s={max_block_time_s:.0f}s). "
                    f"Forcing new block with minimum difficulty to ensure chain progress."
                )
    
    # Apply dynamic theta adjustment based on recent block times
    # This adapts mining difficulty to network conditions (hash rate, block times)
    global _MINING_STATE
    network_dt_seconds = None
    if parent_header is not None:
        head_timestamp = int(getattr(parent_header, "timestamp", 0) or 0)
        network_dt_seconds = _network_block_interval(parent_height, head_timestamp)
    # Force minimum theta if block is being forced due to time
    if force_block_due_to_time:
        # Use minimum theta to ensure block can be mined quickly
        # This matches the theta_min_micro used in mining adjustment (see line 953)
        try:
            header_template = replace(header_template, thetaMicro=_FORCED_BLOCK_MIN_THETA_MICRO)
            log.info(
                f"Forcing block with minimum theta: {_FORCED_BLOCK_MIN_THETA_MICRO/1e6:.3f} nats "
                f"(previous block exceeded max_block_time_s)"
            )
        except Exception as e:
            # If we can't set minimum theta, try to use the current minimum from adjustment state
            # This ensures forcing still works even if replace() fails for some reason
            log.error(f"Failed to apply minimum theta for forced block: {e}")
            try:
                state = _MINING_STATE.get("theta_state")
                if state and hasattr(state, "params"):
                    fallback_theta = state.params.theta_min_micro
                    header_template = replace(header_template, thetaMicro=fallback_theta)
                    log.warning(f"Using fallback theta: {fallback_theta/1e6:.3f} nats")
            except Exception as e2:
                log.error(f"Fallback theta also failed: {e2}. Mining may be difficult.")
    elif network_dt_seconds is not None:
        adjusted_theta = _adjust_theta_for_mining(network_dt_seconds)
        try:
            header_template = replace(header_template, thetaMicro=adjusted_theta)
            log.debug(
                "Applied dynamic theta adjustment: %.3f nats (network dt=%.2fs)",
                adjusted_theta / 1e6,
                network_dt_seconds,
            )
        except Exception as e:
            log.warning(f"Failed to apply theta adjustment to header: {e}")
    else:
        last_block_time = _MINING_STATE.get("last_block_time")
        if last_block_time is not None:
            current_time = time.time()
            dt_seconds = current_time - last_block_time
            adjusted_theta = _adjust_theta_for_mining(dt_seconds)
            try:
                header_template = replace(header_template, thetaMicro=adjusted_theta)
                log.debug(
                    "Applied dynamic theta adjustment: %.3f nats (local dt=%.2fs)",
                    adjusted_theta / 1e6,
                    dt_seconds,
                )
            except Exception as e:
                log.warning(f"Failed to apply theta adjustment to header: {e}")
        else:
            # First block - initialize adjustment state
            _adjust_theta_for_mining(dt_seconds=None)
            log.info("Initialized theta adjustment for first mined block")
    
    # Create coinbase transactions for block rewards
    # These must be added BEFORE computing txsRoot since they're part of the block
    next_height = parent_height + 1
    
    # Calculate canonical_height (count of non-instant blocks) for halving
    # This is used to ensure instant blocks don't count towards emission schedule
    canonical_height = None
    try:
        block_db = getattr(ctx, "block_db", None)
        if block_db is not None and hasattr(block_db, "get_canonical_height"):
            current_canonical = block_db.get_canonical_height()
            if current_canonical is not None:
                # For mining blocks, canonical height increases by 1
                # For instant blocks, it stays the same (they don't count)
                canonical_height = current_canonical + (0 if instant_block else 1)
    except Exception:
        pass
    
    coinbase_txs, reward_amount = _build_coinbase_transactions(
        ctx, next_height, payout_address, instant_block=instant_block, canonical_height=canonical_height
    )
    
    # Prepend coinbase transactions to the tx list
    # Coinbase txs always come first in the block
    if coinbase_txs:
        txs = coinbase_txs + txs
        # Generate hash identifiers for coinbase txs
        for coinbase_tx in coinbase_txs:
            try:
                coinbase_hash_hex = "0x" + coinbase_tx.hash().hex()
                included_hashes.insert(0, coinbase_hash_hex)
            except Exception as e:
                log.warning(f"Failed to compute hash for coinbase tx: {e}")
        log.info(f"Added {len(coinbase_txs)} coinbase transaction(s) for rewards")

    if txs:
        # Build merkle root from canonical tx.hash() values to match Block.txs_root().
        # Drop individual malformed txs instead of failing the whole batch.
        leaves = []
        valid_txs = []
        valid_hashes = []

        for i, tx in enumerate(txs):
            try:
                tx_hash = tx.hash()
                tx_hash_hex = "0x" + tx_hash.hex()

                leaves.append(tx_hash)
                valid_txs.append(tx)
                if i < len(included_hashes):
                    valid_hashes.append(included_hashes[i])
                else:
                    valid_hashes.append(tx_hash_hex)
            except Exception as e:
                log.warning(
                    f"Skipping malformed tx {i+1}/{len(txs)} during hash computation: {e}",
                    extra={"tx_type": type(tx).__name__, "err": str(e)},
                    exc_info=True,
                )

        original_count = len(txs)
        valid_count = len(valid_txs)
        skipped_total = original_count - valid_count

        txs = valid_txs
        included_hashes = valid_hashes

        if valid_count > 0 or skipped_total > 0:
            log.info(
                f"Selected {valid_count} valid transactions for block (skipped {skipped_total} malformed)",
                extra={"pending_total": original_count, "valid": valid_count, "skipped": skipped_total},
            )

        if leaves:
            try:
                from core.utils.merkle import compute_txs_root_from_txs

                txs_root = compute_txs_root_from_txs(txs)
                header_template = replace(header_template, txsRoot=txs_root)
                log.debug(
                    f"Computed txsRoot from {len(leaves)} tx hashes: {txs_root.hex()[:16]}..."
                )

                tx_tuples = list(zip(leaves, txs, included_hashes))
                tx_tuples_sorted = sorted(tx_tuples, key=lambda t: t[0])
                leaves, txs, included_hashes = map(list, zip(*tx_tuples_sorted))
                log.debug(f"Sorted {len(txs)} transactions to match txsRoot leaf order")
            except Exception as e:
                log.error(
                    f"Failed to compute txsRoot from {len(leaves)} leaves: {e}",
                    exc_info=True,
                )
                txs = []
                included_hashes = []
    
    # Log final tx list before mining
    log.info(
        f"_mine_once: Ready to mine block with {len(txs)} transactions",
        extra={
            "tx_count": len(txs),
            "tx_hashes": [h[:16] + "..." for h in included_hashes[:5]],
            "has_more": len(included_hashes) > 5
        }
    )
    
    # Compute target from theta
    theta_micro = header_template.thetaMicro
    target = _theta_to_target(theta_micro)
    
    # Log mining configuration
    log.info(
        f"Starting PoW mining with {workers} worker(s) for parallel nonce search",
        extra={
            "workers": workers,
            "theta_micro": theta_micro,
            "target_hex": hex(target)[:18] + "...",
        },
    )
    
    # Mining loop: iterate through nonces until we find one that meets the target
    # Cap iterations to avoid infinite loops in tests or misconfigured environments
    DEFAULT_MAX_NONCE = 100_000
    max_nonce = int(os.getenv("ANIMICA_MINER_MAX_NONCE", str(DEFAULT_MAX_NONCE)))

    reward_amount = 0

    def _mine_for_header(
        template: Header,
        target_val: int,
        *,
        start_nonce: int = 0,
    ) -> tuple[int, bytes, int] | None:
        from mining.parallel_nonce_search import parallel_nonce_search, pow_check_nonce

        timeout_s = None
        try:
            timeout_s = float(os.getenv("ANIMICA_MINER_POW_TIMEOUT_S", "0") or 0) or None
        except ValueError:
            timeout_s = None
        max_restarts = int(os.getenv("ANIMICA_MINER_WORKER_RESTARTS", "1") or 1)

        result = parallel_nonce_search(
            pow_check_nonce,
            (template, target_val),
            start_nonce=start_nonce,
            max_nonce=max_nonce,
            workers=workers,
            timeout_s=timeout_s,
            max_restarts=max_restarts,
            log=log if verbose else None,
        )
        if result is None:
            return None
        hash_bytes, hash_int = result.payload
        if verbose:
            elapsed = max(result.elapsed_s, 1e-6)
            estimated_rate = (result.attempts * max(1, workers)) / elapsed
            log.info(
                "Nonce found by worker",
                extra={
                    "worker_id": result.worker_id,
                    "attempts": result.attempts,
                    "elapsed_s": result.elapsed_s,
                    "estimated_hashrate_hs": estimated_rate,
                    "restarts": result.restarts,
                },
            )
        return (result.nonce, hash_bytes, hash_int)

    # Perform nonce search (single-worker or multi-worker based on workers parameter)
    valid_nonce = None
    block_hash_bytes = None
    block_hash_int = None

    # Skip PoW for instant blocks - use nonce=0 immediately
    if instant_block:
        log.info("Instant block mode: skipping PoW, using nonce=0")
        valid_nonce = 0
        # Compute hash with nonce=0
        try:
            header_with_nonce = replace(header_template, nonce=0)
        except Exception:
            header_with_nonce = Header(
                v=header_template.v,
                chainId=header_template.chainId,
                height=header_template.height,
                parentHash=header_template.parentHash,
                timestamp=header_template.timestamp,
                stateRoot=header_template.stateRoot,
                txsRoot=header_template.txsRoot,
                receiptsRoot=header_template.receiptsRoot,
                proofsRoot=header_template.proofsRoot,
                daRoot=header_template.daRoot,
                mixSeed=header_template.mixSeed,
                poiesPolicyRoot=header_template.poiesPolicyRoot,
                pqAlgPolicyRoot=header_template.pqAlgPolicyRoot,
                thetaMicro=header_template.thetaMicro,
                workType=getattr(header_template, 'workType', 0),
                nonce=0,
                extra=header_template.extra,
            )
        block_hash_bytes = header_with_nonce.hash()
        block_hash_int = int.from_bytes(block_hash_bytes, "big")
    else:
        # Normal block: perform PoW
        result = _mine_for_header(header_template, target, start_nonce=0)
        if result:
            valid_nonce, block_hash_bytes, block_hash_int = result

    # Check if we found a valid nonce (or using nonce=0 for instant block)
    if valid_nonce is not None and block_hash_bytes is not None and block_hash_int is not None:
        try:
            latest_head = adapter.get_head()
            latest_height = int(latest_head.get("height") or 0)
            latest_hash_val = latest_head.get("hash") or latest_head.get("hash_hex")
            latest_hash_bytes = _bytes32(latest_hash_val or ZERO32)
        except Exception:
            latest_height = start_head_height
            latest_hash_bytes = start_head_hash_bytes

        if latest_height != start_head_height or latest_hash_bytes != start_head_hash_bytes:
            log.info(
                "Discarding mined block due to head update during mining",
                extra={
                    "start_height": start_head_height,
                    "start_hash": start_head_hash_bytes.hex(),
                    "current_height": latest_height,
                    "current_hash": latest_hash_bytes.hex(),
                },
            )
            _cleanup_tracked_txs(txs)
            return (False, 0, selection_summary)
        # Found a valid block! Now execute txs and generate receipts before persisting.
        # Import Receipt, ReceiptStatus, Log, and BlockEnv at block level (once per mined block)
        from core.types.receipt import Receipt, ReceiptStatus, Log
        from execution.runtime.env import BlockEnv

        state_snap = None
        if getattr(ctx, "state_db", None) is not None:
            snap_fn = getattr(ctx.state_db, "snapshot", None)
            if callable(snap_fn):
                try:
                    state_snap = snap_fn()
                except Exception as e:
                    log.warning("Failed to snapshot state before mining", extra={"error": str(e)})
        
        # Reconstruct the header with the valid nonce
        try:
            header = replace(header_template, nonce=valid_nonce)
        except Exception:
            header = Header(
                v=header_template.v,
                chainId=header_template.chainId,
                height=header_template.height,
                parentHash=header_template.parentHash,
                timestamp=header_template.timestamp,
                stateRoot=header_template.stateRoot,
                txsRoot=header_template.txsRoot,
                receiptsRoot=header_template.receiptsRoot,
                proofsRoot=header_template.proofsRoot,
                daRoot=header_template.daRoot,
                mixSeed=header_template.mixSeed,
                poiesPolicyRoot=header_template.poiesPolicyRoot,
                pqAlgPolicyRoot=header_template.pqAlgPolicyRoot,
                thetaMicro=header_template.thetaMicro,
                workType=getattr(header_template, 'workType', 0),
                nonce=valid_nonce,
                extra=header_template.extra,
            )
        
        # Build block environment for transaction execution
        coinbase_addr = (
            payout_address if payout_address is not None else _get_miner_address()
        )
        block_env = BlockEnv(
            height=header.height,
            timestamp=header.timestamp,
            coinbase=coinbase_addr,
            chain_id=header.chainId,
        )
        
        # Execute all transactions and generate receipts
        # State changes (balance transfers, nonce increments) are persisted via state_db
        # Coinbase transactions are executed first (they're prepended to txs list)
        log.info(f"Executing {len(txs)} transactions for block at height {header.height}")
        receipts_dict = _execute_transactions(
            txs=txs,
            state_db=ctx.state_db,
            block_env=block_env,
            logger=log,
        )
        
        # Convert dict receipts to Receipt objects for compatibility with receiptsRoot computation
        receipts = _convert_receipts_dict_to_objects(receipts_dict)

        # Block reward was already applied via coinbase transaction execution
        # reward_amount was set earlier from _build_coinbase_transactions
        log.info(
            f"Block reward applied via coinbase transaction: {reward_amount} nANM"
        )

        # Compute receipts root (if any receipts) and ensure txs root matches tx set
        receipts_root = ZERO32
        if receipts:
            try:
                leaves = [rcpt.hash() for rcpt in receipts]
                receipts_root = merkle_root(leaves) if leaves else ZERO32
            except Exception as e:
                log.warning(
                    "failed to compute receipts root; defaulting to zero", extra={"err": str(e)}
                )

        # Keep txsRoot from header (already computed from canonical hashes before mining loop)
        # Transaction execution doesn't change the transactions themselves, only generates receipts
        # So txsRoot should remain the same as what was computed before mining started
        txs_root = header.txsRoot

        state_root = _compute_state_root(getattr(ctx, "state_db", None))

        try:
            header = replace(
                header,
                stateRoot=state_root,
                txsRoot=txs_root,
                receiptsRoot=receipts_root,
            )
        except Exception:
            header = Header(
                v=header.v,
                chainId=header.chainId,
                height=header.height,
                parentHash=header.parentHash,
                timestamp=header.timestamp,
                stateRoot=state_root,
                txsRoot=txs_root,
                receiptsRoot=receipts_root,
                proofsRoot=header.proofsRoot,
                daRoot=header.daRoot,
                mixSeed=header.mixSeed,
                poiesPolicyRoot=header.poiesPolicyRoot,
                pqAlgPolicyRoot=header.pqAlgPolicyRoot,
                thetaMicro=header.thetaMicro,
                nonce=header.nonce,
                extra=header.extra,
            )

        # Re-check PoW after header roots are finalized (state/receipts updates change hash).
        final_hash_bytes = header.hash()
        final_hash_int = int.from_bytes(final_hash_bytes, "big")
        if final_hash_int > target:
            log.warning(
                "Mined nonce invalid after header root update; reminting",
                extra={
                    "height": header.height,
                    "old_hash": block_hash_bytes.hex() if block_hash_bytes else None,
                    "new_hash": final_hash_bytes.hex(),
                    "target": hex(target),
                },
            )
            retry_windows = int(
                os.getenv("ANIMICA_MINER_POW_RETRY_WINDOWS", "4") or 4
            )
            start_nonce = 0
            remine_result = None
            for _ in range(max(1, retry_windows)):
                remine_result = _mine_for_header(
                    header, target, start_nonce=start_nonce
                )
                if remine_result is not None:
                    break
                start_nonce += max_nonce
            if remine_result is None:
                log.error(
                    "Failed to remine block after header root update",
                    extra={"height": header.height, "target": hex(target)},
                )
                _cleanup_tracked_txs(txs)
                return (False, 0, selection_summary)
            valid_nonce, block_hash_bytes, block_hash_int = remine_result
            try:
                header = replace(header, nonce=valid_nonce)
            except Exception:
                header = Header(
                    v=header.v,
                    chainId=header.chainId,
                    height=header.height,
                    parentHash=header.parentHash,
                    timestamp=header.timestamp,
                    stateRoot=header.stateRoot,
                    txsRoot=header.txsRoot,
                    receiptsRoot=header.receiptsRoot,
                    proofsRoot=header.proofsRoot,
                    daRoot=header.daRoot,
                    mixSeed=header.mixSeed,
                    poiesPolicyRoot=header.poiesPolicyRoot,
                    pqAlgPolicyRoot=header.pqAlgPolicyRoot,
                    thetaMicro=header.thetaMicro,
                    nonce=valid_nonce,
                    extra=header.extra,
                )
            final_hash_bytes = header.hash()
            final_hash_int = int.from_bytes(final_hash_bytes, "big")
            block_hash_bytes = final_hash_bytes
            block_hash_int = final_hash_int
        else:
            block_hash_bytes = final_hash_bytes
            block_hash_int = final_hash_int

        # Build block with updated header and receipts.
        # Verification should succeed because txsRoot is derived from tx.hash() using
        # the same canonical helper as Block.txs_root().
        block = Block.from_components(
            header=header, txs=txs, proofs=(), receipts=receipts, verify=True
        )
        
        # Persist block directly using block_db's atomic method
        # This ensures the block is stored and marked canonical in one transaction
        try:
            block_db = ctx.block_db
            if hasattr(block_db, "append_canonical_block"):
                block_db.append_canonical_block(header.height, block)
                accepted = True
                log.info(f"Block persisted via append_canonical_block at height {header.height}")

                # CRITICAL FIX: Re-index receipts using canonical tx hashes
                # append_canonical_block indexes receipts using tx.hash() which re-encodes,
                # but we need to index them using the canonical hash from raw CBOR.
                # This ensures tx.getTransactionReceipt can find receipts using the hash
                # returned by tx.sendRawTransaction.
                if txs and hasattr(block_db, "kv"):
                    try:
                        from core.encoding.cbor import dumps as cbor_dumps
                        # Re-index receipts with canonical hashes
                        with block_db.kv.batch() as batch:
                            for idx, tx in enumerate(txs):
                                # Get canonical hash from tracked raw bytes
                                tracked = _tracked(tx)
                                if tracked:
                                    tx_hash_hex, raw = tracked
                                    tx_hash = bytes.fromhex(tx_hash_hex[2:])  # strip "0x" prefix

                                    # Store receipt pointer using canonical hash
                                    # Format: PFX_RXI + tx_hash → {"h": height, "i": idx, "b": block_hash}
                                    receipt_ptr = cbor_dumps({"h": header.height, "i": idx, "b": block_hash_bytes})
                                    batch.put(PFX_RXI + tx_hash, receipt_ptr)
                                    log.debug(f"Re-indexed receipt for canonical hash: {tx_hash_hex[:16]}...")
                            batch.commit()
                        log.info(f"Re-indexed {len(txs)} receipts with canonical tx hashes")
                    except Exception as e:
                        log.warning(f"Failed to re-index receipts with canonical hashes: {e}")
            else:
                # Fallback to adapter
                accepted = adapter.submit_block(block)
                log.info(f"Block submitted via adapter: accepted={accepted}")
        except Exception as e:
            log.error(f"Block persistence failed: {e}", exc_info=True)
            accepted = False

        if accepted:
            try:
                if hasattr(block_db, "get_canonical_hash"):
                    canonical_hash = block_db.get_canonical_hash(header.height)
                    if canonical_hash is not None and canonical_hash != block_hash_bytes:
                        log.error(
                            "Invariant violation: mined block not canonical after persistence",
                            extra={
                                "height": header.height,
                                "canonical_hash": canonical_hash.hex(),
                                "block_hash": block_hash_bytes.hex(),
                            },
                        )
                        accepted = False
            except Exception as e:
                log.warning("Failed to verify canonical block after persistence", extra={"error": str(e)})

        if not accepted and state_snap is not None:
            try:
                ctx.state_db.revert(state_snap)
                log.warning(
                    "Reverted state after non-canonical or failed block persistence",
                    extra={"height": header.height},
                )
            except Exception as e:
                log.error("Failed to revert state after block rejection", extra={"error": str(e)})
        
        if accepted:
            _record_local_block(header.height, "0x" + block_hash_bytes.hex(), header)
            _relay_mined_block(block_hash_bytes)
            
            # Update mining state for theta adjustment
            _MINING_STATE["last_block_time"] = time.time()

            included_hashes_canonical: list[str] = []
            if txs:
                included_hashes_canonical = [_canonical_txid_hex(tx) for tx in txs]

            try:
                if mempool_service is not None and included_hashes_canonical:
                    removed = mempool_service.remove_included(included_hashes_canonical)
                    mempool_service.revalidate()
                    log.info(
                        "Evicted included txs from mempool",
                        extra={"removed": removed},
                    )
                from mempool import on_block_accepted

                reconcile_result = on_block_accepted(
                    block, getattr(ctx, "state_db", None), tx_hashes=included_hashes_canonical
                )
                if reconcile_result:
                    log.info(
                        "Reconciled mempool after block acceptance",
                        extra=reconcile_result,
                    )
            except Exception as e:
                log.warning(f"Failed to reconcile mempool after mining: {e}")

            try:
                for tx in txs:
                    _TX_HASH_MAP.pop(id(tx), None)
            except Exception as e:
                log.warning(f"Failed to clean up hash mapping: {e}")

            # INVARIANT CHECK: Verify reward was credited correctly
            # This is a critical check to ensure mining economics are correct
            payout_addr_bytes = (
                payout_address if payout_address is not None else _get_miner_address()
            )
            try:
                from execution.state.apply_balance import get_balance as get_balance_from_state
                final_balance = get_balance_from_state(ctx.state_db, payout_addr_bytes)
                block_hash_hex = "0x" + block_hash_bytes.hex()
                
                # Record in mining audit trail
                _record_mining_audit(
                    height=header.height,
                    block_hash=block_hash_bytes,
                    parent_hash=header.parentHash,
                    miner_address=payout_addr_bytes,
                    expected_reward=reward_amount,
                    credited_reward=final_balance,  # Note: this is total balance, not delta
                    state_root=header.stateRoot,
                )
                
                # Check if reward was applied (we track the expected reward amount)
                # For addresses with no prior transactions in this block, final_balance should >= reward_amount
                # We can't do exact equality because:
                # 1. Address may have had prior balance
                # 2. Transactions in this block may have spent from the address
                # But we CAN verify that reward_amount > 0 implies final_balance > 0
                if reward_amount > 0 and final_balance == 0:
                    # CRITICAL: Reward was supposed to be credited but balance is zero
                    # This should NEVER happen
                    log.error(
                        f"INVARIANT VIOLATION: Block reward not credited! "
                        f"height={header.height}, reward={reward_amount}, balance={final_balance}, "
                        f"coinbase={payout_addr_bytes.hex()[:16]}..., hash={block_hash_hex}",
                        extra={
                            "height": header.height,
                            "expected_reward": reward_amount,
                            "actual_balance": final_balance,
                            "coinbase": payout_addr_bytes.hex(),
                            "block_hash": block_hash_hex,
                        }
                    )
                    # Don't fail mining, but log prominently so this is caught
                
                log.info(
                    f"ACCEPTED: Block mined and reward credited | "
                    f"height={header.height} | "
                    f"hash={block_hash_hex} | "
                    f"coinbase={payout_addr_bytes.hex()[:16]}... | "
                    f"reward={reward_amount} nANM | "
                    f"new_balance={final_balance} nANM | "
                    f"txs={len(txs)} | receipts={len(receipts) if receipts else 0}"
                )
                
            except Exception as e:
                log.warning(f"Failed to query balance after mining: {e}")
                log.info(
                    f"ACCEPTED: Block mined (balance check failed) | "
                    f"height={header.height} with nonce {valid_nonce} "
                    f"(hash {block_hash_int} <= target {target}), reward={reward_amount} nANM, "
                    f"txs={len(txs)}, receipts={len(receipts) if receipts else 0}, "
                    f"included_tx_hashes={included_hashes_canonical[:MAX_DISPLAYED_TX_HASHES]}"
                    f"{' ...' if len(included_hashes_canonical) > MAX_DISPLAYED_TX_HASHES else ''}"
                )
            
            return (True, reward_amount, selection_summary)
        return (False, 0, selection_summary)
    
    # Failed to mine a valid block within max_nonce iterations
    log.warning(
        f"Failed to mine block at height {parent_height + 1} after {max_nonce} attempts "
        f"(target: {target}, theta: {theta_micro})"
    )
    
    # Clean up hash map entries for transactions that weren't successfully mined
    # to prevent memory leaks
    try:
        for tx in txs:
            _TX_HASH_MAP.pop(id(tx), None)
    except Exception as e:
        log.warning(f"Failed to clean up hash mapping after mining failure: {e}")
    
    return (False, 0, selection_summary)




async def _auto_mine_loop(interval: float = 1.0) -> None:
    global _AUTO_MINE
    while _AUTO_MINE:
        try:
            _mine_once()
        except Exception:
            pass
        await asyncio.sleep(interval)


def _start_auto_task() -> bool:
    global _AUTO_TASK, _WATCHDOG_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _AUTO_TASK is None or _AUTO_TASK.done():
        _AUTO_TASK = loop.create_task(_auto_mine_loop())
    if _WATCHDOG_TASK is None or _WATCHDOG_TASK.done():
        _WATCHDOG_TASK = loop.create_task(_watchdog_loop())
    return True


@method(
    "miner.getWork",
    desc="Return a mining work template for Stratum/CPU miners",
    aliases=("miner_getWork",),
)
def miner_get_work(params: Any | None = None) -> Dict[str, Any]:
    from mining.templates import TemplateBuilder
    from mining.share_submitter import json_sanitize

    algo_hint: str | None = None
    if params is None:
        payload: dict[str, Any] | None = None
    elif isinstance(params, dict):
        payload = (
            params.get("payload")
            if len(params) == 1 and "payload" in params
            else params
        )
    elif isinstance(params, (list, tuple)):
        params_list = list(params)
        if len(params_list) == 0:
            payload = None
        elif len(params_list) == 1 and isinstance(params_list[0], dict):
            payload = params_list[0]
        elif len(params_list) == 1:
            payload = None
            algo_hint = str(params_list[0])
        else:
            raise ValueError("expected at most one param: optional algo hint")
    elif isinstance(params, str):
        payload = None
        algo_hint = params
    else:
        raise ValueError("params must be array or object")

    if payload:
        algo_hint = str(
            payload.get("algo")
            or payload.get("algorithm")
            or algo_hint
            or "asic_sha256"
        )
    elif algo_hint is None:
        algo_hint = "asic_sha256"

    allowed, reason = _mining_gate()
    if not allowed:
        return _mining_disabled_payload(reason)

    try:
        from mining.da_adapter import get_da_root
    except Exception:  # pragma: no cover
        get_da_root = None

    tb = TemplateBuilder(
        get_head_info=_head_info,
        get_theta=_resolve_theta,
        get_policy_roots=_policy_roots,
        get_beacon=_beacon,
        da_root_supplier=get_da_root if get_da_root is not None else None,
    )
    proof_type = "sha256d"
    if payload:
        proof_type = str(payload.get("proof") or payload.get("proofType") or proof_type)
    job = tb.current_job(force=True, proof_type=proof_type)

    theta = job.theta_target_micro
    block_target = job.target
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(share_microtarget(theta, shares_per_block=1)) / float(
                theta or 1
            )
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    header_dict = asdict(job.header)
    # asdict preserves bytes; coerce to hex for JSON clients
    header_view = {
        k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header_dict.items()
    }

    try:
        sign_bytes = job.header.to_sign_bytes()
    except Exception:
        # msgspec may not be available in lightweight environments; fall back
        # to a deterministic JSON encoding with hex-encoded bytes.
        import json

        body = {
            k: (v if not isinstance(v, (bytes, bytearray)) else v.hex())
            for k, v in header_dict.items()
        }
        sign_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    head_snapshot = _current_head_snapshot()
    now = time.time()
    for cached_job_id, cached in list(_JOB_CACHE.items()):
        created_at = float(cached.get("created_at") or 0)
        if (
            created_at < now - _JOB_CACHE_TIMEOUT_S
            or cached.get("head_generation") != head_snapshot.get("generation")
        ):
            _JOB_CACHE.pop(cached_job_id, None)

    job_id = job.job_id
    _JOB_CACHE[job_id] = {
        "job": job,
        "sign_bytes": sign_bytes,
        "block_target": block_target,
        "share_target": share_target,
        "height": int(job.header.number),
        "created_at": time.time(),
        "parent_hash": job.parent_hash,
        "parent_height": job.parent_height,
        "chain_id": int(job.chain_id),
        "head_generation": head_snapshot.get("generation"),
    }

    # Check mempool status for diagnostic compatibility with diagnose_tx_propagation.py
    # The diagnostic script passes include_mempool param to verify mining includes mempool txs
    include_mempool_requested = True  # Default to True
    if payload:
        include_mempool_requested = bool(
            payload.get("include_mempool", payload.get("includeMempool", True))
        )
    
    # Get mempool transaction count if service is available
    # Note: miner.getWork returns a header template without actual tx inclusion,
    # but we report mempool status for diagnostic purposes
    tx_count = 0
    mempool_service = _resolve_mempool_service(_ctx())
    if mempool_service is not None and include_mempool_requested:
        try:
            stats = mempool_service.stats() if hasattr(mempool_service, "stats") else {}
            tx_count = stats.get("count", 0)
        except Exception:
            tx_count = 0

    return {
        "jobId": job_id,
        "templateId": job_id,
        "header": header_view,
        "thetaMicro": int(theta),
        "miningEnabled": True,
        "mempoolEnabled": include_mempool_requested,
        "txCount": tx_count,
        "shareTarget": float(share_target),
        "target": hex(block_target),
        "height": int(job.header.number),
        "parentHash": _to_hex(job.parent_hash),
        "parentHeight": int(job.parent_height),
        "chainId": int(job.chain_id),
        "createdAt": int(time.time()),
        "expiresAt": int(job.expires_at) if job.expires_at else None,
        "headGeneration": head_snapshot.get("generation"),
        "hints": {"mixSeed": _to_hex(job.header.mix_seed)},
        "signBytes": _to_hex(sign_bytes),
        "algo": algo_hint,
        "proofType": proof_type,
        "challenge": json_sanitize(job.challenge),
        "scriptHash": job.script_hash,
        "inputsCommit": job.inputs_commit,
        "outputsCommit": job.outputs_commit,
        "templateVersion": int(job.template_version),
    }


@method(
    "miner.submitWork",
    desc="Validate and accept a mined solution",
    aliases=("miner_submitWork", "miner.submit_work"),
)
def miner_submit_work(*args: Any, **payload: Any) -> Dict[str, Any]:
    positional = list(args)
    if (
        not positional
        and "args" in payload
        and isinstance(payload["args"], (list, tuple))
    ):
        positional = list(payload.pop("args"))

    if (
        payload
        and len(payload) == 1
        and "payload" in payload
        and isinstance(payload["payload"], dict)
    ):
        payload = payload["payload"]
    elif payload:
        payload = payload
    elif positional:
        if len(positional) == 1 and isinstance(positional[0], dict):
            payload = positional[0]
        elif len(positional) in (2, 3):
            payload = {"jobId": positional[0], "nonce": positional[1]}
            if len(positional) == 3:
                payload["digest"] = positional[2]
        else:
            raise ValueError("params must be an object or [jobId, nonce, digest]")
    else:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError("params must be an object or array")

    job_id = payload.get("jobId") or payload.get("job_id")
    nonce_val = payload.get("nonce")
    if not job_id or nonce_val is None:
        raise ValueError("jobId and nonce are required")

    job = _JOB_CACHE.get(str(job_id))
    if job is None:
        raise ValueError("unknown or stale jobId")

    current_head = _current_head_snapshot()
    head_height = int(current_head.get("height") or 0)
    head_hash_hex = current_head.get("hash")
    head_generation = current_head.get("generation")
    if head_generation != job.get("head_generation"):
        _JOB_CACHE.pop(str(job_id), None)
        return {
            "accepted": False,
            "jobId": job_id,
            "stale": True,
            "reason": "stale-head",
            "head": {"height": head_height, "hash": head_hash_hex},
        }

    # Guard against stale work: if the head advanced to this height or beyond,
    # or parent differs from current canonical head, reject and evict the job.
    if head_height >= int(job.get("height", 0)):
        _JOB_CACHE.pop(str(job_id), None)
        return {
            "accepted": False,
            "jobId": job_id,
            "stale": True,
            "reason": "stale-height",
            "head": {"height": head_height, "hash": head_hash_hex},
        }
    if head_hash_hex:
        head_hash_bytes = bytes.fromhex(
            head_hash_hex[2:] if head_hash_hex.startswith("0x") else head_hash_hex
        )
        if job.get("parent_hash") and head_hash_bytes != job.get("parent_hash"):
            window = int(os.getenv("ANIMICA_MINER_REORG_WINDOW", "6"))
            in_window = False
            try:
                in_window = _parent_within_canonical_window(
                    block_db=_ctx().block_db,
                    parent_hash=job.get("parent_hash"),
                    head_height=head_height,
                    window=window,
                )
            except Exception:
                in_window = False
            if not in_window:
                _JOB_CACHE.pop(str(job_id), None)
                return {
                    "accepted": False,
                    "jobId": job_id,
                    "stale": True,
                    "reason": "stale-parent",
                    "head": {"height": head_height, "hash": head_hash_hex},
                }

    nonce = _parse_nonce(nonce_val)
    sign_bytes: bytes = job["sign_bytes"]
    digest = hashlib.sha3_256(sign_bytes + nonce).digest()
    digest_int = int.from_bytes(digest, "big")

    accepted = digest_int <= int(job["block_target"])
    block_hash = "0x" + digest.hex()
    res: Dict[str, Any] = {
        "accepted": bool(accepted),
        "jobId": job_id,
        "hash": block_hash,
        "target": hex(int(job["block_target"])),
    }

    if not accepted:
        res["reason"] = "target-not-met"
        return res

    # Check if this block hash already exists (duplicate)
    try:
        ctx = _ctx()
        if hasattr(ctx, 'block_db') and ctx.block_db:
            # Convert block_hash to bytes for lookup
            block_hash_bytes = bytes.fromhex(block_hash[2:] if block_hash.startswith("0x") else block_hash)
            existing_header = ctx.block_db.get_header_by_hash(block_hash_bytes)
            if existing_header is not None:
                # Block already exists - mark as duplicate
                res["duplicate"] = True
                res["reason"] = "duplicate"
                res["height"] = int(job.get("height", 0))
                res["newHead"] = {"height": int(job.get("height", 0)), "hash": block_hash}
                _JOB_CACHE.pop(str(job_id), None)
                return res
    except Exception as e:
        # If duplicate check fails, log but continue (don't block submission)
        log.debug("Failed to check for duplicate block: %s", e)

    # Record the new head locally for lightweight test chains.
    try:
        header_obj = job["job"].header  # type: ignore[index]
        header_view = asdict(header_obj)
    except Exception:
        header_obj = None
        header_view = None

    _JOB_CACHE.pop(str(job_id), None)
    _record_local_block(
        int(job.get("height", 0)), block_hash, header_view or header_obj
    )
    res.update(
        {
            "reason": None,
            "height": int(job.get("height", 0)),
            "newHead": {"height": int(job.get("height", 0)), "hash": block_hash},
            "duplicate": False,
        }
    )
    return res


@method("miner.mine", desc="Mine up to N blocks locally")
def miner_mine(
    count: int | None = None,
    address: str | None = None,
    workers: int | None = None,
    threads: int | None = None,
    include_mempool: bool | None = None,
    allow_offline_mining: bool | None = None,
    allow_unsynced_mining: bool | None = None,
    force_empty_template: bool | None = None,
    instant_block: bool | None = None,
    verbose: bool | None = None,
) -> dict[str, int | list[dict[str, int]] | dict[str, Any]]:
    """
    Mine N blocks locally with dynamic theta micro adjustment.
    
    Args:
        count: Number of blocks to mine (default: 1)
        address: Optional payout address (bech32 or hex). If omitted, uses default miner address.
        workers: Optional number of CPU worker processes to use for mining (default: auto).
                 The nonce search space is divided among workers for parallel mining.
        threads: Deprecated alias for workers (for backward compatibility).
        include_mempool: Whether to include pending mempool transactions (default: True).
        allow_offline_mining: Requested offline override (ignored; unsafe overrides disabled).
        allow_unsynced_mining: Requested unsynced override (ignored; unsafe overrides disabled).
        force_empty_template: Force mining without mempool inclusion.
        instant_block: If True, mine instant blocks with zero rewards (default: False).
        
    Returns:
        dict: {
            "mined": int,           # Number of blocks successfully mined
            "height": int,          # Final chain height after mining
            "totalReward": int,     # Total miner reward in nANM across all blocks
            "rewards": [            # Per-block reward details
                {"height": int, "reward": int},
                ...
            ]
        }
    """
    ctx = _ctx()
    try:
        head_before = ctx.get_head()
    except Exception:
        head_before = {"height": None, "hash": None}

    allow_unsynced_flag = bool(allow_unsynced_mining)
    force_empty_flag = bool(force_empty_template)
    instant_block_flag = bool(instant_block)

    def _mempool_has_pending() -> bool:
        if force_empty_flag:
            return False
        try:
            mempool_service = getattr(ctx, "mempool", None)
            if mempool_service is None:
                return False
            stats_fn = getattr(mempool_service, "stats", None)
            if not callable(stats_fn):
                return False
            stats = stats_fn() or {}
            return int(stats.get("count", 0) or 0) > 0
        except Exception:
            return False

    mempool_instant_mode = (
        include_mempool is not False and not instant_block_flag and _mempool_has_pending()
    )
    if mempool_instant_mode:
        log.info(
            "Enabling instant block mode: pending mempool transactions detected",
            extra={"trigger": "mempool_non_empty"},
        )
    
    if force_empty_flag:
        include_mempool = False
        log.warning(
            "Force empty template enabled; mining without mempool",
            extra={"force_empty_template": True},
        )

    # Instant blocks bypass all safety checks - they are used for immediate tx persistence
    # and do not produce rewards or count towards supply
    if not instant_block_flag and not mempool_instant_mode:
        if allow_offline_mining or allow_unsynced_flag:
            log.warning(
                "Unsafe mining override requested; ignoring.",
                extra={
                    "allow_offline_mining": bool(allow_offline_mining),
                    "allow_unsynced_mining": bool(allow_unsynced_flag),
                },
            )
        allow_offline_flag = False
        allow_unsynced_flag = False

        allowed, reason = _mining_gate(
            allow_offline_mining=allow_offline_flag,
            allow_unsynced=allow_unsynced_flag,
        )
        if not allowed:
            return {
                "mined": 0,
                "height": int(head_before.get("height") or 0),
                "totalReward": 0,
                "rewards": [],
                "disabled": True,
                "reason": reason,
            }
    else:
        # Instant blocks always allowed - bypass mining gate
        log.info("Instant block mode: bypassing mining gate checks")
        allow_offline_flag = False
        allow_unsynced_flag = False
    
    include_mempool_flag = True if include_mempool is None else bool(include_mempool)

    if workers is None and threads is not None:
        workers = threads
    from mining.parallel_nonce_search import resolve_worker_count

    workers = resolve_worker_count(workers)
    log.info(
        "Mining with %d worker(s) for parallel nonce search",
        workers,
        extra={"workers": workers},
    )
    
    # Parse payout address if provided
    payout_address_bytes: bytes | None = None
    if address:
        try:
            # Try to decode as bech32 first
            payout_address_bytes = _decode_bech32_address(address)
            log.info(f"Using custom payout address: {address}")
        except Exception as bech32_err:
            # Try hex fallback (validate length before conversion)
            try:
                addr_str = address[2:] if address.startswith("0x") else address
                # Validate hex string is exactly 64 characters (32 bytes)
                if len(addr_str) != 64:
                    raise ValueError(f"Hex address must be exactly 64 hex characters (32 bytes), got {len(addr_str)}")
                payout_address_bytes = bytes.fromhex(addr_str)
                # No need for second validation: 64 hex chars always => exactly 32 bytes
                log.info(f"Using custom payout address (hex): {address}")
            except Exception as hex_err:
                log.warning(
                    f"Failed to decode payout address '{address}': bech32={bech32_err}, hex={hex_err}. "
                    f"Using default miner address."
                )
                payout_address_bytes = None
    
    log.info(
        "miner.mine request",
        extra={
            "db_uri": getattr(ctx, "cfg", None) and getattr(ctx.cfg, "db_uri", None),
            "chain_id": getattr(ctx, "cfg", None)
            and getattr(ctx.cfg, "chain_id", None),
            "count": count,
            "address": address,
            "head_height": head_before.get("height"),
            "head_hash": head_before.get("hash"),
            "allow_unsynced_mining": allow_unsynced_flag if not instant_block_flag else None,
            "force_empty_template": force_empty_flag,
            "workers": workers,
            "verbose": bool(verbose),
        },
    )
    target = max(1, int(count or 1))
    mined = 0
    total_reward = 0
    rewards_list: list[dict[str, int]] = []
    mempool_pending_before_first = 0
    total_included = 0
    aggregated_rejected: dict[str, int] = {}
    rejected_by_hash_sample: dict[str, str] = {}
    
    retry_delay_s = float(os.getenv("ANIMICA_MINER_MINE_RETRY_DELAY_S", "1.0") or 1.0)
    max_failures = int(os.getenv("ANIMICA_MINER_MINE_MAX_FAILURES", "0") or 0)
    failures = 0
    while mined < target:
        effective_instant_block = bool(instant_block_flag or _mempool_has_pending())
        min_spacing_s = _min_block_spacing_s()
        if min_spacing_s > 0 and not effective_instant_block:
            head_ts = _head_timestamp_seconds()
            if head_ts is not None:
                now = time.time()
                earliest = head_ts + min_spacing_s
                if now < earliest:
                    wait_s = max(0.0, earliest - now)
                    log.info(
                        "MINER_WAIT_COOLDOWN",
                        extra={
                            "wait_s": round(wait_s, 3),
                            "head_height": int(_current_head_snapshot().get("height") or 0),
                        },
                    )
                    if wait_s > 0:
                        time.sleep(wait_s)

        log.info("mining block %d/%d", mined + 1, target)
        mine_result = _mine_once(
            payout_address=payout_address_bytes,
            workers=workers,
            include_mempool=include_mempool_flag,
            allow_offline_mining=allow_offline_flag,
            allow_unsynced_mining=allow_unsynced_flag,
            instant_block=effective_instant_block,
            verbose=bool(verbose),
        )
        if isinstance(mine_result, tuple) and len(mine_result) == 2:
            success, reward_amount = mine_result
            selection_summary = {}
        else:
            success, reward_amount, selection_summary = mine_result
        if success:
            failures = 0
            mined += 1
            total_reward += reward_amount
            mempool_pending_before = selection_summary.get("pending", 0)
            mempool_selected = selection_summary.get("selected", 0)
            mempool_rejected = selection_summary.get("rejected", {})
            mempool_rejected_by_hash = selection_summary.get("rejectedByHash", {})
            if mempool_pending_before and mempool_pending_before_first == 0:
                mempool_pending_before_first = int(mempool_pending_before)
            total_included += int(mempool_selected or 0)
            if isinstance(mempool_rejected, dict):
                for reason, count in mempool_rejected.items():
                    aggregated_rejected[reason] = aggregated_rejected.get(reason, 0) + int(count)
            if isinstance(mempool_rejected_by_hash, dict):
                for tx_hash, reason in mempool_rejected_by_hash.items():
                    if tx_hash not in rejected_by_hash_sample:
                        rejected_by_hash_sample[tx_hash] = reason
                    if len(rejected_by_hash_sample) >= 10:
                        break
            # Get current head to record the height of this block
            head_current = ctx.get_head()
            current_height = int(head_current.get("height") or 0) if isinstance(head_current, dict) else 0
            rewards_list.append({"height": current_height, "reward": reward_amount})
        else:
            failures += 1
            selection_summary = selection_summary or {}
            log.warning(
                "mining attempt failed; retrying",
                extra={"failures": failures, "max_failures": max_failures},
            )
            if max_failures > 0 and failures >= max_failures:
                log.error(
                    "mining aborted after repeated failures",
                    extra={"failures": failures, "max_failures": max_failures},
                )
                break
            if retry_delay_s > 0:
                time.sleep(retry_delay_s)
    
    head = ctx.get_head()
    height = int(head.get("height") or 0) if isinstance(head, dict) else 0
    log.info(
        "miner.mine completed",
        extra={
            "mined": mined,
            "height": height,
            "total_reward": total_reward,
            "head_hash": head.get("hash") if isinstance(head, dict) else None,
        },
    )
    return {
        "mined": mined,
        "height": height,
        "totalReward": total_reward,
        "rewards": rewards_list,
        "mempool": {
            "enabled": include_mempool_flag,
            "pending": mempool_pending_before_first,
            "included": total_included,
            "rejected": aggregated_rejected,
            "rejectedByHash": rejected_by_hash_sample,
        },
    }


@method("miner.getBlockTemplate", desc="Return a block template with mempool selection")
def miner_get_block_template(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    if args and kwargs:
        raise rpc_errors.InvalidParams("expected either positional or named params")

    payload: dict[str, Any] | None = None
    include_mempool_flag = True
    payout_address = None
    allow_offline_mining = False
    allow_unsynced_mining = False
    force_empty_template = False
    raw_params: dict[str, Any] | list[Any] | None = None
    template_ttl_s = _TEMPLATE_TTL_S

    if args:
        raw_params = list(args)
        if len(args) == 1 and isinstance(args[0], dict):
            payload = args[0]
        else:
            if len(args) > 2:
                raise rpc_errors.InvalidParams("expected at most 2 positional arguments")
            payout_address = args[0]
            if len(args) > 1:
                include_mempool_flag = bool(args[1])
    elif kwargs:
        raw_params = dict(kwargs)
        if "payload" in kwargs:
            if len(kwargs) != 1:
                raise rpc_errors.InvalidParams("payload must be the only named argument")
            payload = kwargs["payload"]
        else:
            payload = kwargs

    if payload is not None:
        if not isinstance(payload, dict):
            raise rpc_errors.InvalidParams("params must be an object")
        include_mempool_flag = bool(
            payload.get("include_mempool", payload.get("includeMempool", include_mempool_flag))
        )
        payout_address = payload.get("address") or payload.get("payout_address") or payout_address
        allow_offline_mining = bool(
            payload.get("allow_offline_mining", payload.get("allowOfflineMining", False))
        )
        allow_unsynced_mining = bool(
            payload.get("allow_unsynced_mining", payload.get("allowUnsyncedMining", False))
        )
        force_empty_template = bool(
            payload.get("force_empty_template", payload.get("forceEmptyTemplate", False))
        )
        ttl_raw = payload.get("ttl_seconds", payload.get("ttlSeconds", template_ttl_s))
        try:
            template_ttl_s = max(5.0, min(float(ttl_raw), 300.0))
        except Exception:
            template_ttl_s = _TEMPLATE_TTL_S
        unknown = set(payload.keys()) - {
            "address",
            "payout_address",
            "include_mempool",
            "includeMempool",
            "allow_offline_mining",
            "allowOfflineMining",
            "allow_unsynced_mining",
            "allowUnsyncedMining",
            "force_empty_template",
            "forceEmptyTemplate",
            "ttl_seconds",
            "ttlSeconds",
        }
        if unknown:
            raise rpc_errors.InvalidParams(
                f"unexpected params: {', '.join(sorted(unknown))}"
            )

    if not payout_address:
        raise rpc_errors.InvalidParams("address is required")
    payout_address = _validate_payout_address(payout_address)

    if allow_offline_mining or allow_unsynced_mining:
        log.warning(
            "Unsafe mining override requested; ignoring.",
            extra={
                "allow_offline_mining": bool(allow_offline_mining),
                "allow_unsynced_mining": bool(allow_unsynced_mining),
            },
        )
    allow_offline_mining = False
    allow_unsynced_mining = False

    log.info(
        "miner.getBlockTemplate request",
        extra={
            "params": raw_params or payload,
            "include_mempool": include_mempool_flag,
            "allow_offline_mining": allow_offline_mining,
            "allow_unsynced_mining": allow_unsynced_mining,
            "force_empty_template": force_empty_template,
            "payout_address": payout_address,
            "template_ttl_s": template_ttl_s,
        },
    )

    if force_empty_template:
        include_mempool_flag = False
        log.warning(
            "Force empty template enabled; building without mempool",
            extra={"force_empty_template": True},
        )

    allowed, reason = _mining_gate(
        allow_offline_mining=allow_offline_mining,
        allow_unsynced=allow_unsynced_mining,
    )
    if not allowed:
        if reason and reason.startswith("sync_phase:"):
            log.info(
                "MINER_WAIT_TEMPLATE",
                extra={"sync_phase": reason.split(":", 1)[1]},
            )
        return {"enabled": False, "reason": reason}

    min_spacing_s = _min_block_spacing_s()
    if min_spacing_s > 0:
        head_ts = _head_timestamp_seconds()
        if head_ts is not None:
            now = time.time()
            earliest = head_ts + min_spacing_s
            if now < earliest:
                wait_s = max(0.0, earliest - now)
                snap = _current_head_snapshot()
                log.info(
                    "MINER_WAIT_TEMPLATE",
                    extra={
                        "reason": "min_block_spacing",
                        "wait_s": round(wait_s, 3),
                        "head_height": int(snap.get("height") or 0),
                        "head_hash": snap.get("hash"),
                    },
                )
                return {
                    "enabled": False,
                    "reason": "min_block_spacing",
                    "waitSeconds": round(wait_s, 3),
                    "head": {"height": int(snap.get("height") or 0), "hash": snap.get("hash")},
                }

    try:
        ctx = _ctx()
        adapter = _adapter()
        mempool_service = _resolve_mempool_service(ctx)
        pending_entries: list[PendingTxEntry] = []
        pending_raw_by_hash: dict[str, bytes] = {}
        selection_summary: dict[str, Any] = {
            "pending": 0,
            "selected": 0,
            "rejected": {},
            "rejectedByHash": {},
            "rejectedDetailsByHash": {},
            "mempoolEnabled": include_mempool_flag,
        }

        if include_mempool_flag:
            # Sync mempools from all peers to ensure we have transactions from all nodes
            synced_peers = _sync_all_peer_mempools(timeout_s=1.5)
            if synced_peers > 0:
                log.info(
                    "Synced peer mempools before building block template",
                    extra={"peers_synced": synced_peers},
                )
            
            pending_entries, pending_raw_by_hash, pending_total = _collect_mempool_entries(
                ctx=ctx,
                adapter=adapter,
                limit=1000,
            )
            log.info(
                "block template mempool collection",
                extra={
                    "entries": len(pending_entries),
                    "total": pending_total,
                    "source": "service" if mempool_service is not None else "adapter",
                    "mempool_id": id(mempool_service) if mempool_service is not None else "None",
                },
            )
            if not pending_entries:
                requested = _request_missing_mempool_txs(limit=128, wait_s=0.25)
                if requested:
                    log.info(
                        "block template requested missing txids",
                        extra={"requested": requested},
                    )
                    time.sleep(0.25)
                    pending_entries, pending_raw_by_hash, pending_total = (
                        _collect_mempool_entries(
                            ctx=ctx,
                            adapter=adapter,
                            limit=1000,
                        )
                    )
                    log.info(
                        "block template mempool collection (after fetch)",
                        extra={
                            "entries": len(pending_entries),
                            "total": pending_total,
                            "source": "service" if mempool_service is not None else "adapter",
                            "mempool_id": id(mempool_service)
                            if mempool_service is not None
                            else "None",
                        },
                    )
        else:
            pending_total = 0

        if include_mempool_flag and not pending_entries and mempool_service is None:
            try:
                from rpc.methods import tx as tx_methods

                pend = getattr(tx_methods, "_PEND", None)
                pending_map = {}

                if pend is not None:
                    if hasattr(pend, "items") and callable(pend.items):
                        pending_map = dict(pend.items())
                    elif hasattr(pend, "list_raw") and callable(pend.list_raw):
                        pending_map = dict(pend.list_raw())

                if not pending_map:
                    pending_map = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}

                log.warning(
                    "miner.getBlockTemplate: FALLBACK - using _PEND/_FALLBACK_PENDING, entries=%d (mempool_service was None)",
                    len(pending_map),
                )

                for tx_hash_hex, raw in pending_map.items():
                    try:
                        decoded, _obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                        tx_obj = None
                        if isinstance(decoded, Tx):
                            tx_obj = decoded
                        elif isinstance(decoded, dict):
                            normalized = _normalize_tx_envelope(decoded)
                            tx_obj = _construct_tx_from_dict(normalized)
                        if tx_obj is None:
                            continue
                        tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
                        pending_raw_by_hash[tx_hash_hex] = raw
                        pending_entries.append(
                            PendingTxEntry(hash_hex=tx_hash_hex, raw=raw, tx=None)
                        )
                    except Exception:
                        continue
            except Exception as e:
                log.error("Fallback pending pool unavailable", extra={"err": str(e)}, exc_info=True)

        if include_mempool_flag and pending_entries:
            normalized_entries: list[PendingTxEntry] = []
            for entry in pending_entries:
                tx = entry.tx
                if tx is None:
                    normalized_entries.append(entry)
                    continue
                tx_normalized = _attach_sender_if_possible(tx)
                if tx_normalized is not tx:
                    tracked = _tracked(tx)
                    if tracked:
                        _TX_HASH_MAP[id(tx_normalized)] = tracked
                normalized_entries.append(
                    PendingTxEntry(
                        hash_hex=entry.hash_hex, raw=entry.raw, tx=tx_normalized
                    )
                )

            decode_fn = None
            try:
                from rpc.methods import tx as tx_methods

                decode_fn = tx_methods._decode_tx  # type: ignore[attr-defined]
            except Exception:
                decode_fn = None

            min_gas_price = 0
            try:
                min_gas_price = int(ctx.params.get("min_gas_price", 0))
            except Exception:
                min_gas_price = 0

            try:
                from rpc.methods import tx as tx_methods
            except Exception:
                tx_methods = None  # type: ignore[assignment]

            def _signature_validator(tx_obj: Any, decoded_obj: dict[str, Any] | None) -> None:
                if tx_methods is None:
                    return
                if decoded_obj is None:
                    return
                tx_methods._verify_pq_signature(  # type: ignore[attr-defined]
                    tx_obj, decoded_obj, chain_id=_resolve_chain_id_for_sig(ctx)
                )

            selection = select_for_block(
                head_state=_template_head_state(
                    ctx=ctx, adapter=adapter, phase="block_template"
                ),
                limits={
                    "max_gas": DEFAULT_BLOCK_GAS_LIMIT,
                    "max_bytes": DEFAULT_BLOCK_BYTE_LIMIT,
                    "max_txs": 1000,
                },
                pending=normalized_entries,
                decode=decode_fn,
                state_db=getattr(ctx, "state_db", None),
                policy={"min_gas_price": min_gas_price},
                tx_index=getattr(ctx, "tx_index", None),
                signature_validator=_signature_validator,
            )
            txs, included_hashes, dropped_counts, dropped_by_hash, dropped_details = (
                _coerce_selected_txs(
                    selected=list(selection.selected),
                    selected_hashes=list(selection.selected_hashes),
                    pending_raw_by_hash=pending_raw_by_hash,
                    decode_fn=decode_fn,
                )
            )
            merged_rejected = dict(selection.rejected)
            for reason, count in dropped_counts.items():
                merged_rejected[reason] = merged_rejected.get(reason, 0) + int(count)
            merged_rejected_by_hash = dict(selection.rejected_by_hash)
            merged_rejected_by_hash.update(dropped_by_hash)
            merged_rejected_details_by_hash = dict(selection.rejected_details_by_hash)
            merged_rejected_details_by_hash.update(dropped_details)
            selection_summary = {
                "pending": selection.total_pending,
                "candidates": len(pending_entries),
                "mempoolTotal": pending_total,
                "selected": len(txs),
                "rejected": dict(merged_rejected),
                "rejectedCount": sum(int(v) for v in merged_rejected.values()),
                "rejectedByHash": dict(list(merged_rejected_by_hash.items())[:10]),
                "rejectedDetailsByHash": dict(
                    list(merged_rejected_details_by_hash.items())[:10]
                ),
                "mempoolEnabled": True,
            }
            log.info(
                "TEMPLATE_BUILD",
                extra={
                    "mempool_total": pending_total,
                    "included": len(txs),
                    "rejected": dict(merged_rejected),
                },
            )
            _maybe_log_mempool_debug(
                phase="block_template",
                pending_total=selection.total_pending,
                candidate_count=len(pending_entries),
                included_count=len(txs),
                rejected=merged_rejected,
                rejected_details_by_hash=merged_rejected_details_by_hash,
            )
            if merged_rejected:
                log.info(
                    "Block template mempool selection rejected candidates",
                    extra={
                        "rejected": dict(merged_rejected),
                        "rejected_by_hash_sample": dict(
                            list(merged_rejected_by_hash.items())[:10]
                        ),
                    },
                )
            if selection.total_pending and len(txs) == 0:
                log.warning(
                    "Block template has pending mempool txs but no eligible inclusions",
                    extra={
                        "pending": selection.total_pending,
                        "rejected": dict(merged_rejected),
                    },
                )
        else:
            txs = []
            included_hashes = []

        head = adapter.get_head()
        parent_height = int(head.get("height") or 0)
        parent_hash_val = head.get("hash") or head.get("hash_hex")
        parent_header = head.get("obj") or head.get("header")

        if parent_header is None:
            _maybe_bootstrap = getattr(deps, "startup", None)
            if callable(_maybe_bootstrap):
                try:
                    deps.ensure_started(ctx.cfg)
                    head = adapter.get_head()
                    parent_height = int(head.get("height") or 0)
                    parent_hash_val = head.get("hash") or head.get("hash_hex")
                    parent_header = head.get("obj") or head.get("header")
                except Exception:
                    parent_header = None

        parent_hash_bytes = _bytes32(parent_hash_val or ZERO32)
        if parent_header is None:
            pq_root, poies_root = _policy_roots()
            parent_header = Header(
                v=1,
                chainId=_ctx().cfg.chain_id,
                height=parent_height,
                parentHash=parent_hash_bytes,
                timestamp=int(time.time()),
                stateRoot=ZERO32,
                txsRoot=ZERO32,
                receiptsRoot=ZERO32,
                proofsRoot=ZERO32,
                daRoot=ZERO32,
                mixSeed=ZERO32,
                poiesPolicyRoot=poies_root,
                pqAlgPolicyRoot=pq_root,
                thetaMicro=_resolve_theta(),
                nonce=0,
                extra=b"",
            )

        timestamp_min, timestamp_max, _ = _timestamp_bounds(parent_header)
        
        # Convert payout address to bytes for coinbase
        coinbase_bytes = _as_bytes32_addr(payout_address)
        
        header_template = _build_child_header(parent_height, parent_hash_bytes, parent_header, coinbase=coinbase_bytes)

        network_dt_seconds = None
        try:
            head_timestamp = int(getattr(parent_header, "timestamp", 0) or 0)
            network_dt_seconds = _network_block_interval(parent_height, head_timestamp)
        except Exception:
            network_dt_seconds = None
        if network_dt_seconds is not None:
            adjusted_theta = _adjust_theta_for_mining(network_dt_seconds)
            try:
                header_template = replace(header_template, thetaMicro=adjusted_theta)
            except Exception:
                pass

        if txs:
            leaves = []
            valid_txs = []
            valid_hashes = []
            for i, tx in enumerate(txs):
                try:
                    tx_hash = tx.hash()
                    tx_hash_hex = "0x" + tx_hash.hex()
                    leaves.append(tx_hash)
                    valid_txs.append(tx)
                    if i < len(included_hashes):
                        valid_hashes.append(included_hashes[i])
                    else:
                        valid_hashes.append(tx_hash_hex)
                except Exception:
                    continue
            txs = valid_txs
            included_hashes = valid_hashes
            if leaves:
                try:
                    from core.utils.merkle import compute_txs_root_from_txs

                    txs_root = compute_txs_root_from_txs(txs)
                    header_template = replace(header_template, txsRoot=txs_root)
                    tx_tuples = list(zip(leaves, txs, included_hashes))
                    tx_tuples_sorted = sorted(tx_tuples, key=lambda t: t[0])
                    leaves, txs, included_hashes = map(list, zip(*tx_tuples_sorted))
                except Exception:
                    txs = []
                    included_hashes = []

        header_dict = asdict(header_template)
        header_view = {
            k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
            for k, v in header_dict.items()
        }
        target = _theta_to_target(header_template.thetaMicro)

        tx_payloads: list[dict[str, str]] = []
        raw_by_hash: dict[str, bytes] = {}
        for tx in txs:
            tracked = _tracked(tx)
            if tracked:
                tx_hash_hex, raw = tracked
            else:
                tx_hash_hex = _canonical_txid_hex(tx)
                raw = getattr(tx, "raw_cbor", None) or b""
                if not raw and hasattr(tx, "to_cbor"):
                    try:
                        raw = tx.to_cbor()
                    except Exception:
                        raw = b""
            raw_by_hash[tx_hash_hex] = raw
            tx_payloads.append({"hash": tx_hash_hex, "raw": "0x" + raw.hex()})

        excluded: list[dict[str, Any]] = []
        rejected_details = selection_summary.get("rejectedDetailsByHash", {})
        if rejected_details:
            for tx_hash, detail in list(rejected_details.items())[:10]:
                payload = {"hash": tx_hash}
                if isinstance(detail, dict):
                    payload.update(detail)
                else:
                    payload["reason"] = str(detail)
                excluded.append(payload)

        coinbase = {"address": payout_address, "amount": None}
        try:
            from consensus.rewards import compute_block_reward  # type: ignore[import-not-found]

            rewards = compute_block_reward(
                chain_id=int(ctx.cfg.chain_id),
                height=int(header_template.height),
                params=ctx.params,
            )
            if rewards:
                coinbase["amount"] = int(rewards[0][1])
        except Exception:
            coinbase["amount"] = None

        log.info(
            "miner.getBlockTemplate assembled",
            extra={
                "parent_height": parent_height,
                "parent_hash": _to_hex(parent_hash_bytes),
                "header_height": int(header_template.height),
                "header_timestamp": int(header_template.timestamp),
                "target": hex(int(target)),
                "coinbase": coinbase,
            },
        )

        _prune_template_cache()
        issued_at = time.time()
        expires_at = issued_at + float(template_ttl_s)
        template_id = uuid.uuid4().hex
        head_at_issue = _current_head_snapshot()
        frozen_tx_hashes = [str(item.get("hash")) for item in tx_payloads if isinstance(item, dict) and item.get("hash")]
        with _TEMPLATE_CACHE_LOCK:
            _TEMPLATE_CACHE[template_id] = {
                "created_at": issued_at,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "parent_hash": _to_hex(parent_hash_bytes),
                "parent_height": parent_height,
                "head_hash_at_issue": head_at_issue.get("hash"),
                "head_height_at_issue": head_at_issue.get("height"),
                "target": hex(int(target)),
                "theta_micro": int(header_template.thetaMicro),
                "timestamp_min": int(timestamp_min),
                "timestamp_max": int(timestamp_max) if timestamp_max is not None else None,
                "payout_address": payout_address,
                "tx_root": header_view.get("txsRoot"),
                "tx_hashes": frozen_tx_hashes,
                "mempool_version": int(selection_summary.get("mempoolVersion") or selection_summary.get("version") or 0),
                "chain_identity": f"{ctx.cfg.chain_id}:{getattr(ctx.cfg, 'genesis_path', None)}",
            }
        _metric_inc("templates_issued")
        log.info("template_issued", extra={"template_id": template_id, "parent_hash": _to_hex(parent_hash_bytes), "parent_height": parent_height, "head_hash_at_issue": head_at_issue.get("hash"), "issued_at": int(issued_at), "expires_at": int(expires_at)})

        return {
            "enabled": True,
            "templateId": template_id,
            "parent": {"height": parent_height, "hash": _to_hex(parent_hash_bytes)},
            "header": header_view,
            "target": hex(int(target)),
            "thetaMicro": int(header_template.thetaMicro),
            "timestampMin": int(timestamp_min),
            "timestampMax": int(timestamp_max) if timestamp_max is not None else None,
            "issuedAt": int(issued_at),
            "expiresAt": int(expires_at),
            "headHashAtIssue": head_at_issue.get("hash"),
            "coinbase": coinbase,
            "txs": tx_payloads,
            "excluded": excluded,
            "mempool": selection_summary,
            "address": payout_address,
            "payout_address": payout_address,
        }
    except NameError as exc:
        log.exception(
            "miner.getBlockTemplate NameError",
            extra={
                "params": raw_params or payload,
                "payout_address": payout_address,
                "include_mempool": include_mempool_flag,
                "allow_offline_mining": allow_offline_mining,
            },
        )
        raise rpc_errors.InternalError(
            f"{exc.__class__.__name__}: {exc}",
            reason="NameError",
        ) from exc


@method(
    "miner.start", aliases=("miner_start", "miner.setAutoMine", "animica_setAutoMine")
)
def miner_start(enable: bool | None = None) -> bool:
    global _AUTO_MINE
    _AUTO_MINE = True if enable is None else bool(enable)
    _start_auto_task()
    return _AUTO_MINE


@method("miner.stop", aliases=("miner_stop", "animica_stopAutoMine"))
def miner_stop() -> bool:
    global _AUTO_MINE
    _AUTO_MINE = False
    if _AUTO_TASK is not None:
        _AUTO_TASK.cancel()
    if _WATCHDOG_TASK is not None:
        _WATCHDOG_TASK.cancel()
    return False


def _extract_payload(payload: Any, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract payload from either positional or keyword arguments.
    
    Supports:
    - params=[payload_dict] -> positional dict (from share_submitter)
    - params={key: val, ...} -> keyword arguments (backward compatibility)
    - params=[{"payload": {...}}] -> wrapped payload format (for compatibility with wrapped payloads)
    
    Args:
        payload: First positional argument (None if called with kwargs only)
        kwargs: Keyword arguments
        
    Returns:
        Extracted payload dict, or None if no valid payload provided
    """
    if payload is not None:
        return payload
    elif kwargs:
        # Support wrapped payload format: {"payload": {...}}
        if len(kwargs) == 1 and "payload" in kwargs:
            return kwargs.get("payload")
        # Direct keyword arguments
        return kwargs
    return None


def _process_attached_proofs(
    attached_proofs: list[str],
    job_id: str,
    nonce: bytes,
    mix_seed: bytes,
    height: int,
    miner_address: str,
) -> tuple[list[dict[str, Any]], int]:
    """
    Process attached useful work proofs with strict budgeting and error handling.
    
    Returns:
        (proof_results, total_bonus_credits)
    """
    try:
        from core.usefulwork import (
            decode_proof_from_hex,
            verify_proof,
            ShareContext,
            get_policy,
            UWPDecodeError,
        )
    except ImportError as e:
        log.warning(f"UWP module not available: {e}")
        return [], 0
    
    policy = get_policy()
    
    # Early checks
    if not policy.enabled:
        return [], 0
    
    if len(attached_proofs) > policy.max_proofs_per_share:
        log.warning(f"Too many proofs: {len(attached_proofs)} > {policy.max_proofs_per_share}")
        return [{
            "index": 0,
            "status": "REJECTED",
            "reason": f"Too many proofs (max {policy.max_proofs_per_share})"
        }], 0
    
    # Check total bytes
    total_bytes = sum(len(p.replace("0x", "")) // 2 for p in attached_proofs if isinstance(p, str))
    if total_bytes > policy.max_total_bytes_per_share:
        log.warning(f"Proofs too large: {total_bytes} > {policy.max_total_bytes_per_share}")
        return [{
            "index": 0,
            "status": "REJECTED",
            "reason": f"Total proof bytes exceed {policy.max_total_bytes_per_share}"
        }], 0
    
    # Build share context
    context = ShareContext(
        job_id=job_id,
        nonce=nonce,
        mix_seed=mix_seed,
        height=height,
        miner_address=miner_address,
        timestamp=int(time.time()),
    )
    
    # Process each proof with time budgeting
    proof_results = []
    total_bonus_credits = 0
    time_budget_remaining_ms = policy.max_verify_ms_per_share
    start_time = time.time()
    
    for idx, proof_hex in enumerate(attached_proofs):
        # Check time budget
        elapsed_ms = (time.time() - start_time) * 1000
        if elapsed_ms >= time_budget_remaining_ms:
            proof_results.append({
                "index": idx,
                "status": "SKIPPED_BUDGET",
                "reason": "Verification time budget exhausted",
            })
            continue
        
        # Decode proof
        try:
            proof = decode_proof_from_hex(proof_hex)
        except UWPDecodeError as e:
            proof_results.append({
                "index": idx,
                "status": "REJECTED",
                "reason": f"Decode error: {str(e)[:100]}",
            })
            continue
        except Exception as e:
            log.error(f"Unexpected decode error: {e}", exc_info=True)
            proof_results.append({
                "index": idx,
                "status": "VERIFIER_ERROR",
                "reason": f"Decode error: {str(e)[:100]}",
            })
            continue
        
        # Verify proof
        try:
            result = verify_proof(
                proof=proof,
                context=context,
                time_budget_ms=int(time_budget_remaining_ms - elapsed_ms),
            )
            
            proof_results.append({
                "index": idx,
                "status": result.status.name,
                "statusCode": int(result.status),
                "accepted": result.accepted,
                "reason": result.reason,
                "bonusCredits": result.bonus_credits,
                "metadata": result.metadata,
            })
            
            if result.accepted:
                total_bonus_credits += result.bonus_credits
                
        except Exception as e:
            log.error(f"Verifier exception for proof {idx}: {e}", exc_info=True)
            proof_results.append({
                "index": idx,
                "status": "VERIFIER_ERROR",
                "reason": f"Internal error: {str(e)[:100]}",
            })
    
    return proof_results, total_bonus_credits


@method(
    "miner.submitShare",
    desc="Accept a submitted share from the mining pool",
    aliases=("miner_submitShare",),
)
def miner_submit_share(payload: Any = None, **kwargs: Any) -> Dict[str, Any]:
    # Support both positional (share as first param) and keyword arguments
    # This allows params=[payload_dict] from share_submitter and params={key: val} for backward compatibility
    share = _extract_payload(payload, kwargs)
    if share is None:
        # No payload provided - return rejection response (standard pattern for share submissions in mining pools)
        return {"accepted": False, "reason": "invalid share payload"}
    
    if isinstance(share, list) and share:
        share = share[0]
    if not isinstance(share, dict):
        return {"accepted": False, "reason": "invalid share payload"}

    job_id = (
        share.get("jobId")
        or share.get("job_id")
        or share.get("job")
        or share.get("templateId")
    )
    if not job_id:
        return {"accepted": False, "reason": "missing jobId"}
    cached = _JOB_CACHE.get(str(job_id))
    if not cached:
        return {"accepted": False, "reason": "stale job"}

    head_snapshot = _current_head_snapshot()
    if cached.get("head_generation") != head_snapshot.get("generation"):
        return {"accepted": False, "reason": "stale job (head moved)"}
    if cached.get("parent_hash") != _head_info()[0]:
        return {"accepted": False, "reason": "stale job (parent mismatch)"}

    nonce = share.get("nonce") or share.get("nonce64") or share.get("n")
    try:
        nonce_int = int(nonce, 16) if isinstance(nonce, str) else int(nonce)
    except Exception:
        return {"accepted": False, "reason": "invalid nonce"}

    sign_bytes = cached.get("sign_bytes")
    if not isinstance(sign_bytes, (bytes, bytearray)):
        return {"accepted": False, "reason": "missing sign bytes"}

    mix_seed = share.get("mixSeed") or share.get("mix_seed")
    try:
        if isinstance(mix_seed, str) and mix_seed.startswith("0x"):
            mix_seed_bytes = bytes.fromhex(mix_seed[2:])
        elif isinstance(mix_seed, (bytes, bytearray)):
            mix_seed_bytes = bytes(mix_seed)
        else:
            mix_seed_bytes = b""
    except Exception:
        mix_seed_bytes = b""

    try:
        from mining import nonce_domain as nd  # type: ignore

        digest = nd.sha3_256(
            sign_bytes + mix_seed_bytes + nonce_int.to_bytes(8, "little")
        )
        digest_int = int.from_bytes(digest, "big")
    except Exception:
        import hashlib

        h = hashlib.sha3_256()
        h.update(sign_bytes)
        h.update(mix_seed_bytes)
        h.update(nonce_int.to_bytes(8, "little", signed=False))
        digest = h.digest()
        digest_int = int.from_bytes(digest, "big")

    share_target = float(
        share.get("shareTarget") or cached.get("share_target") or 0.0
    )
    if cached.get("job"):
        theta_default = cached["job"].theta_target_micro
    else:
        theta_default = 0
    theta_micro = int(share.get("thetaMicro") or theta_default)
    t_share_micro = max(0, int(theta_micro * share_target))
    try:
        from mining.hash_search import micro_threshold_to_target256

        share_target_int = micro_threshold_to_target256(t_share_micro)
    except Exception:
        share_target_int = 0
    if share_target_int and digest_int > share_target_int:
        return {"accepted": False, "reason": "low difficulty share"}

    is_block = digest_int <= int(cached.get("block_target") or 0)
    
    # Process attached useful work proofs (Phase 2)
    proof_results = []
    total_bonus_credits = 0
    
    attached_proofs = share.get("attachedProofs") or share.get("attached_proofs") or []
    if attached_proofs and isinstance(attached_proofs, list):
        proof_results, total_bonus_credits = _process_attached_proofs(
            attached_proofs=attached_proofs,
            job_id=str(job_id),
            nonce=nonce_int.to_bytes(8, "little"),
            mix_seed=mix_seed_bytes,
            height=int(cached.get("height") or 0),
            miner_address=share.get("minerAddress") or share.get("miner_address") or "",
        )
    
    result = {
        "accepted": True,
        "reason": None,
        "jobId": job_id,
        "isBlock": is_block,
        "hash": "0x" + digest.hex(),
        "height": int(cached.get("height") or 0),
    }
    
    # Add proof results if any proofs were submitted
    if proof_results:
        result["proofs"] = proof_results
        result["totalBonusCredits"] = total_bonus_credits
    
    return result


@method(
    "miner.submitBlock",
    desc="Accept a candidate block from the miner/pool",
    aliases=("miner_submitBlock",),
)
def miner_submit_block(payload: Any = None, **kwargs: Any) -> Dict[str, Any]:
    # Support both positional (block as first param) and keyword arguments
    # This allows params=[payload_dict] from share_submitter and params={key: val} for backward compatibility
    block = _extract_payload(payload, kwargs)
    if block is None:
        # No payload provided - raise exception (standard pattern for block submissions; differs from shares)
        # Block submissions are critical operations that should fail fast with exceptions
        raise rpc_errors.InvalidParams("invalid block payload")
    
    if isinstance(block, list) and block:
        block = block[0]
    if not isinstance(block, dict):
        raise rpc_errors.InvalidParams("invalid block payload")
    template_id = block.get("templateId") or block.get("template_id")
    raw_txs_hex: list[str] = []
    parent_hash_field = block.get("parentHash") or block.get("parent_hash")
    if isinstance(block.get("header"), dict):
        header_map = dict(block["header"])
        for key, value in list(header_map.items()):
            if isinstance(value, str) and value.startswith("0x"):
                try:
                    header_map[key] = bytes.fromhex(value[2:])
                except Exception:
                    header_map[key] = value
        block["header"] = header_map
    if "txs" in block and isinstance(block["txs"], list):
        normalized_txs = []
        for entry in block["txs"]:
            if isinstance(entry, str) and entry.startswith("0x"):
                raw_txs_hex.append(entry)
                try:
                    from core.encoding.cbor import loads as cbor_loads

                    decoded = cbor_loads(bytes.fromhex(entry[2:]))
                    if isinstance(decoded, dict):
                        normalized_txs.append(decoded)
                        continue
                except Exception:
                    normalized_txs.append(entry)
                    continue
            normalized_txs.append(entry)
        block["txs"] = normalized_txs

    header_parent = None
    try:
        header = block.get("header")
        if isinstance(header, dict):
            header_parent = header.get("parentHash") or header.get("parent_hash")
    except Exception:
        header_parent = None

    if parent_hash_field is None:
        parent_hash_field = header_parent

    if parent_hash_field is None:
        raise rpc_errors.InvalidParams("parent_hash or template_id is required")

    if isinstance(parent_hash_field, str):
        parent_hash_bytes = _bytes32(parent_hash_field)
        parent_hash_hex = _to_hex(parent_hash_bytes)
    elif isinstance(parent_hash_field, (bytes, bytearray)):
        parent_hash_bytes = _bytes32(parent_hash_field)
        parent_hash_hex = _to_hex(parent_hash_bytes)
    else:
        raise rpc_errors.InvalidParams("parent_hash must be hex string or bytes")

    _prune_template_cache()
    cached = None
    if template_id:
        with _TEMPLATE_CACHE_LOCK:
            cached = _TEMPLATE_CACHE.get(str(template_id))
        _metric_inc("templates_submitted")
        if not cached:
            _metric_inc("stale_rejects")
            raise rpc_errors.RpcError(
                rpc_errors.AnimicaCode.STALE_TEMPLATE,
                "stale template",
                {
                    "reason": "stale_template",
                    "templateId": template_id,
                    "detail": "template_not_found",
                },
            )
        now = time.time()
        if float(cached.get("expires_at") or 0) and now > float(cached.get("expires_at") or 0):
            _metric_inc("stale_rejects")
            raise rpc_errors.RpcError(
                rpc_errors.AnimicaCode.STALE_TEMPLATE,
                "stale template",
                {
                    "reason": "stale_template",
                    "templateId": template_id,
                    "detail": "template_expired",
                    "issued_at": cached.get("issued_at"),
                    "expires_at": cached.get("expires_at"),
                    "submitted_at": now,
                },
            )
        cached_parent = cached.get("parent_hash")
        if cached_parent and parent_hash_hex and cached_parent != parent_hash_hex:
            _metric_inc("stale_rejects")
            raise rpc_errors.RpcError(
                rpc_errors.AnimicaCode.STALE_TEMPLATE,
                "stale template",
                {
                    "reason": "stale_template",
                    "templateId": template_id,
                    "expected_parent": cached_parent,
                    "got_parent": parent_hash_hex,
                },
            )

    head_snapshot = _current_head_snapshot()
    head_hash = head_snapshot.get("hash")
    submitted_height = None
    if isinstance(block.get("header"), dict):
        submitted_height = block["header"].get("height") or block["header"].get("number")
    is_stale = bool(parent_hash_hex and head_hash and parent_hash_hex != head_hash)
    if is_stale:
        parent_matches_head = bool(parent_hash_hex and head_hash and parent_hash_hex == head_hash)
        height_ok = True
        try:
            if submitted_height is not None:
                height_ok = int(submitted_height) == int(head_snapshot.get("height") or 0) + 1
        except Exception:
            height_ok = True
        if not (parent_matches_head and height_ok):
            _metric_inc("stale_rejects")
            raise rpc_errors.RpcError(
                rpc_errors.AnimicaCode.STALE_TEMPLATE,
                "stale template",
                {
                    "reason": "stale_template",
                    "templateId": template_id,
                    "expected_head": head_hash,
                    "got_parent": parent_hash_hex,
                    "head_height": head_snapshot.get("height"),
                    "submitted_height": submitted_height,
                    "head_hash_at_issue": (cached or {}).get("head_hash_at_issue") if isinstance(cached, dict) else None,
                    "head_hash_at_submit": head_hash,
                    "issued_at": (cached or {}).get("issued_at") if isinstance(cached, dict) else None,
                    "expires_at": (cached or {}).get("expires_at") if isinstance(cached, dict) else None,
                    "submitted_at": time.time(),
                },
            )

    try:
        ctx = _ctx()
        from core.chain import block_import as block_import_mod

        params = block_import_mod._load_chain_params_for_import(  # type: ignore[attr-defined]
            getattr(ctx.cfg, "genesis_path", None)
        )
        importer = block_import_mod._get_importer(  # type: ignore[attr-defined]
            ctx.block_db, ctx.state_db, ctx.tx_index, params
        )
        result = importer.import_block(block)

        accepted = result.code in (
            block_import_mod.ImportErrorCode.ACCEPTED,
            block_import_mod.ImportErrorCode.DUPLICATE,
        )
        if not accepted:
            _metric_inc("imports_failed")
            reason = result.reason or result.code
            reason_lower = str(reason).lower()
            reject_reason = "invalid_state_transition"
            code = rpc_errors.AnimicaCode.INVALID_STATE_TRANSITION
            if "pow" in reason_lower:
                reject_reason = "invalid_pow"
                code = rpc_errors.AnimicaCode.INVALID_POW
            elif "timestamp" in reason_lower:
                reject_reason = "invalid_timestamp"
                code = rpc_errors.AnimicaCode.INVALID_TIMESTAMP
            elif "parent" in reason_lower or "height continuity" in reason_lower:
                reject_reason = "invalid_parent"
                code = rpc_errors.AnimicaCode.INVALID_PARENT
            elif "coinbase" in reason_lower:
                reject_reason = "invalid_coinbase"
                code = rpc_errors.AnimicaCode.INVALID_COINBASE
            elif "merkle" in reason_lower or "txsroot" in reason_lower:
                reject_reason = "invalid_merkle_root"
                code = rpc_errors.AnimicaCode.INVALID_MERKLE_ROOT

            raise rpc_errors.RpcError(
                code,
                "block rejected",
                {
                    "reason": reject_reason,
                    "detail": reason,
                    "height": result.height,
                    "block_hash": result.block_hash.hex() if result.block_hash else None,
                },
            )

        payout_address = None
        if template_id:
            cached = _TEMPLATE_CACHE.get(str(template_id)) or {}
            payout_address = cached.get("payout_address")
        if result.code == block_import_mod.ImportErrorCode.ACCEPTED:
            _metric_inc("imports_ok")
            _mark_head_progress()
            try:
                block_obj, _ = block_import_mod.decode_block(block)
            except Exception as e:
                log.warning("Failed to decode accepted block for state execution", extra={"err": str(e)})
            else:
                log.info(
                    "Skipping post-import tx execution; state already applied by BlockImporter",
                    extra={
                        "height": block_obj.header.height,
                        "block_hash": result.block_hash.hex() if result.block_hash else None,
                    },
                )

                # Block rewards are now applied during block import in BlockImporter._apply_block_state
                # No need to apply them again here (would cause double-crediting)

                try:
                    tx_hashes = []
                    if raw_txs_hex:
                        from mempool.tx_hash import tx_hash_hex as _tx_hash_hex

                        for raw_hex in raw_txs_hex:
                            raw_bytes = bytes.fromhex(raw_hex[2:])
                            tx_hashes.append(_tx_hash_hex(raw_bytes))
                    else:
                        tx_hashes = [_canonical_txid_hex(tx) for tx in block_obj.txs]
                    mempool_service = getattr(ctx, "mempool", None)
                    if mempool_service is not None and tx_hashes:
                        removed = mempool_service.remove_included(tx_hashes)
                        mempool_service.revalidate()
                        log.info(
                            "Evicted included txs from mempool after submitBlock",
                            extra={"removed": removed},
                        )
                    from mempool import on_block_accepted

                    reconcile_result = on_block_accepted(
                        block_obj, getattr(ctx, "state_db", None), tx_hashes=tx_hashes
                    )
                    if reconcile_result:
                        log.info(
                            "Reconciled mempool after submitBlock acceptance",
                            extra=reconcile_result,
                        )
                except Exception as e:
                    log.warning(
                        "Failed to reconcile mempool after submitBlock",
                        extra={"err": str(e)},
                    )

        # Calculate credited reward amount from canonical state delta
        credited_amount = 0
        block_hash_hex = None
        balance_now = None
        if result.code == block_import_mod.ImportErrorCode.ACCEPTED:
            # Notify all miners that a block was found so they can move to next block
            try:
                from mining.orchestrator import notify_all_template_feeders_block_found
                notify_all_template_feeders_block_found()
                log.debug("Notified template feeders about block acceptance")
            except Exception as e:
                log.debug("Failed to notify template feeders: %s", e, exc_info=True)

            # Get block hash
            if result.block_hash:
                block_hash_hex = "0x" + result.block_hash.hex()

            # Read canonical balance after commit; this is the source of truth
            if payout_address:
                try:
                    from rpc.state_service import parse_address

                    key = parse_address(payout_address)
                    balance_now = int(ctx.state_db.get_balance(key))
                except Exception as e:
                    log.warning("Failed to query payout balance after accepted block: %s", e, exc_info=True)

            # For single-block submitBlock, credited amount equals expected protocol reward.
            # Miner CLI validates the observable delta via state.getBalance RPC.
            try:
                from consensus.rewards import compute_block_reward

                chain_id = getattr(ctx.cfg, "chain_id", 1)
                params = getattr(ctx, "params", None) or {}
                rewards = compute_block_reward(
                    chain_id=chain_id,
                    height=int(result.height or 0),
                    params=params,
                )
                if rewards:
                    credited_amount = int(rewards[0][1])
            except Exception as e:
                log.warning("Failed to calculate expected reward: %s", e)

            # AICF Credit Minting: Mint credits from block reward + fees
            # This happens AFTER block is committed and rewards are applied to state
            try:
                from aicf.protocol.state import ProtocolState
                from aicf.credits.minting import mint_block_credits, get_aicf_slice_bps
                
                # Get AICF state DB path (default to data directory)
                data_dir = os.getenv("ANIMICA_DATA_DIR", os.path.expanduser("~/.animica/data"))
                aicf_db_path = os.path.join(data_dir, "aicf_protocol.db")
                
                # Initialize protocol state
                protocol_state = ProtocolState(aicf_db_path)
                
                # Get miner address for credit allocation
                miner_addr_bytes = payout_address if payout_address else _get_miner_address()
                miner_addr_hex = "0x" + miner_addr_bytes.hex()
                
                # Get AICF slice configuration
                aicf_slice_bps = get_aicf_slice_bps(params)
                
                # Extract fees from block transactions
                fees_collected = 0
                if block_obj and block_obj.receipts:
                    # Use actual gas used from receipts for accurate fee calculation
                    for tx, receipt in zip(block_obj.txs, block_obj.receipts):
                        if tx.unsigned.kind != 3:  # Skip coinbase transactions (TxKind.COINBASE = 3)
                            fees_collected += tx.unsigned.gas_price * receipt.gas_used
                elif block_obj:
                    # Fallback: use gas_limit if receipts unavailable (max potential fees)
                    for tx in block_obj.txs:
                        if tx.unsigned.kind != 3:  # Skip coinbase transactions
                            fees_collected += tx.unsigned.gas_price * tx.unsigned.gas_limit
                
                # Mint credits (base_reward + fees_collected)
                mint_result = mint_block_credits(
                    protocol_state,
                    block_height=int(result.height or 0),
                    block_hash=block_hash_hex or "0x" + result.block_hash.hex() if result.block_hash else "0x00",
                    miner_address=miner_addr_hex,
                    base_reward=credited_amount,
                    fees_collected=fees_collected,
                    aicf_slice_bps=aicf_slice_bps,
                )
                
                log.info(
                    f"AICF credits minted for block {result.height}: "
                    f"aicf_credits={mint_result['aicf_credits']}, "
                    f"ledger_id={mint_result['ledger_id']}"
                )
            except Exception as e:
                # Don't fail block submission if credit minting has issues
                # Credit minting is tracked separately and can be reconciled later
                log.warning(
                    f"Failed to mint AICF credits for block {result.height}: {e}",
                    exc_info=True
                )

        return {
            "accepted": True,
            "duplicate": result.code == block_import_mod.ImportErrorCode.DUPLICATE,
            "credited_amount": int(credited_amount),
            "balance_now": int(balance_now) if balance_now is not None else None,
            "new_head": int(result.height or 0),
            "block_hash": block_hash_hex,
        }
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        raise rpc_errors.RpcError(
            rpc_errors.AnimicaCode.SERVER_ERROR,
            "block submission failed",
            {"reason": "submit_failed", "detail": str(e)},
        )


@method("miner.get_sha256_job", desc="Return a Bitcoin-style Stratum v1 job template")
def miner_get_sha256_job(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Provide a lightweight SHA-256 template for ASIC-oriented Stratum clients."""

    params = params or {}
    address = params.get("address") or params.get("poolAddress") or ""
    parent_hash, height, _mix_seed, chain_id, _state_root = _head_info()

    prevhash = parent_hash[::-1].hex()  # Stratum v1 expects little-endian hex
    coinb1 = (
        "01000000"  # version
        + f"{height:08x}"  # fake height marker
        + f"{chain_id:08x}"  # chain id marker
    )
    coinb2 = (address or "").replace("0x", "") + "00"
    merkle_branch: list[str] = []

    bits = _DEFAULT_SHA256_BITS
    nbits = bits if isinstance(bits, str) else str(bits)
    ntime = f"{int(time.time()):08x}"
    version = "20000000"

    block_target = _bits_to_target(nbits)
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(
                share_microtarget(_resolve_theta(), shares_per_block=1)
            ) / float(_resolve_theta() or 1)
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    return {
        "jobId": uuid.uuid4().hex,
        "prevhash": prevhash,
        "coinb1": coinb1,
        "coinb2": coinb2,
        "merkle_branch": merkle_branch,
        "version": version,
        "nbits": nbits,
        "ntime": ntime,
        "clean_jobs": True,
        "target": hex(block_target),
        "difficulty": share_target,
        "height": height,
    }


@method(
    "miner.submit_sha256_block", desc="Accept a candidate SHA-256 block from the pool"
)
def miner_submit_sha256_block(**payload: Any) -> Dict[str, Any]:
    # Stub for integration with the Animica orchestrator. For now we simply echo success.
    block = (
        payload.get("payload")
        if len(payload) == 1 and "payload" in payload
        else payload
    )
    return {"accepted": True, "payload": block}


@method("mining.getTemplateStatus", desc="Get mining template readiness status")
def mining_get_template_status() -> dict[str, Any]:
    """
    Get the current template readiness status.
    
    Returns:
        dict: {
            "can_mine": bool,  # Whether mining is allowed
            "reason": str | None,  # Reason if mining is blocked
            "sync_phase": str | None,  # Current sync phase
            "head": {
                "height": int,
                "hash": str,
                "has_state_root": bool,
            },
            "mempool": {
                "size": int,
            },
        }
    """
    try:
        # Check mining gate
        allowed, reason = _mining_gate(allow_offline_mining=False, allow_unsynced=False)
        
        # Get head info
        head_snap = _current_head_snapshot()
        head_height = head_snap.get("height", 0)
        head_hash = head_snap.get("hash")
        head_header = head_snap.get("header")
        
        # Check if we have a state root
        has_state_root = False
        if head_header is not None:
            state_root = getattr(head_header, "stateRoot", None)
            if state_root and state_root != (b"\x00" * 32):
                has_state_root = True
        
        # Get sync phase
        sync_phase = None
        try:
            import p2p
            svc = p2p.get_service()
            if svc is not None:
                sync_status = svc.sync_status_snapshot().to_dict()
                sync_phase = sync_status.get("phase")
        except Exception:
            pass
        
        # Get mempool size
        mempool_size = 0
        try:
            ctx = _ctx()
            mempool_service = _resolve_mempool_service(ctx)
            if mempool_service is not None:
                if hasattr(mempool_service, "size"):
                    mempool_size = mempool_service.size()
                elif hasattr(mempool_service, "__len__"):
                    mempool_size = len(mempool_service)
        except Exception:
            pass
        
        return {
            "can_mine": allowed,
            "reason": reason,
            "sync_phase": sync_phase,
            "head": {
                "height": int(head_height) if head_height is not None else 0,
                "hash": head_hash,
                "has_state_root": has_state_root,
            },
            "mempool": {
                "size": mempool_size,
            },
        }
    except Exception as e:
        log.error(f"Failed to get template status: {e}", exc_info=True)
        return {
            "can_mine": False,
            "reason": f"error: {e}",
            "sync_phase": None,
            "head": {"height": 0, "hash": None, "has_state_root": False},
            "mempool": {"size": 0},
        }


@method("miner.status", aliases=("miner_status", "miner.getStatus", "miner_getStatus"), desc="Get miner status")
def miner_status() -> dict[str, Any]:
    """
    Get the current miner status in a stable schema: {enabled, ok, reason, message, details}.

    Internally delegates to mining.getTemplateStatus and maps the result.
    """
    try:
        template_status = mining_get_template_status()
        can_mine = bool(template_status.get("can_mine", False))
        reason = template_status.get("reason") or None
        return {
            "enabled": True,
            "ok": can_mine,
            "reason": None if can_mine else (reason or "not_ready"),
            "message": None if can_mine else reason,
            "details": {
                "sync_phase": template_status.get("sync_phase"),
                "head": template_status.get("head", {}),
                "mempool": template_status.get("mempool", {}),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {
            "enabled": True,
            "ok": False,
            "reason": "internal",
            "message": str(e),
            "details": {},
        }


@method("mining.getCredits", desc="Get mining credits audit trail")
def mining_get_credits(
    address: str | None = None,
    from_height: int | None = None,
    to_height: int | None = None,
    last: int | None = None,
) -> dict[str, Any]:
    """
    Get mining credits from the audit trail.
    
    This method returns records of blocks mined locally, including expected vs credited rewards.
    Useful for debugging and verifying that mining rewards are being credited correctly.
    
    Args:
        address: Filter by miner address (hex string with 0x prefix)
        from_height: Filter by minimum block height (inclusive)
        to_height: Filter by maximum block height (inclusive)
        last: Return only the last N records (after other filters)
    
    Returns:
        dict: {
            "credits": list of {
                "height": int,
                "hash": str,
                "parent_hash": str,
                "miner_address": str,
                "expected_reward": int,  # in nANM
                "credited_reward": int,  # in nANM (total balance after block)
                "state_root": str,
                "timestamp": int,  # unix timestamp
            },
            "count": int,
            "filters": dict with applied filters
        }
    """
    try:
        global _MINING_AUDIT_TRAIL
        
        # Apply filters
        filtered = _MINING_AUDIT_TRAIL
        
        if address is not None:
            # Normalize address (add 0x prefix if missing)
            addr_normalized = address if address.startswith("0x") else f"0x{address}"
            filtered = [r for r in filtered if r["miner_address"].lower() == addr_normalized.lower()]
        
        if from_height is not None:
            filtered = [r for r in filtered if r["height"] >= from_height]
        
        if to_height is not None:
            filtered = [r for r in filtered if r["height"] <= to_height]
        
        # Sort by height descending (most recent first)
        filtered = sorted(filtered, key=lambda r: r["height"], reverse=True)
        
        if last is not None and last > 0:
            filtered = filtered[:last]
        
        return {
            "credits": filtered,
            "count": len(filtered),
            "filters": {
                "address": address,
                "from_height": from_height,
                "to_height": to_height,
                "last": last,
            },
        }
    except Exception as e:
        log.error(f"Failed to get mining credits: {e}", exc_info=True)
        return {
            "credits": [],
            "count": 0,
            "error": str(e),
        }
