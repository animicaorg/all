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
from core.utils.time import maybe_normalize_unix_timestamp_seconds
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


def _build_useful_work_payload() -> Dict[str, Any]:
    """
    Build a useful-work payload for block templates when include_aicf=true.

    Queries the local ENA artifact registry for verified artifacts whose
    manifest blobs are locally available in DA, then returns references
    for inclusion in the block header.
    """
    import hashlib

    manifest_blob_ids: list[str] = []
    credit_event_ids: list[str] = []

    # Pull verified artifacts from ENA pending registry
    try:
        from rpc.methods.ena import _PENDING_ARTIFACTS  # type: ignore

        for rec in _PENDING_ARTIFACTS.values():
            if rec.get("status") != "verified":
                continue
            mid = rec.get("manifest_blob_id", "")
            if not mid:
                continue
            # DA preflight: ensure blob is locally available
            try:
                from da.node_store import get_store as _da_get_store  # type: ignore
                import os as _os

                _base = _os.getenv("ANIMICA_DATA_DIR") or _os.path.expanduser("~/.animica")
                _chain_id = _os.getenv("ANIMICA_CHAIN_ID", "1")
                _da_dir = _os.path.join(_base, f"chain-{_chain_id}", "da")
                store = _da_get_store(_da_dir)
                if store is not None and store.has(mid):
                    manifest_blob_ids.append(mid)
                    cev = rec.get("credit_event_id")
                    if cev:
                        credit_event_ids.append(cev)
            except Exception:
                manifest_blob_ids.append(mid)
    except Exception:
        pass

    # Compute credit event Merkle root (sha3-256 of sorted joined IDs)
    credit_event_root: Optional[str] = None
    if credit_event_ids:
        joined = "|".join(sorted(credit_event_ids)).encode()
        credit_event_root = hashlib.sha3_256(joined).hexdigest()

    return {
        "manifestBlobIds": manifest_blob_ids,
        "creditEventIds": credit_event_ids,
        "creditEventRoot": credit_event_root,
        "count": len(manifest_blob_ids),
    }



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
DEFAULT_BLOCK_TX_LIMIT = int(os.getenv("ANIMICA_MAX_TXS_PER_BLOCK", "150000"))

# Receipt index prefix (matches PFX_RXI from core/db/block_db.py)
# Used for re-indexing receipts with canonical tx hashes
PFX_RXI = b"\x22"

try:
    from core.utils.deploy_metadata import (
        build_python_vm_package_deploy_metadata,
        is_python_vm_package_deploy_tx,
    )
except Exception:  # pragma: no cover
    build_python_vm_package_deploy_metadata = None  # type: ignore[assignment]
    is_python_vm_package_deploy_tx = None  # type: ignore[assignment]

# Default gas limit for transactions when not specified (same as INTRINSIC_GAS_TRANSFER)
DEFAULT_TX_GAS_LIMIT = INTRINSIC_GAS_TRANSFER  # 21,000 gas for simple transfers

# In-memory job cache for miner.getWork / miner.submitWork flows
_JOB_CACHE: dict[str, dict[str, Any]] = {}
_LOCAL_HEAD: dict[str, Any] = {}
_HEAD_STATE: dict[str, Any] = {
    "height": None,
    "hash": None,
    "theta": None,
    "generation": 0,
}
_AUTO_MINE: bool = False
_AUTO_TASK: asyncio.Task | None = None

# Theta tracking state for mining/template operations.
# Canonical theta is sourced from BlockImporter via _resolve_theta(); this state
# only keeps lightweight telemetry fields.
_MINING_STATE: dict[str, Any] = {
    "last_block_time": None,  # timestamp of last mined block
    "block_times": [],         # recent observed block intervals (seconds)
    "theta_state": None,       # legacy field retained for compatibility
    "adjustment_enabled": True, # legacy toggle retained for compatibility
    "last_network_height": None,  # last observed chain head height
    "last_network_timestamp": None,  # last observed chain head timestamp
    "stale_head_hash": None,  # head hash used for stale-head bucket tracking
    "stale_head_bucket": 0,  # last processed stale-age bucket for unchanged head
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
            # Keep legacy behavior for non-payout call sites.
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
            decoded = _decode_bech32_address(value)
        except Exception as exc:
            raise rpc_errors.InvalidParams(
                "address must be a valid anim bech32 address"
            ) from exc
        if decoded == ZERO32:
            raise rpc_errors.InvalidParams("address must not be the zero address")
        return value

    hex_str = value[2:] if value.startswith("0x") else value
    if len(hex_str) != 64:
        raise rpc_errors.InvalidParams(
            "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        )
    try:
        decoded = bytes.fromhex(hex_str)
    except Exception as exc:
        raise rpc_errors.InvalidParams(
            "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        ) from exc
    if decoded == ZERO32:
        raise rpc_errors.InvalidParams("address must not be the zero address")
    return "0x" + hex_str


def _require_payout_address_bytes(addr: Any, *, field_name: str = "address") -> bytes:
    """Strictly parse payout address and reject missing/invalid/zero values."""
    if not isinstance(addr, str) or not addr.strip():
        raise rpc_errors.InvalidParams(f"{field_name}: Select payout address")
    normalized = _validate_payout_address(addr.strip())
    decoded = _as_bytes32_addr(normalized)
    if decoded == ZERO32:
        raise rpc_errors.InvalidParams(f"{field_name} must not be the zero address")
    return decoded


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
    def _theta_from_header(header: Any) -> int | None:
        if header is None:
            return None
        theta = getattr(header, "thetaMicro", getattr(header, "theta_micro", None))
        if isinstance(header, dict):
            theta = header.get("thetaMicro", header.get("theta_micro", theta))
        if theta is None:
            return None
        try:
            return int(theta)
        except Exception:
            return None

    # Canonical importer state is the authoritative source for the next
    # canonical theta used by mining templates/jobs.
    try:
        ctx = _ctx()
        block_db = getattr(ctx, "block_db", None)
        if block_db is not None:
            from core.chain import block_import as block_import_mod

            params = block_import_mod._load_chain_params_for_import(  # type: ignore[attr-defined]
                getattr(getattr(ctx, "cfg", None), "genesis_path", None)
            )
            importer = block_import_mod._get_importer(  # type: ignore[attr-defined]
                block_db,
                getattr(ctx, "state_db", None),
                getattr(ctx, "tx_index", None),
                params,
            )
            theta = int(importer.get_current_difficulty())
            if theta > 0:
                return theta
    except Exception:
        pass

    # Canonical chain head is the source of truth for difficulty/theta.
    try:
        snap = _current_head_snapshot()
    except Exception:
        snap = {}
    theta = _theta_from_header(snap.get("header"))
    if theta and theta > 0:
        return int(theta)

    try:
        ctx = _ctx()
        block_db = getattr(ctx, "block_db", None)
    except Exception:
        block_db = None

    if block_db is not None:
        header = None
        head_hash = snap.get("hash") if isinstance(snap, dict) else None
        if isinstance(head_hash, str) and head_hash:
            try:
                head_hash_bytes = bytes.fromhex(
                    head_hash[2:] if head_hash.startswith("0x") else head_hash
                )
                getter = getattr(block_db, "get_header_by_hash", None)
                if callable(getter):
                    header = getter(head_hash_bytes)
            except Exception:
                header = None
        if header is None:
            try:
                head_height = int((snap or {}).get("height") or 0)
                getter = getattr(block_db, "get_header_by_height", None)
                if callable(getter):
                    header = getter(head_height)
            except Exception:
                header = None
        theta = _theta_from_header(header)
        if theta and theta > 0:
            return int(theta)

    return _DEFAULT_THETA_MICRO


def _ctx():
    try:
        return deps.get_ctx()
    except Exception:
        # In tests the FastAPI lifecycle may not have run yet; fall back to a
        # one-off context.
        return deps.build_context()


def _target_block_time_s(default: float = 60.0) -> float:
    def _coerce_seconds(value: Any, *, ms: bool = False) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except Exception:
            return None
        if not math.isfinite(numeric) or numeric <= 0:
            return None
        if ms:
            numeric = numeric / 1000.0
        return numeric

    try:
        ctx = _ctx()
        params = getattr(ctx, "params", {}) or {}
        if isinstance(params, dict):
            monetary = params.get("monetary") or {}
            issuance = monetary.get("issuance") or {}
            defaults = params.get("defaults") or {}
            defaults_issuance = defaults.get("issuance") if isinstance(defaults, dict) else {}
            for src in (issuance, defaults_issuance or {}):
                target_ms = _coerce_seconds(src.get("target_block_interval_ms"), ms=True)
                if target_ms is not None:
                    return float(target_ms)
            block = params.get("block") or params.get("blocks") or {}
            target_seconds = _coerce_seconds(block.get("target_seconds"))
            if target_seconds is not None:
                return float(target_seconds)
        else:
            block_obj = getattr(params, "block", None)
            target_seconds = _coerce_seconds(getattr(block_obj, "target_seconds", None))
            if target_seconds is not None:
                return float(target_seconds)
    except Exception:
        pass
    fallback = _coerce_seconds(os.getenv("ANIMICA_TARGET_BLOCK_TIME_S"), ms=False)
    return float(fallback if fallback is not None else default)


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


def _header_timestamp_seconds(header: Any) -> int:
    if header is None:
        return 0
    if isinstance(header, dict):
        for key in ("timestamp", "time", "ts"):
            value = header.get(key)
            if value is not None:
                normalized = maybe_normalize_unix_timestamp_seconds(value)
                if normalized is not None:
                    return normalized
        return 0
    for attr in ("timestamp", "time", "ts"):
        value = getattr(header, attr, None)
        if value is not None:
            normalized = maybe_normalize_unix_timestamp_seconds(value)
            if normalized is not None:
                return normalized
    return 0


def _head_timestamp_seconds() -> Optional[int]:
    snap = _current_head_snapshot()
    header = snap.get("header") if isinstance(snap, dict) else None
    ts = _header_timestamp_seconds(header)
    if ts > 0:
        return ts
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
                normalized = maybe_normalize_unix_timestamp_seconds(ts)
                if normalized is not None:
                    return normalized
    except Exception:
        return None
    return None


def _adjust_theta_for_mining(dt_seconds: float | None = None, *, blocks_skipped: int = 1) -> int:
    """
    Return canonical theta for mining/template headers.

    Mining must not run a private theta retarget loop because block import
    validates header theta against the canonical BlockImporter state once the
    warmup window is passed (mainnet window is 720). Divergent local retarget
    parameters can therefore hard-stop progress exactly at that boundary.

    We still keep lightweight interval telemetry in `_MINING_STATE["block_times"]`
    for observability, but consensus theta is always sourced from `_resolve_theta()`.
    """
    # Keep bounded local interval telemetry for diagnostics.
    if dt_seconds is not None:
        try:
            dt = float(dt_seconds)
        except Exception:
            dt = None
        if dt is not None and math.isfinite(dt) and dt > 0:
            block_times = _MINING_STATE.get("block_times")
            if not hasattr(block_times, "append"):
                from collections import deque

                block_times = deque(maxlen=20)
                _MINING_STATE["block_times"] = block_times
            block_times.append(dt)

    # Keep legacy fields stable for callers/tests that introspect them.
    _MINING_STATE["theta_state"] = None
    _MINING_STATE["adjustment_enabled"] = True

    theta = int(_resolve_theta())
    if theta <= 0:
        return int(_DEFAULT_THETA_MICRO)
    return theta


def _network_block_interval(head_height: int, head_timestamp: int) -> tuple[float, int] | None:
    if head_height <= 0 or head_timestamp <= 0:
        _MINING_STATE["last_network_height"] = head_height
        _MINING_STATE["last_network_timestamp"] = head_timestamp
        _MINING_STATE["stale_head_hash"] = None
        _MINING_STATE["stale_head_bucket"] = 0
        return None

    last_height = _MINING_STATE.get("last_network_height")
    last_timestamp = _MINING_STATE.get("last_network_timestamp")
    if last_height is None or last_timestamp is None:
        _MINING_STATE["last_network_height"] = head_height
        _MINING_STATE["last_network_timestamp"] = head_timestamp
        _MINING_STATE["stale_head_hash"] = None
        _MINING_STATE["stale_head_bucket"] = 0
        return None
    height_delta = int(head_height) - int(last_height)
    if height_delta <= 0:
        return None

    dt_seconds = int(head_timestamp) - int(last_timestamp)
    _MINING_STATE["last_network_height"] = head_height
    _MINING_STATE["last_network_timestamp"] = head_timestamp
    _MINING_STATE["stale_head_hash"] = None
    _MINING_STATE["stale_head_bucket"] = 0
    if dt_seconds <= 0:
        return None
    # update_theta() expects mean per-step dt when blocks_skipped > 1.
    steps = max(1, int(height_delta))
    dt_per_step = float(dt_seconds) / float(steps)
    return float(dt_per_step), steps


def _stale_head_interval(head_hash: str | None, head_timestamp: int) -> tuple[float, int] | None:
    """
    Return a synthetic dt and bucket count when the chain head is unchanged.

    To avoid call-frequency bias, we retarget at most once per target-time bucket
    for a given head hash (e.g., once per ~60s bucket when target is 60s). If
    multiple buckets elapsed between calls, we return the bucket delta so the
    caller can catch up with multiple retarget steps.
    """
    if head_timestamp <= 0:
        _MINING_STATE["stale_head_hash"] = None
        _MINING_STATE["stale_head_bucket"] = 0
        return None

    head_key = str(head_hash or f"ts:{int(head_timestamp)}")
    last_key = _MINING_STATE.get("stale_head_hash")
    if last_key != head_key:
        _MINING_STATE["stale_head_hash"] = head_key
        _MINING_STATE["stale_head_bucket"] = 0

    age_s = int(time.time()) - int(head_timestamp)
    if age_s <= 0:
        return None

    target_s = max(1.0, float(_target_block_time_s()))
    current_bucket = int(age_s // target_s)
    last_bucket = int(_MINING_STATE.get("stale_head_bucket") or 0)
    if current_bucket <= last_bucket:
        return None

    buckets_skipped = max(1, current_bucket - last_bucket)
    _MINING_STATE["stale_head_bucket"] = current_bucket
    return float(min(age_s, 3600)), buckets_skipped


def _bool_env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _current_chain_id_for_mining_policy() -> int | None:
    try:
        ctx = _ctx()
        cfg = getattr(ctx, "cfg", None)
        chain_id = getattr(cfg, "chain_id", None)
        if chain_id is not None:
            return int(chain_id)
    except Exception:
        pass

    raw = os.getenv("ANIMICA_CHAIN_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _resolve_allow_offline_mining(allow_offline_mining: bool, *, source: str) -> bool:
    """
    Resolve whether offline mining/template issuance is allowed for this request.

    Policy:
    - Disabled by default.
    - Enabled when explicitly requested by RPC params (`allow_offline_mining=true`)
      or via env override (`ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS=1`).
    - Always denied on mainnet chain_id=1 to preserve production safety.
    """
    env_override = _bool_env_enabled("ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS")
    requested = bool(allow_offline_mining)
    if not requested and not env_override:
        return False

    chain_id = _current_chain_id_for_mining_policy()
    if chain_id == 1:
        log.warning(
            "Offline mining/template override denied on mainnet.",
            extra={
                "source": source,
                "chain_id": chain_id,
                "requested": requested,
                "env_override": env_override,
            },
        )
        return False

    return True


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
        allow_offline_mining: Effective offline override (resolved by caller policy).
        allow_unsynced: Effective unsynced override.
        
    Returns:
        (allowed: bool, reason: str | None)
        - (True, None) if mining is allowed
        - (False, reason) if mining is blocked
    """
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

    # Network-tip lag: best_header_height can read "at tip" while the node is
    # actually wedged N blocks short of the real network tip (the near-tip sync
    # wedge), so ALSO gate on network_best_height (the peers' height). Without
    # this, a behind seed hands out stale templates that orphan when it catches
    # up — wasting miners' hashpower mining against an unsynced node. A small
    # tolerance absorbs propagation jitter / being the block producer; the
    # allow_unsynced override still works for intentional offline/test mining.
    network_best = int(sync_status.get("network_best_height") or 0)
    net_lag = network_best - exec_head
    net_lag_limit = int(os.getenv("ANIMICA_MINING_MAX_NETWORK_LAG", "16"))
    if network_best > 0 and net_lag >= net_lag_limit and not allow_unsynced:
        log.info(
            "MINER_BEHIND_NETWORK",
            extra={
                "exec_head": exec_head,
                "network_best": network_best,
                "net_lag": net_lag,
            },
        )
        return False, f"behind_network:{net_lag}_blocks"

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
    def _hash_hex(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return "0x" + bytes(value).hex()
        if isinstance(value, str):
            return value
        return None

    with _HEAD_RW_LOCK:
        try:
            ctx = _ctx()
            snap = ctx.get_head()
            if not isinstance(snap, dict):
                snap = {}
        except Exception:
            snap = {}

        header = snap.get("header")
        height = int(snap.get("height") or snap.get("number") or 0)
        hash_hex = _hash_hex(snap.get("hash") or snap.get("blockHash"))

        if hash_hex is None and header is not None:
            header_hash = getattr(header, "hash", None)
            try:
                if callable(header_hash):
                    raw = header_hash()
                    if isinstance(raw, (bytes, bytearray)):
                        hash_hex = "0x" + bytes(raw).hex()
                elif isinstance(header_hash, (bytes, bytearray)):
                    hash_hex = "0x" + bytes(header_hash).hex()
                elif isinstance(header_hash, str) and header_hash:
                    hash_hex = header_hash
            except Exception:
                pass

        prev_height_raw = _HEAD_STATE.get("height")
        prev_height = int(prev_height_raw) if prev_height_raw is not None else None
        prev_hash = _hash_hex(_HEAD_STATE.get("hash"))
        theta_val = None
        if isinstance(header, dict):
            theta_val = header.get("thetaMicro", header.get("theta_micro"))
        else:
            theta_val = getattr(header, "thetaMicro", getattr(header, "theta_micro", None))
        try:
            theta_val = int(theta_val) if theta_val is not None else None
        except Exception:
            theta_val = None
        prev_theta = _HEAD_STATE.get("theta")
        canonical_changed = (height, hash_hex, theta_val) != (prev_height, prev_hash, prev_theta)

        if canonical_changed:
            if prev_height is not None and height > prev_height:
                _metric_inc("head_advances")
            _mark_head_progress()

            _HEAD_STATE["hash"] = hash_hex
            _HEAD_STATE["height"] = height
            _HEAD_STATE["theta"] = theta_val
            _HEAD_STATE["generation"] = int(_HEAD_STATE.get("generation", 0)) + 1

            _LOCAL_HEAD.clear()
            _JOB_CACHE.clear()
            with _TEMPLATE_CACHE_LOCK:
                _TEMPLATE_CACHE.clear()
            _prune_template_cache()

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
    2. None (missing address is now a hard error)
    
    Returns:
        bytes: 32-byte miner address
    """
    # Try environment variable first
    env_addr = os.getenv("ANIMICA_MINER_ADDRESS", "").strip()
    if env_addr:
        try:
            # Try to decode bech32 address to raw bytes
            decoded = _decode_bech32_address(env_addr)
            if decoded == ZERO32:
                raise ValueError("zero address is not allowed")
            return decoded
        except Exception as e:
            log.debug(f"Failed to decode ANIMICA_MINER_ADDRESS as bech32: {e}")
            # If bech32 decode fails, try hex
            try:
                if env_addr.startswith("0x"):
                    env_addr = env_addr[2:]
                addr_bytes = bytes.fromhex(env_addr)[:32].ljust(32, b"\x00")
                if addr_bytes == ZERO32:
                    raise ValueError("zero address is not allowed")
                return addr_bytes
            except Exception as hex_err:
                log.warning(f"Failed to decode ANIMICA_MINER_ADDRESS as hex: {hex_err}")
    
    raise rpc_errors.InvalidParams("Select payout address")


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


def _parse_nonce_int(nonce: Any) -> int:
    if isinstance(nonce, int):
        value = nonce
    elif isinstance(nonce, (bytes, bytearray)):
        raw = bytes(nonce)
        if len(raw) > 8:
            raise ValueError("nonce must fit in 8 bytes")
        value = int.from_bytes(raw, "big", signed=False)
    elif isinstance(nonce, str):
        raw = _hex_to_bytes(nonce)
        if len(raw) > 8:
            raise ValueError("nonce must fit in 8 bytes")
        value = int.from_bytes(raw, "big", signed=False)
    else:
        raise ValueError("nonce must be hex string, int, or bytes")

    if value < 0:
        raise ValueError("nonce must be non-negative")
    if value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("nonce must fit in 64 bits")
    return value


def _resolve_submit_work_mix_seed(
    payload: dict[str, Any], cached_job: dict[str, Any]
) -> bytes:
    mix_seed = payload.get("mixSeed") or payload.get("mix_seed")
    if mix_seed is None:
        hints = payload.get("hints")
        if isinstance(hints, dict):
            mix_seed = hints.get("mixSeed") or hints.get("mix_seed")
    if mix_seed is None:
        mix_seed = cached_job.get("mix_seed")
    if mix_seed is None:
        job_obj = cached_job.get("job")
        try:
            mix_seed = job_obj.header.mix_seed  # type: ignore[union-attr]
        except Exception:
            mix_seed = None

    if isinstance(mix_seed, (bytes, bytearray)):
        return bytes(mix_seed)
    if isinstance(mix_seed, str):
        try:
            return _hex_to_bytes(mix_seed)
        except Exception:
            return b""
    return b""


def _extract_payload(payload: Any, kwargs: dict[str, Any]) -> Any:
    """Normalize positional/keyword JSON-RPC payload variants."""
    if payload is not None:
        if (
            isinstance(payload, (list, tuple))
            and len(payload) == 1
            and isinstance(payload[0], dict)
        ):
            return payload[0]
        return payload

    if not kwargs:
        return None

    if len(kwargs) == 1 and "payload" in kwargs:
        return kwargs.get("payload")

    return dict(kwargs)


def _encode_header_extra(*, coinbase: bytes | None = None, instant_block: bool = False) -> bytes:
    """Encode optional header `extra` metadata as CBOR."""
    extra: dict[str, Any] = {}
    if isinstance(coinbase, (bytes, bytearray)):
        cb = _bytes32(coinbase)
        if cb != ZERO32:
            extra["coinbase"] = cb
    if instant_block:
        extra["instant_block"] = True
    if not extra:
        return b""
    try:
        import cbor2

        return cbor2.dumps(extra)
    except Exception:
        return b""


def _decode_header_extra(extra: Any) -> dict[str, Any]:
    if extra is None:
        return {}
    raw: bytes | None = None
    if isinstance(extra, (bytes, bytearray)):
        raw = bytes(extra)
    elif isinstance(extra, str):
        try:
            raw = _hex_to_bytes(extra)
        except Exception:
            raw = None
    if not raw:
        return {}
    try:
        import cbor2

        decoded = cbor2.loads(raw)
        if isinstance(decoded, dict):
            return decoded
    except Exception:
        return {}
    return {}


def _extract_coinbase_from_header_payload(header_payload: Any) -> bytes | None:
    if not isinstance(header_payload, dict):
        return None
    for key in ("coinbase", "miner", "proposer"):
        if key in header_payload:
            candidate = _as_bytes32_addr(header_payload.get(key))
            if candidate != ZERO32:
                return candidate
    extra_map = _decode_header_extra(header_payload.get("extra"))
    if "coinbase" in extra_map:
        candidate = _as_bytes32_addr(extra_map.get("coinbase"))
        if candidate != ZERO32:
            return candidate
    return None


def _header_from_cached_job(job: dict[str, Any], *, nonce: int) -> Header:
    header_override = job.get("header_override")
    if isinstance(header_override, dict):
        try:
            from mining.template_block import header_from_template_view

            return header_from_template_view(header_override, nonce=int(nonce))
        except Exception:
            pass

    try:
        job_obj = job["job"]
        header_tpl = job_obj.header  # type: ignore[index]
    except Exception as exc:
        raise ValueError("cached mining job missing header template") from exc

    return Header(
        v=1,
        chainId=int(getattr(header_tpl, "chain_id")),
        height=int(getattr(header_tpl, "number")),
        parentHash=_bytes32(getattr(header_tpl, "parent_hash")),
        timestamp=int(getattr(header_tpl, "timestamp")),
        stateRoot=_bytes32(getattr(header_tpl, "state_root")),
        txsRoot=_bytes32(getattr(header_tpl, "txs_root")),
        receiptsRoot=_bytes32(getattr(header_tpl, "receipts_root")),
        proofsRoot=_bytes32(getattr(header_tpl, "proofs_root")),
        daRoot=_bytes32(getattr(header_tpl, "da_root")),
        mixSeed=_bytes32(getattr(header_tpl, "mix_seed")),
        poiesPolicyRoot=_bytes32(getattr(header_tpl, "poies_policy_root")),
        pqAlgPolicyRoot=_bytes32(getattr(header_tpl, "pq_alg_policy_root")),
        thetaMicro=int(getattr(header_tpl, "theta_target_micro")),
        workType=int(getattr(header_tpl, "work_type", 0) or 0),
        nonce=int(nonce),
        extra=bytes(getattr(header_tpl, "extra", b"") or b""),
    )


def _mempool_pending_count(mempool_service: Any) -> int:
    if mempool_service is None:
        return 0
    try:
        snapshot_fn = getattr(mempool_service, "snapshot", None)
        if callable(snapshot_fn):
            snapshot = snapshot_fn(limit=1)
            total = getattr(snapshot, "total", None)
            if total is not None:
                return int(total)
            entries = getattr(snapshot, "entries", None)
            if isinstance(entries, list):
                return len(entries)
    except Exception:
        pass
    try:
        stats_fn = getattr(mempool_service, "stats", None)
        if callable(stats_fn):
            stats = stats_fn() or {}
            if isinstance(stats, dict):
                for key in ("count", "pending_count", "pending", "total_txs", "total"):
                    value = stats.get(key)
                    if value is not None:
                        return int(value)
    except Exception:
        pass
    return 0


def _mempool_snapshot_limit(
    mempool_service: Any,
    *,
    default_limit: int = 1000,
) -> int:
    base_limit = max(1, int(default_limit))
    max_limit = max(1, int(DEFAULT_BLOCK_TX_LIMIT))
    pending = _mempool_pending_count(mempool_service)
    if pending <= 0:
        return min(base_limit, max_limit)
    return min(max_limit, max(base_limit, int(pending)))


def _extract_template_raw_txs(template_payload: dict[str, Any]) -> list[str]:
    raw_txs: list[str] = []
    txs = template_payload.get("txs")
    if not isinstance(txs, list):
        return raw_txs
    for entry in txs:
        if isinstance(entry, dict):
            raw = entry.get("raw")
            if isinstance(raw, str) and raw.startswith("0x"):
                raw_txs.append(raw)
            continue
        if isinstance(entry, str) and entry.startswith("0x"):
            raw_txs.append(entry)
    return raw_txs


def _serialize_header_for_submit(header: Header) -> dict[str, Any]:
    return {
        k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }


def _record_local_block(
    height: int, block_hash: str, header: dict[str, Any] | None = None
) -> None:
    # Debug-only local mirror. Never advance canonical mining state from here.
    with _HEAD_RW_LOCK:
        _LOCAL_HEAD.clear()
        _LOCAL_HEAD.update(
            {
                "height": int(height),
                "hash": block_hash,
                "header": header,
            }
        )
        _JOB_CACHE.clear()
        with _TEMPLATE_CACHE_LOCK:
            _TEMPLATE_CACHE.clear()
        _prune_template_cache()


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
                                    # Calculate reasonable limit based on max_gas (avoid division by zero),
                                    # but never exceed the configured per-block transaction cap.
                                    tx_limit = max(1000, int(max_gas // 21000)) if max_gas > 0 else 1000
                                    tx_limit = min(int(DEFAULT_BLOCK_TX_LIMIT), int(tx_limit))
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
        entries = list(getattr(snapshot, "entries", []) or [])
        total = int(getattr(snapshot, "total", len(entries)) or 0)
        if total > len(entries) and total > int(limit):
            try:
                expanded_snapshot = mempool_service.snapshot(limit=total)
                expanded_entries = list(getattr(expanded_snapshot, "entries", []) or [])
                if len(expanded_entries) > len(entries):
                    snapshot = expanded_snapshot
                    entries = expanded_entries
                    total = int(getattr(snapshot, "total", len(entries)) or 0)
            except Exception:
                pass
        for entry in entries:
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
                "entries": len(entries),
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
    parent_height: int,
    parent_hash: bytes,
    parent_header: Any,
    *,
    coinbase: bytes | None = None,
    instant_block: bool = False,
    disable_block_time_limits: bool = False,
) -> Header:
    _, _, timestamp = _timestamp_bounds(
        parent_header,
        disable_block_time_limits=disable_block_time_limits,
    )
    theta = _resolve_theta()
    if int(theta or 0) <= 0:
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


def _timestamp_bounds(
    parent_header: Any,
    *,
    disable_block_time_limits: bool = False,
) -> tuple[int, int | None, int]:
    parent_ts = _header_timestamp_seconds(parent_header)
    min_delta = 0 if disable_block_time_limits else int(math.ceil(_min_block_spacing_s()))
    timestamp_min = parent_ts + min_delta if parent_ts else int(time.time())
    now = int(time.time())
    max_future = (
        0 if disable_block_time_limits else int(os.getenv("ANIMICA_MAX_FUTURE_SECONDS", "5"))
    )
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
        from execution.runtime.env import make_tx_env
    except ImportError:
        logger.warning("execution.runtime not available; cannot execute transactions")
        return [{"status": 0, "gasUsed": 0, "logs": []} for _ in txs]

    apply_dispatch = None
    resolve_kind = None
    try:
        from execution.runtime.dispatcher import dispatch as apply_dispatch
        from execution.runtime.dispatcher import resolve_tx_kind as resolve_kind
    except ImportError:
        apply_dispatch = None
        resolve_kind = None

    apply_transfer = None
    if apply_dispatch is None:
        try:
            from execution.runtime.transfers import apply_transfer
        except ImportError:
            apply_transfer = None

    if apply_dispatch is None and apply_transfer is None:
        logger.warning("execution.runtime dispatcher not available; cannot execute transactions")
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

        tx_kind: str | None = None
        if callable(resolve_kind):
            try:
                tx_kind = str(resolve_kind(tx))
            except Exception:
                tx_kind = None

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

            # Execute state transition (use dispatcher when available).
            if callable(apply_dispatch):
                res = apply_dispatch(tx=tx, state=state_db, block_env=block_env, tx_env=tx_env)
            elif callable(apply_transfer):
                res = apply_transfer(tx=tx, state=state_db, block_env=block_env, tx_env=tx_env)
                tx_kind = tx_kind or "transfer"
            else:
                raise RuntimeError("No execution handler available")

            # Log execution result
            status_str = "SUCCESS" if res.is_success else "FAILED"
            logger.info(
                f"Transaction {idx} executed: status={status_str}, "
                f"gasUsed={res.gas_used}, logs={len(res.logs or [])}"
            )

            # Receipt-like view
            # Note: res.is_success checks if res.status == TxStatus.SUCCESS
            receipt_view: dict[str, Any] = {
                "status": 1 if res.is_success else 0,
                "gasUsed": int(res.gas_used or 0),
                "logs": res.logs or [],
            }

            is_deploy = tx_kind == "deploy"
            if not is_deploy and callable(is_python_vm_package_deploy_tx):
                try:
                    is_deploy = bool(is_python_vm_package_deploy_tx(tx))
                except Exception:
                    is_deploy = False

            if (
                is_deploy
                and res.is_success
                and callable(build_python_vm_package_deploy_metadata)
            ):
                try:
                    tx_hash_hex = _canonical_txid_hex(tx)
                    deploy_meta = build_python_vm_package_deploy_metadata(
                        tx,
                        tx_hash=tx_hash_hex,
                        sender=sender_bytes,
                        block_number=getattr(block_env, "height", None),
                        tx_index=idx,
                        status=1,
                        chain_id=getattr(block_env, "chain_id", None),
                    )
                except Exception as meta_err:
                    logger.debug(
                        "Failed to compute deploy metadata during execution",
                        extra={"tx_index": idx, "error": str(meta_err)},
                    )
                    deploy_meta = None

                if isinstance(deploy_meta, dict):
                    contract_address = deploy_meta.get("contractAddress")
                    if isinstance(contract_address, str) and contract_address:
                        receipt_view["contractAddress"] = contract_address
                        receipt_view["createdAddress"] = contract_address
                    for key in ("deploymentType", "codeHash", "manifestHash", "sender"):
                        value = deploy_meta.get(key)
                        if value is not None:
                            receipt_view[key] = value

            receipts.append(receipt_view)
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
    mempool_service = _resolve_mempool_service(ctx)
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
        snapshot_limit = _mempool_snapshot_limit(mempool_service)
        log.info("_mine_once: Starting transaction collection from mempool adapter")
        log.info(f"_mine_once: Adapter has miner_feed: {adapter.miner_feed is not None}")
        pending_entries, pending_raw_by_hash, pending_total = _collect_mempool_entries(
            ctx=ctx,
            adapter=adapter,
            limit=snapshot_limit,
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
                snapshot_limit = _mempool_snapshot_limit(
                    mempool_service, default_limit=snapshot_limit
                )
                pending_entries, pending_raw_by_hash, pending_total = (
                    _collect_mempool_entries(
                        ctx=ctx,
                        adapter=adapter,
                        limit=snapshot_limit,
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
                "max_txs": min(
                    int(DEFAULT_BLOCK_TX_LIMIT),
                    max(1000, int(pending_total or 0), len(normalized_entries)),
                ),
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
                "included_hashes_sample": list(included_hashes[:10]),
                "rejected_by_hash_sample": dict(
                    list(merged_rejected_by_hash.items())[:10]
                ),
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
        head_ts = _head_timestamp_seconds() or int(time.time())
        parent_header = Header(
            v=1,
            chainId=_ctx().cfg.chain_id,
            height=parent_height,
            parentHash=parent_hash_bytes,
            timestamp=int(head_ts),
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
        parent_timestamp = _header_timestamp_seconds(parent_header)
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
    network_blocks_skipped = 1
    stale_dt_seconds = None
    stale_blocks_skipped = 1
    if parent_header is not None:
        head_timestamp = _header_timestamp_seconds(parent_header)
        network_dt = _network_block_interval(parent_height, head_timestamp)
        if network_dt is not None:
            network_dt_seconds, network_blocks_skipped = network_dt
        else:
            head_hash = parent_hash_val if isinstance(parent_hash_val, str) else None
            stale_dt = _stale_head_interval(head_hash, head_timestamp)
            if stale_dt is not None:
                stale_dt_seconds, stale_blocks_skipped = stale_dt
    # Always keep header theta canonical. Even when the previous block is old,
    # forcing a local minimum theta can diverge from importer validation and
    # stall progress at retarget-window enforcement boundaries.
    if force_block_due_to_time:
        adjusted_theta = _adjust_theta_for_mining(
            float(max_block_time_s), blocks_skipped=1
        )
        try:
            header_template = replace(header_template, thetaMicro=adjusted_theta)
            log.info(
                "Previous block exceeded max_block_time_s; "
                "using canonical theta %.3f nats for forced mining attempt",
                adjusted_theta / 1e6,
            )
        except Exception as e:
            log.warning(
                "Failed to apply canonical theta during forced block attempt: %s",
                e,
            )
    elif network_dt_seconds is not None:
        adjusted_theta = _adjust_theta_for_mining(
            network_dt_seconds, blocks_skipped=network_blocks_skipped
        )
        try:
            header_template = replace(header_template, thetaMicro=adjusted_theta)
            log.debug(
                "Applied dynamic theta adjustment: %.3f nats (network dt=%.2fs, steps=%d)",
                adjusted_theta / 1e6,
                network_dt_seconds,
                network_blocks_skipped,
            )
        except Exception as e:
            log.warning(f"Failed to apply theta adjustment to header: {e}")
    elif stale_dt_seconds is not None:
        adjusted_theta = _adjust_theta_for_mining(
            stale_dt_seconds, blocks_skipped=stale_blocks_skipped
        )
        try:
            header_template = replace(header_template, thetaMicro=adjusted_theta)
            log.debug(
                "Applied dynamic theta adjustment: %.3f nats (stale head age=%.2fs, steps=%d)",
                adjusted_theta / 1e6,
                stale_dt_seconds,
                stale_blocks_skipped,
            )
        except Exception as e:
            log.warning(f"Failed to apply stale-head theta adjustment to header: {e}")
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
                                # Prefer canonical hash from tracked raw bytes.
                                tracked = _tracked(tx)
                                if tracked:
                                    tx_hash_hex, _raw = tracked
                                else:
                                    tx_hash_hex = _canonical_txid_hex(tx)

                                tx_hash_hex = _normalize_hash_hex(tx_hash_hex)
                                if not (tx_hash_hex.startswith("0x") and len(tx_hash_hex) == 66):
                                    continue

                                tx_hash = bytes.fromhex(tx_hash_hex[2:])

                                # Store receipt pointer using canonical hash
                                # Format: PFX_RXI + tx_hash → {"h": height, "i": idx, "b": block_hash}
                                receipt_ptr = cbor_dumps({"h": header.height, "i": idx, "b": block_hash_bytes})
                                batch.put(PFX_RXI + tx_hash, receipt_ptr)
                                log.debug(f"Re-indexed receipt for canonical hash: {tx_hash_hex[:16]}...")

                                # Persist deployment metadata index for successful deploy txs.
                                receipt_view = receipts_dict[idx] if idx < len(receipts_dict) else {}
                                status_int = None
                                try:
                                    status_int = int(receipt_view.get("status")) if isinstance(receipt_view, dict) else None
                                except Exception:
                                    status_int = None

                                if (
                                    status_int == 1
                                    and callable(build_python_vm_package_deploy_metadata)
                                ):
                                    try:
                                        deploy_meta = build_python_vm_package_deploy_metadata(
                                            tx,
                                            tx_hash=tx_hash_hex,
                                            block_hash=block_hash_bytes,
                                            block_number=header.height,
                                            tx_index=idx,
                                            status=status_int,
                                            chain_id=header.chainId,
                                        )
                                    except Exception as meta_err:
                                        log.debug(
                                            "Failed to build deploy metadata for tx",
                                            extra={"tx_hash": tx_hash_hex, "error": str(meta_err)},
                                        )
                                        deploy_meta = None

                                    if isinstance(deploy_meta, dict):
                                        if hasattr(block_db, "put_deploy_metadata_by_tx_hash"):
                                            block_db.put_deploy_metadata_by_tx_hash(  # type: ignore[attr-defined]
                                                tx_hash, deploy_meta, batch=batch
                                            )
                                        else:
                                            # Fallback path for older BlockDB implementations.
                                            batch.put(b"\x23" + tx_hash, cbor_dumps(deploy_meta))
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
    from mining.templates import TemplateBuilder, compute_job_id
    from mining.share_submitter import json_sanitize
    from mining.template_block import (
        header_from_template_view,
        header_sign_bytes_from_template_view,
    )

    def _looks_like_address(value: str) -> bool:
        candidate = value.strip()
        if not candidate:
            return False
        if candidate.lower().startswith("anim"):
            return True
        if candidate.startswith("0x"):
            return True
        if len(candidate) == 64:
            try:
                int(candidate, 16)
                return True
            except Exception:
                return False
        return False

    algo_hint: str | None = None
    payout_address_input: str | None = None
    include_mempool_requested = True
    proof_type = "sha256d"

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
        elif len(params_list) == 1 and isinstance(params_list[0], str):
            payload = None
            value = params_list[0].strip()
            if _looks_like_address(value):
                payout_address_input = value
            else:
                algo_hint = value
        elif (
            len(params_list) == 2
            and isinstance(params_list[0], str)
            and isinstance(params_list[1], (bool, int))
        ):
            payload = None
            value = params_list[0].strip()
            if _looks_like_address(value):
                payout_address_input = value
            else:
                algo_hint = value
            include_mempool_requested = bool(params_list[1])
        else:
            raise ValueError(
                "expected params object or [address?, include_mempool?] or [algo_hint]"
            )
    elif isinstance(params, str):
        payload = None
        value = params.strip()
        if _looks_like_address(value):
            payout_address_input = value
        else:
            algo_hint = value
    else:
        raise ValueError("params must be array or object")

    if payload:
        address_candidate = (
            payload.get("address")
            or payload.get("payout_address")
            or payload.get("payoutAddress")
        )
        if isinstance(address_candidate, str) and address_candidate.strip():
            payout_address_input = address_candidate.strip()
        algo_hint = str(
            payload.get("algo")
            or payload.get("algorithm")
            or algo_hint
            or "asic_sha256"
        )
        include_mempool_requested = bool(
            payload.get(
                "include_mempool", payload.get("includeMempool", include_mempool_requested)
            )
        )
        proof_type = str(payload.get("proof") or payload.get("proofType") or proof_type)
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
    job = tb.current_job(force=True, proof_type=proof_type)
    sign_bytes = job.sign_bytes

    payout_address: str | None = None
    payout_address_bytes: bytes | None = None
    if payout_address_input:
        payout_address = _validate_payout_address(payout_address_input)
        payout_address_bytes = _as_bytes32_addr(payout_address)
    else:
        try:
            payout_address_bytes = _get_miner_address()
            if payout_address_bytes != ZERO32:
                payout_address = _to_hex(payout_address_bytes)
        except Exception:
            payout_address = None
            payout_address_bytes = None

    if payout_address_bytes and payout_address_bytes != ZERO32:
        header_extra = _encode_header_extra(coinbase=payout_address_bytes)
        if header_extra != getattr(job.header, "extra", b""):
            header_with_extra = replace(job.header, extra=header_extra)
            sign_bytes = header_with_extra.to_sign_bytes()
            job_id = compute_job_id(
                parent_hash=job.parent_hash,
                parent_height=job.parent_height,
                chain_id=job.chain_id,
                theta_target_micro=job.theta_target_micro,
                target=job.target,
                proof_type=job.proof_type,
                template_version=job.template_version,
                header=header_with_extra,
                challenge=job.challenge,
                script_hash=job.script_hash,
                inputs_commit=job.inputs_commit,
                outputs_commit=job.outputs_commit,
            )
            job = replace(job, job_id=job_id, header=header_with_extra, sign_bytes=sign_bytes)

    theta = job.theta_target_micro
    block_target = job.target
    header_dict = asdict(job.header)
    # asdict preserves bytes; coerce to hex for JSON clients
    header_view = {
        k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header_dict.items()
    }
    parent_hash_bytes = _bytes32(header_view.get("parent_hash") or header_view.get("parentHash") or job.parent_hash)
    parent_height = int(job.parent_height)
    chain_id = int(job.chain_id)
    block_template_id: str | None = None
    template_txs_raw: list[str] = []
    template_proofs: list[Any] = []
    mempool_summary: dict[str, Any] = {
        "pending": 0,
        "selected": 0,
        "rejected": {},
        "rejectedByHash": {},
        "mempoolEnabled": include_mempool_requested,
    }

    mempool_service = _resolve_mempool_service(_ctx())
    pending_count = (
        _mempool_pending_count(mempool_service)
        if include_mempool_requested
        else 0
    )
    mempool_summary["pending"] = int(pending_count)

    # Materialize a full block template whenever we have a payout address.
    # This keeps getWork aligned with getBlockTemplate behavior, including
    # stale-head theta relaxation even when the mempool is currently empty.
    if payout_address is not None:
        try:
            template_payload = miner_get_block_template(
                {
                    "address": payout_address,
                    "include_mempool": bool(include_mempool_requested),
                    "sync_peer_mempools": False,
                }
            )
            if isinstance(template_payload, dict) and not template_payload.get("enabled", True):
                reason = str(template_payload.get("reason") or "template_unavailable")
                if include_mempool_requested and int(pending_count) > 0:
                    # Do not silently mine header-only work when we know there are pending txs.
                    # Surface template unavailability so callers can wait/retry instead.
                    return {
                        "enabled": False,
                        "reason": reason,
                        "waitSeconds": template_payload.get("waitSeconds"),
                        "head": template_payload.get("head"),
                        "mempoolEnabled": True,
                        "mempool": {
                            "pending": int(pending_count),
                            "selected": 0,
                            "rejected": {reason: int(pending_count)},
                            "rejectedByHash": {},
                            "mempoolEnabled": True,
                        },
                    }
                log.info(
                    "miner.getWork template unavailable; using header-only work",
                    extra={"reason": reason, "pending_count": int(pending_count)},
                )
            elif (
                isinstance(template_payload, dict)
                and isinstance(template_payload.get("header"), dict)
                and template_payload.get("target") is not None
            ):
                template_header_view = dict(template_payload.get("header") or {})
                _ = header_from_template_view(template_header_view, nonce=0)
                sign_bytes = header_sign_bytes_from_template_view(template_header_view)
                header_view = template_header_view
                theta = int(
                    header_view.get("thetaMicro")
                    or header_view.get("theta_target_micro")
                    or theta
                )
                target_value = template_payload.get("target")
                if isinstance(target_value, str):
                    block_target = int(target_value, 16) if target_value.startswith("0x") else int(target_value)
                else:
                    block_target = int(target_value)
                parent_hash_bytes = _bytes32(
                    header_view.get("parentHash")
                    or header_view.get("parent_hash")
                    or parent_hash_bytes
                )
                parent_obj = template_payload.get("parent")
                if isinstance(parent_obj, dict):
                    parent_height = int(parent_obj.get("height") or parent_height)
                else:
                    parent_height = max(0, int(header_view.get("height") or header_view.get("number") or 0) - 1)
                chain_id = int(header_view.get("chainId") or chain_id)
                template_txs_raw = _extract_template_raw_txs(template_payload)
                proofs_payload = template_payload.get("proofs")
                if isinstance(proofs_payload, list):
                    template_proofs = list(proofs_payload)
                block_template_id = str(
                    template_payload.get("templateId")
                    or template_payload.get("template_id")
                    or ""
                ) or None
                tx_count_hint = int(
                    template_payload.get("txCount")
                    or len(template_txs_raw)
                )
                mempool_summary_raw = template_payload.get("mempool")
                if isinstance(mempool_summary_raw, dict):
                    mempool_summary = dict(mempool_summary_raw)
                mempool_summary["pending"] = int(
                    mempool_summary.get("pending")
                    or mempool_summary.get("mempoolTotal")
                    or tx_count_hint
                    or pending_count
                )
                mempool_summary.setdefault("selected", int(len(template_txs_raw)))
                mempool_summary.setdefault("rejected", {})
                mempool_summary.setdefault("rejectedByHash", {})
                mempool_summary["mempoolEnabled"] = True
            else:
                raise ValueError("materialized template missing header/target")
        except Exception as exc:
            log.warning(
                "miner.getWork failed to materialize block template; falling back to header-only work",
                extra={"error": str(exc)},
            )
            if include_mempool_requested and int(pending_count) > 0:
                return {
                    "enabled": False,
                    "reason": "template_materialization_failed",
                    "waitSeconds": None,
                    "mempoolEnabled": True,
                    "mempool": {
                        "pending": int(pending_count),
                        "selected": 0,
                        "rejected": {"template_materialization_failed": int(pending_count)},
                        "rejectedByHash": {},
                        "mempoolEnabled": True,
                    },
                }

    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(share_microtarget(theta, shares_per_block=1)) / float(
                theta or 1
            )
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    head_snapshot = _current_head_snapshot()
    now = time.time()
    for cached_job_id, cached in list(_JOB_CACHE.items()):
        created_at = float(cached.get("created_at") or 0)
        if (
            created_at < now - _JOB_CACHE_TIMEOUT_S
            or cached.get("head_generation") != head_snapshot.get("generation")
        ):
            _JOB_CACHE.pop(cached_job_id, None)

    job_id = block_template_id or job.job_id
    job_height = int(header_view.get("height") or header_view.get("number") or job.header.number)
    mix_seed_hex = header_view.get("mixSeed") or header_view.get("mix_seed")
    mix_seed_bytes = _bytes32(mix_seed_hex) if mix_seed_hex is not None else bytes(job.header.mix_seed)
    tx_count = int(len(template_txs_raw)) if template_txs_raw else int(pending_count)

    _JOB_CACHE[job_id] = {
        "job": job,
        "header_override": dict(header_view),
        "sign_bytes": sign_bytes,
        "mix_seed": mix_seed_bytes,
        "block_target": block_target,
        "share_target": share_target,
        "height": int(job_height),
        "created_at": time.time(),
        "parent_hash": parent_hash_bytes,
        "parent_height": int(parent_height),
        "chain_id": int(chain_id),
        "head_generation": head_snapshot.get("generation"),
        "payout_address": payout_address,
        "payout_address_bytes": payout_address_bytes,
        "template_id": block_template_id,
        "template_txs_raw": list(template_txs_raw),
        "template_proofs": list(template_proofs),
        "mempool": dict(mempool_summary),
    }

    return {
        "jobId": job_id,
        "templateId": job_id,
        "header": header_view,
        "thetaMicro": int(theta),
        "miningEnabled": True,
        "mempoolEnabled": include_mempool_requested,
        "mempool": mempool_summary,
        "txCount": tx_count,
        "shareTarget": float(share_target),
        "target": hex(block_target),
        "height": int(job_height),
        "parentHash": _to_hex(parent_hash_bytes),
        "parentHeight": int(parent_height),
        "chainId": int(chain_id),
        "createdAt": int(time.time()),
        "expiresAt": int(job.expires_at) if job.expires_at else None,
        "headGeneration": head_snapshot.get("generation"),
        "hints": {"mixSeed": _to_hex(mix_seed_bytes)},
        "signBytes": _to_hex(sign_bytes),
        "algo": algo_hint,
        "proofType": proof_type,
        "challenge": json_sanitize(job.challenge),
        "scriptHash": job.script_hash,
        "inputsCommit": job.inputs_commit,
        "outputsCommit": job.outputs_commit,
        "templateVersion": int(job.template_version),
        "address": payout_address,
        "payout_address": payout_address,
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

    nonce_int = _parse_nonce_int(nonce_val)
    header = _header_from_cached_job(job, nonce=nonce_int)
    digest = header.hash()
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

    _JOB_CACHE.pop(str(job_id), None)
    cached_txs_raw = job.get("template_txs_raw")
    if not isinstance(cached_txs_raw, list):
        cached_txs_raw = []
    cached_proofs = job.get("template_proofs")
    if not isinstance(cached_proofs, list):
        cached_proofs = []
    cached_template_id = job.get("template_id")
    block_payload: dict[str, Any] = {
        "header": _serialize_header_for_submit(header),
        "txs": list(cached_txs_raw),
        "proofs": list(cached_proofs),
        "parentHash": _to_hex(header.parentHash),
    }
    if isinstance(cached_template_id, str) and cached_template_id:
        block_payload["templateId"] = cached_template_id

    try:
        submit_result = miner_submit_block(block_payload)
    except rpc_errors.RpcError as exc:
        error_data = getattr(exc, "data", None)
        reason = None
        if isinstance(error_data, dict):
            reason = error_data.get("reason")
        reason_str = str(reason or exc).lower()
        if (
            exc.code == rpc_errors.AnimicaCode.STALE_TEMPLATE
            or "stale" in reason_str
            or "parent" in reason_str
        ):
            head_after_reject = _current_head_snapshot()
            return {
                "accepted": False,
                "jobId": job_id,
                "stale": True,
                "reason": str(reason or "stale-template"),
                "head": {
                    "height": int(head_after_reject.get("height") or 0),
                    "hash": head_after_reject.get("hash"),
                },
            }
        raise
    except Exception as exc:
        log.exception(
            "miner.submitWork unexpected submit failure",
            extra={
                "job_id": job_id,
                "height": int(getattr(header, "height", 0) or 0),
                "parent_hash": _to_hex(header.parentHash),
                "nonce": int(nonce_int),
            },
        )
        raise rpc_errors.RpcError(
            rpc_errors.AnimicaCode.SERVER_ERROR,
            "submitWork failed",
            {
                "reason": "submit_work_failed",
                "detail": f"{exc.__class__.__name__}: {exc}",
            },
        ) from exc

    head_after = _current_head_snapshot()
    head_after_height = int(head_after.get("height") or 0)
    head_after_hash = head_after.get("hash")
    submit_new_head = (
        submit_result.get("newHead")
        if isinstance(submit_result.get("newHead"), dict)
        else {}
    )
    submit_height_raw = (
        submit_new_head.get("height")
        or submit_result.get("new_head")
        or submit_result.get("height")
        or 0
    )
    try:
        submit_height = int(submit_height_raw)
    except Exception:
        submit_height = 0
    submit_hash = (
        submit_new_head.get("hash")
        or submit_result.get("new_head_hash")
        or submit_result.get("block_hash")
    )

    canonical_height = head_after_height
    canonical_hash = head_after_hash
    if submit_height > canonical_height:
        canonical_height = submit_height
        canonical_hash = submit_hash or canonical_hash
    elif canonical_height == submit_height and not canonical_hash:
        canonical_hash = submit_hash
    canonical_head = {"height": int(canonical_height), "hash": canonical_hash}

    expected_reward = submit_result.get("expected_reward")
    credited_amount = submit_result.get("credited_amount")
    balance_before = submit_result.get("balance_before")
    balance_now = submit_result.get("balance_now")
    credited_delta = submit_result.get("credited_delta")

    try:
        expected_reward = int(expected_reward) if expected_reward is not None else None
    except Exception:
        expected_reward = None
    try:
        credited_amount = int(credited_amount) if credited_amount is not None else None
    except Exception:
        credited_amount = None
    try:
        balance_before = int(balance_before) if balance_before is not None else None
    except Exception:
        balance_before = None
    try:
        balance_now = int(balance_now) if balance_now is not None else None
    except Exception:
        balance_now = None
    try:
        credited_delta = int(credited_delta) if credited_delta is not None else None
    except Exception:
        credited_delta = None

    res.update(
        {
            "reason": None,
            "height": int(canonical_head["height"] or job.get("height", 0)),
            "newHead": canonical_head,
            "new_head": int(canonical_head["height"] or 0),
            "new_head_hash": canonical_head.get("hash"),
            "block_hash": submit_result.get("block_hash") or block_hash,
            "duplicate": bool(submit_result.get("duplicate", False)),
            "expected_reward": expected_reward,
            "credited_amount": credited_amount,
            "balance_before": balance_before,
            "balance_now": balance_now,
            "credited_delta": credited_delta,
            "credited_source": submit_result.get("credited_source"),
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
        allow_offline_mining: Explicit offline override request (policy-validated).
        allow_unsynced_mining: Requested unsynced override (restricted/ignored by policy).
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
        allow_offline_flag = _resolve_allow_offline_mining(
            bool(allow_offline_mining), source="miner.mine"
        )
        if allow_unsynced_flag:
            log.warning(
                "Unsafe unsynced mining override requested; ignoring.",
                extra={
                    "allow_unsynced_mining": bool(allow_unsynced_flag),
                },
            )
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
    
    # Parse payout address strictly (no silent fallback to default/zero)
    payout_address_bytes: bytes | None = None
    if address is not None:
        payout_address_bytes = _require_payout_address_bytes(address)
        log.info(f"Using custom payout address: {address}")
    else:
        payout_address_bytes = _get_miner_address()
        if payout_address_bytes == ZERO32:
            raise rpc_errors.InvalidParams("Select payout address")
    
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
            "allow_offline_mining": allow_offline_flag if not instant_block_flag else None,
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
    include_aicf = False
    force_empty_template = False
    disable_block_time_limits = False
    sync_peer_mempools = True
    raw_params: dict[str, Any] | list[Any] | None = None
    template_ttl_s = _TEMPLATE_TTL_S

    if args:
        raw_params = list(args)
        if len(args) == 1 and isinstance(args[0], dict):
            payload = args[0]
        else:
            if len(args) > 3:
                raise rpc_errors.InvalidParams("expected at most 3 positional arguments")
            payout_address = args[0]
            if len(args) > 1:
                include_mempool_flag = bool(args[1])
            if len(args) > 2:
                disable_block_time_limits = bool(args[2])
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
        disable_block_time_limits = bool(
            payload.get(
                "disable_block_time_limits",
                payload.get("disableBlockTimeLimits", False),
            )
        )
        include_aicf = bool(
            payload.get("include_aicf", payload.get("includeAicf", False))
        )
        sync_peer_mempools = bool(
            payload.get("sync_peer_mempools", payload.get("syncPeerMempools", sync_peer_mempools))
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
            "disable_block_time_limits",
            "disableBlockTimeLimits",
            "ttl_seconds",
            "ttlSeconds",
            "include_aicf",
            "includeAicf",
            "sync_peer_mempools",
            "syncPeerMempools",
        }
        if unknown:
            raise rpc_errors.InvalidParams(
                f"unexpected params: {', '.join(sorted(unknown))}"
            )

    if not payout_address:
        raise rpc_errors.InvalidParams("address is required")
    payout_address = _validate_payout_address(payout_address)

    requested_allow_offline_mining = bool(allow_offline_mining)
    allow_offline_mining = _resolve_allow_offline_mining(
        requested_allow_offline_mining, source="miner.getBlockTemplate"
    )

    if allow_unsynced_mining:
        log.warning(
            "Unsafe unsynced mining override requested; ignoring.",
            extra={
                "allow_unsynced_mining": bool(allow_unsynced_mining),
            },
        )
        allow_unsynced_mining = False

    log.info(
        "miner.getBlockTemplate request",
        extra={
            "params": raw_params or payload,
            "include_mempool": include_mempool_flag,
            "allow_offline_mining_requested": requested_allow_offline_mining,
            "allow_offline_mining": allow_offline_mining,
            "allow_unsynced_mining": allow_unsynced_mining,
            "force_empty_template": force_empty_template,
            "disable_block_time_limits": disable_block_time_limits,
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
    if min_spacing_s > 0 and not disable_block_time_limits:
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
            snapshot_limit = _mempool_snapshot_limit(mempool_service)
            if sync_peer_mempools:
                # Sync mempools from peers to improve template completeness for external miners.
                synced_peers = _sync_all_peer_mempools(timeout_s=1.5)
                if synced_peers > 0:
                    log.info(
                        "Synced peer mempools before building block template",
                        extra={"peers_synced": synced_peers},
                    )
            
            pending_entries, pending_raw_by_hash, pending_total = _collect_mempool_entries(
                ctx=ctx,
                adapter=adapter,
                limit=snapshot_limit,
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
            if not pending_entries and sync_peer_mempools:
                requested = _request_missing_mempool_txs(limit=128, wait_s=0.25)
                if requested:
                    log.info(
                        "block template requested missing txids",
                        extra={"requested": requested},
                    )
                    time.sleep(0.25)
                    snapshot_limit = _mempool_snapshot_limit(
                        mempool_service, default_limit=snapshot_limit
                    )
                    pending_entries, pending_raw_by_hash, pending_total = (
                        _collect_mempool_entries(
                            ctx=ctx,
                            adapter=adapter,
                            limit=snapshot_limit,
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
                    "max_txs": min(
                        int(DEFAULT_BLOCK_TX_LIMIT),
                        max(1000, int(pending_total or 0), len(normalized_entries)),
                    ),
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
                    "included_hashes_sample": list(included_hashes[:10]),
                    "rejected_by_hash_sample": dict(
                        list(merged_rejected_by_hash.items())[:10]
                    ),
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

        try:
            head = adapter.get_head()
        except Exception as exc:
            if not allow_offline_mining:
                raise
            log.warning(
                "miner.getBlockTemplate: adapter head unavailable in offline-override mode; using local snapshot fallback",
                extra={"error": str(exc)},
            )
            try:
                head = _current_head_snapshot()
            except Exception:
                head = {"height": 0, "hash": "0x" + ZERO32.hex(), "header": None}
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
            head_ts = _head_timestamp_seconds() or int(time.time())
            parent_header = Header(
                v=1,
                chainId=_ctx().cfg.chain_id,
                height=parent_height,
                parentHash=parent_hash_bytes,
                timestamp=int(head_ts),
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

        timestamp_min, timestamp_max, _ = _timestamp_bounds(
            parent_header,
            disable_block_time_limits=disable_block_time_limits,
        )
        
        # Convert payout address to bytes for coinbase (strict/no zero fallback)
        coinbase_bytes = _require_payout_address_bytes(payout_address)
        
        header_template = _build_child_header(
            parent_height,
            parent_hash_bytes,
            parent_header,
            coinbase=coinbase_bytes,
            disable_block_time_limits=disable_block_time_limits,
        )

        network_dt_seconds = None
        network_blocks_skipped = 1
        stale_dt_seconds = None
        stale_blocks_skipped = 1
        try:
            head_timestamp = _header_timestamp_seconds(parent_header)
            network_dt = _network_block_interval(parent_height, head_timestamp)
            if network_dt is not None:
                network_dt_seconds, network_blocks_skipped = network_dt
            else:
                head_hash = parent_hash_val if isinstance(parent_hash_val, str) else None
                stale_dt = _stale_head_interval(head_hash, head_timestamp)
                if stale_dt is not None:
                    stale_dt_seconds, stale_blocks_skipped = stale_dt
        except Exception:
            network_dt_seconds = None
        if network_dt_seconds is not None:
            adjusted_theta = _adjust_theta_for_mining(
                network_dt_seconds, blocks_skipped=network_blocks_skipped
            )
            try:
                header_template = replace(header_template, thetaMicro=adjusted_theta)
            except Exception:
                pass
        elif stale_dt_seconds is not None:
            adjusted_theta = _adjust_theta_for_mining(
                stale_dt_seconds, blocks_skipped=stale_blocks_skipped
            )
            try:
                header_template = replace(header_template, thetaMicro=adjusted_theta)
            except Exception:
                pass
        else:
            last_block_time = _MINING_STATE.get("last_block_time")
            if last_block_time is not None:
                dt_seconds = time.time() - float(last_block_time)
                adjusted_theta = _adjust_theta_for_mining(dt_seconds)
                try:
                    header_template = replace(header_template, thetaMicro=adjusted_theta)
                except Exception:
                    pass
            else:
                _adjust_theta_for_mining(dt_seconds=None)

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
                "disable_block_time_limits": bool(disable_block_time_limits),
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
            "disableBlockTimeLimits": bool(disable_block_time_limits),
            "disable_block_time_limits": bool(disable_block_time_limits),
            "usefulWorkPayload": _build_useful_work_payload() if include_aicf else None,
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
        raise rpc_errors.RpcError(
            rpc_errors.AnimicaCode.INTERNAL_ERROR,
            f"internal error: {exc}",
            {"reason": "NameError"},
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
    from mining.pow_validation import (derive_block_target_int, digest_from_sign_bytes,
                                       evaluate_digest, parse_hex_bytes,
                                       parse_nonce_int)

    nonce_int = parse_nonce_int(nonce, default=-1)
    if nonce_int < 0:
        return {"accepted": False, "reason": "invalid nonce"}

    mix_seed = share.get("mixSeed") or share.get("mix_seed") or cached.get("mix_seed")
    mix_seed_bytes = parse_hex_bytes(mix_seed, default=b"")
    if not mix_seed_bytes and isinstance(cached.get("mix_seed"), (bytes, bytearray)):
        mix_seed_bytes = bytes(cached.get("mix_seed"))

    digest: bytes | None = None
    digest_source = "header"
    try:
        header = _header_from_cached_job(cached, nonce=nonce_int)
        digest = header.hash()
    except Exception:
        digest = None

    if digest is None:
        digest_source = "sign_bytes"
        sign_bytes = cached.get("sign_bytes")
        if not isinstance(sign_bytes, (bytes, bytearray)):
            return {"accepted": False, "reason": "missing sign bytes"}
        digest = digest_from_sign_bytes(
            bytes(sign_bytes),
            mix_seed=mix_seed_bytes,
            nonce_int=nonce_int,
            nonce_byteorder="little",
        )

    header_override = cached.get("header_override")
    theta_default = 0
    if cached.get("job"):
        theta_default = int(getattr(cached["job"], "theta_target_micro", 0) or 0)
    if theta_default <= 0 and isinstance(header_override, dict):
        theta_default = int(
            header_override.get("thetaMicro")
            or header_override.get("thetaTargetMicro")
            or header_override.get("theta_target_micro")
            or 0
        )
    theta_micro = int(share.get("thetaMicro") or theta_default or 0)
    if theta_micro <= 0:
        return {"accepted": False, "reason": "missing thetaMicro"}

    share_target = float(share.get("shareTarget") or cached.get("share_target") or 0.0)
    decision = evaluate_digest(
        digest,
        theta_micro=theta_micro,
        share_ratio=share_target,
        block_target=derive_block_target_int(cached.get("block_target") or 0),
        enforce_share_target=True,
    )
    if decision.share_target_int <= 0:
        return {"accepted": False, "reason": "missing share target"}

    log.info(
        "miner_submit_share_target_compare",
        extra={
            "job_id": str(job_id),
            "worker": share.get("worker"),
            "address": share.get("minerAddress") or share.get("miner_address"),
            "nonce": int(nonce_int),
            "hash": decision.digest_hex,
            "hash_int": hex(decision.digest_int),
            "share_target": hex(decision.share_target_int),
            "share_ratio": float(decision.share_ratio),
            "theta_micro": int(decision.theta_micro),
            "share_threshold_micro": int(decision.share_threshold_micro),
            "source": digest_source,
            "pass": bool(decision.share_ok),
        },
    )
    if not decision.share_ok:
        return {"accepted": False, "reason": "low difficulty share"}

    is_block = bool(decision.is_block)
    log.info(
        "miner_submit_block_target_compare",
        extra={
            "job_id": str(job_id),
            "worker": share.get("worker"),
            "address": share.get("minerAddress") or share.get("miner_address"),
            "nonce": int(nonce_int),
            "hash": decision.digest_hex,
            "hash_int": hex(decision.digest_int),
            "block_target": hex(decision.block_target_int)
            if decision.block_target_int > 0
            else "0x0",
            "source": digest_source,
            "pass": bool(is_block),
        },
    )
    
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
        "hash": decision.digest_hex,
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
    disable_block_time_limits = bool(
        block.get("disable_block_time_limits") or block.get("disableBlockTimeLimits")
    )
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
        if isinstance(cached, dict):
            disable_block_time_limits = bool(
                disable_block_time_limits
                or cached.get("disable_block_time_limits")
            )
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

        payout_address = None
        payout_address_bytes: bytes | None = None
        if template_id:
            cached = _TEMPLATE_CACHE.get(str(template_id)) or {}
            payout_address = cached.get("payout_address")
            if payout_address:
                payout_address_bytes = _as_bytes32_addr(payout_address)
        if payout_address_bytes in (None, ZERO32):
            payout_address_bytes = _extract_coinbase_from_header_payload(block.get("header"))
        if payout_address_bytes == ZERO32:
            payout_address_bytes = None

        balance_before = None
        if payout_address_bytes is not None:
            try:
                balance_before = int(ctx.state_db.get_balance(payout_address_bytes))
            except Exception as e:
                log.warning(
                    "Failed to query payout balance before block submit: %s",
                    e,
                    exc_info=True,
                )

        params = block_import_mod._load_chain_params_for_import(  # type: ignore[attr-defined]
            getattr(ctx.cfg, "genesis_path", None)
        )
        importer = block_import_mod._get_importer(  # type: ignore[attr-defined]
            ctx.block_db, ctx.state_db, ctx.tx_index, params
        )
        restore_min_block_spacing_ms = None
        restore_max_future_seconds = None
        if disable_block_time_limits:
            if hasattr(importer, "_min_block_spacing_ms"):
                try:
                    restore_min_block_spacing_ms = int(
                        getattr(importer, "_min_block_spacing_ms")
                    )
                except Exception:
                    restore_min_block_spacing_ms = None
                try:
                    setattr(importer, "_min_block_spacing_ms", 0)
                except Exception:
                    pass
            if hasattr(importer, "_max_future_seconds"):
                try:
                    restore_max_future_seconds = int(
                        getattr(importer, "_max_future_seconds")
                    )
                except Exception:
                    restore_max_future_seconds = None
                try:
                    setattr(importer, "_max_future_seconds", 0)
                except Exception:
                    pass
            log.info(
                "miner.submitBlock disabling block-time guardrails for this submission",
                extra={"template_id": template_id, "parent_hash": parent_hash_hex},
            )
        try:
            result = importer.import_block(block)
        finally:
            if restore_min_block_spacing_ms is not None:
                try:
                    setattr(importer, "_min_block_spacing_ms", int(restore_min_block_spacing_ms))
                except Exception:
                    pass
            if restore_max_future_seconds is not None:
                try:
                    setattr(importer, "_max_future_seconds", int(restore_max_future_seconds))
                except Exception:
                    pass

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
            tx_hashes_sample: list[str] = []
            if raw_txs_hex:
                try:
                    from mempool.tx_hash import tx_hash_hex as _tx_hash_hex

                    for raw_hex in raw_txs_hex[:10]:
                        raw_bytes = bytes.fromhex(
                            raw_hex[2:] if raw_hex.startswith("0x") else raw_hex
                        )
                        tx_hashes_sample.append(_tx_hash_hex(raw_bytes))
                except Exception:
                    tx_hashes_sample = []
            if "pow" in reason_lower:
                reject_reason = "invalid_pow"
                code = rpc_errors.AnimicaCode.INVALID_POW
            elif "timestamp" in reason_lower:
                reject_reason = "invalid_timestamp"
                code = rpc_errors.AnimicaCode.INVALID_TIMESTAMP
            elif "parent" in reason_lower or "height continuity" in reason_lower:
                reject_reason = "invalid_parent"
                code = rpc_errors.AnimicaCode.INVALID_PARENT
            elif (
                "tx already applied on canonical chain" in reason_lower
                or "duplicate tx apply attempt rejected" in reason_lower
                or "duplicate tx hash in block" in reason_lower
                or "duplicate sender+nonce in block" in reason_lower
            ):
                reject_reason = "duplicate_tx"
                code = rpc_errors.AnimicaCode.INVALID_TX
            elif "coinbase" in reason_lower:
                reject_reason = "invalid_coinbase"
                code = rpc_errors.AnimicaCode.INVALID_COINBASE
            elif "merkle" in reason_lower or "txsroot" in reason_lower:
                reject_reason = "invalid_merkle_root"
                code = rpc_errors.AnimicaCode.INVALID_MERKLE_ROOT

            log.warning(
                "block_rejected",
                extra={
                    "stage": "block_apply",
                    "reason": reject_reason,
                    "detail": str(reason),
                    "height": result.height,
                    "tx_hashes_sample": tx_hashes_sample,
                },
            )

            raise rpc_errors.RpcError(
                code,
                "block rejected",
                {
                    "stage": "block_apply",
                    "reason": reject_reason,
                    "detail": reason,
                    "height": result.height,
                    "block_hash": result.block_hash.hex() if result.block_hash else None,
                    "tx_hashes_sample": tx_hashes_sample,
                },
            )

        block_obj = None
        if result.code == block_import_mod.ImportErrorCode.ACCEPTED:
            _metric_inc("imports_ok")
            _mark_head_progress()
            _MINING_STATE["last_block_time"] = time.time()
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

        # Calculate credited reward amount from canonical state delta.
        expected_reward = 0
        credited_amount = 0
        credited_delta = None
        credited_source = "none"
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
            if payout_address_bytes is not None:
                try:
                    balance_now = int(ctx.state_db.get_balance(payout_address_bytes))
                except Exception as e:
                    log.warning("Failed to query payout balance after accepted block: %s", e, exc_info=True)

            if isinstance(balance_before, int) and isinstance(balance_now, int):
                credited_delta = int(balance_now) - int(balance_before)
                if credited_delta >= 0:
                    credited_amount = int(credited_delta)
                    credited_source = "state_balance_delta"

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
                    expected_reward = int(rewards[0][1])
            except Exception as e:
                log.warning("Failed to calculate expected reward: %s", e)
            if credited_source == "none":
                credited_amount = int(expected_reward)
                credited_source = "expected_reward"

            # AICF Credit Minting: Mint credits from block reward + fees.
            # Gate on head_changed so credits are minted only when this block actually
            # advanced the canonical head. A fork-choice desync could leave a block
            # ACCEPTED-but-non-canonical (head frozen); the old code minted on every
            # such retry, inflating AICF credits for the same height. The root-cause
            # fix lives in block_import._apply_fork_choice (self-heal); this is
            # defense in depth so a stalled head can never mint duplicate credits.
            class _AicfMintSkipped(Exception):
                pass
            try:
                if not result.head_changed:
                    raise _AicfMintSkipped()
                from aicf.protocol.state import ProtocolState
                from aicf.credits.minting import mint_block_credits, get_aicf_slice_bps
                
                # Get AICF state DB path (default to data directory)
                data_dir = os.getenv("ANIMICA_DATA_DIR", os.path.expanduser("~/.animica/data"))
                aicf_db_path = os.path.join(data_dir, "aicf_protocol.db")
                
                # Initialize protocol state
                protocol_state = ProtocolState(aicf_db_path)
                
                # Get miner address for credit allocation
                miner_addr_bytes = (
                    payout_address_bytes if payout_address_bytes is not None else _get_miner_address()
                )
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
                    base_reward=expected_reward,
                    fees_collected=fees_collected,
                    aicf_slice_bps=aicf_slice_bps,
                )
                
                log.info(
                    f"AICF credits minted for block {result.height}: "
                    f"aicf_credits={mint_result['aicf_credits']}, "
                    f"ledger_id={mint_result['ledger_id']}"
                )
            except _AicfMintSkipped:
                log.debug(
                    "Skipped AICF mint for non-canonical block height=%s "
                    "(head did not advance)",
                    result.height,
                )
            except Exception as e:
                # Don't fail block submission if credit minting has issues
                # Credit minting is tracked separately and can be reconciled later
                log.warning(
                    f"Failed to mint AICF credits for block {result.height}: {e}",
                    exc_info=True
                )

        head_after = _current_head_snapshot()
        head_after_height = int(head_after.get("height") or 0)
        committed_height = int(result.height or 0)
        new_head_height = head_after_height
        # Only claim the submitted height as the new head if the import actually
        # advanced the canonical head. Previously this overrode unconditionally, so a
        # stalled head still reported new_head=committed_height — telling miners to move
        # on while the chain was frozen, a self-reinforcing resubmit loop.
        if result.head_changed and committed_height > new_head_height:
            new_head_height = committed_height

        new_head_hash = head_after.get("hash")
        if new_head_height == committed_height and (
            head_after_height < committed_height or not new_head_hash
        ):
            new_head_hash = block_hash_hex or new_head_hash
        if not new_head_hash:
            new_head_hash = block_hash_hex

        return {
            "accepted": True,
            "duplicate": result.code == block_import_mod.ImportErrorCode.DUPLICATE,
            "expected_reward": int(expected_reward),
            "credited_amount": int(credited_amount),
            "credited_delta": int(credited_delta) if credited_delta is not None else None,
            "credited_source": credited_source,
            "balance_before": int(balance_before) if balance_before is not None else None,
            "balance_now": int(balance_now) if balance_now is not None else None,
            "new_head": int(new_head_height),
            "new_head_hash": new_head_hash,
            "newHead": {"height": int(new_head_height), "hash": new_head_hash},
            "block_hash": block_hash_hex,
        }
    except rpc_errors.RpcError:
        raise
    except Exception as e:
        log.exception(
            "miner.submitBlock unexpected failure",
            extra={
                "template_id": template_id,
                "parent_hash": parent_hash_hex,
            },
        )
        raise rpc_errors.RpcError(
            rpc_errors.AnimicaCode.SERVER_ERROR,
            "block submission failed",
            {"reason": "submit_failed", "detail": f"{e.__class__.__name__}: {e}"},
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

        # Check min_block_spacing and compute retry hint
        retry_after_ms: int | None = None
        if allowed:
            min_spacing_s = _min_block_spacing_s()
            if min_spacing_s > 0:
                head_ts = _head_timestamp_seconds()
                if head_ts is not None:
                    import time as _time
                    now = _time.time()
                    earliest = head_ts + min_spacing_s
                    if now < earliest:
                        wait_s = max(0.0, earliest - now)
                        retry_after_ms = int(wait_s * 1000) + 50  # +50ms buffer
                        allowed = False
                        reason = "min_block_spacing"

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
            "retry_after_ms": retry_after_ms,
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
            "retry_after_ms": None,
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
