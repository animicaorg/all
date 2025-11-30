"""
capabilities.host.result_read
=============================

Deterministic getter for off-chain compute results:

    read_result(task_id: bytes, *, consume: bool = False) -> dict

Rules
-----
* Determinism / next-block rule: a result may be read only at heights
  >= ready_height + 1. (ready_height is when the job was resolved and
  recorded; typically the block height where the corresponding proof
  was observed/accepted.)
* If `consume=True`, the implementation marks the record as consumed
  (idempotently) at the current height so replays/read-twice are
  prevented at the host layer. (Actual persistence is adapter-specific.)
* Returns a compact, deterministic dict:
    {
        "task_id": bytes,            # 32-byte id
        "status": "PENDING" | "NOT_YET" | "READY",
        "ready_height": int | None,  # when it became available (if known)
        "min_read_height": int | None,  # ready_height+1 when NOT_YET
        "consumed": bool,            # True iff (consume=True) and persisted
        "result": bytes | None       # present only if status == "READY"
    }

Integration
-----------
This module prefers an adapter-backed result store if available:

- capabilities.jobs.result_store: get_result(task_id) / consume(task_id, height)
- capabilities.adapters.proofs: get_result(task_id)

It gracefully degrades to "PENDING" when no store/adapter is wired yet.

This provider is registered under RESULT_READ in the ProviderRegistry.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional, Tuple

from ..errors import CapError, NotDeterministic
from .provider import (RESULT_READ, ProviderRegistry, SyscallContext,
                       get_registry)

log = logging.getLogger("capabilities.host.result_read")

# Optional primary store (preferred)
_HAS_RESULT_STORE = False
try:
    from ..jobs import result_store as _result_store  # type: ignore

    _HAS_RESULT_STORE = True
except Exception:  # pragma: no cover
    _result_store = None

# Optional adapter-only lookup (fallback)
_HAS_PROOFS_ADAPTER = False
try:
    from ..adapters import proofs as _proofs_adapter  # type: ignore

    _HAS_PROOFS_ADAPTER = True
except Exception:  # pragma: no cover
    _proofs_adapter = None


# ----------------------------
# Internal helpers
# ----------------------------


def _normalize_record(rec: Any) -> Tuple[Optional[int], Optional[bytes], bool]:
    """
    Extract (ready_height, result_bytes, consumed) from a store/adapter record.

    Supports both dict-like and object-like records, being conservative.
    Unknown fields are treated as None/False.
    """
    if rec is None:
        return (None, None, False)

    # ready height
    ready_h = None
    for key in ("ready_height", "resolved_height", "height_ready", "height"):
        try:
            if isinstance(rec, dict):
                if key in rec and isinstance(rec[key], int):
                    ready_h = rec[key]
                    break
            else:
                v = getattr(rec, key)
                if isinstance(v, int):
                    ready_h = v
                    break
        except Exception:  # pragma: no cover
            pass

    # result bytes
    result_b = None
    for key in ("result", "output", "bytes", "data", "result_bytes", "output_bytes"):
        try:
            if isinstance(rec, dict):
                if key in rec and isinstance(rec[key], (bytes, bytearray)):
                    result_b = bytes(rec[key])
                    break
            else:
                v = getattr(rec, key)
                if isinstance(v, (bytes, bytearray)):
                    result_b = bytes(v)
                    break
        except Exception:  # pragma: no cover
            pass

    # consumed flag
    consumed = False
    for key in ("consumed", "is_consumed"):
        try:
            if isinstance(rec, dict):
                if key in rec and isinstance(rec[key], bool):
                    consumed = rec[key]
                    break
            else:
                v = getattr(rec, key)
                if isinstance(v, bool):
                    consumed = v
                    break
        except Exception:  # pragma: no cover
            pass

    return (ready_h, result_b, consumed)


def _get_record(task_id: bytes) -> Any:
    """
    Try to fetch the record from the recommended store; fall back to adapter;
    return None if unavailable.
    """
    # Prefer dedicated result_store API
    if _HAS_RESULT_STORE and _result_store is not None:
        for fn_name in ("get_result", "get", "lookup"):
            fn = getattr(_result_store, fn_name, None)
            if callable(fn):
                try:
                    return fn(task_id)  # type: ignore[misc]
                except KeyError:
                    return None
                except Exception as e:  # pragma: no cover
                    log.debug("result_store.%s failed", fn_name, exc_info=e)
                    break  # fall back to adapter

    # Fallback to proofs adapter
    if _HAS_PROOFS_ADAPTER and _proofs_adapter is not None:
        for fn_name in ("get_result", "lookup_result", "get_result_for_task"):
            fn = getattr(_proofs_adapter, fn_name, None)
            if callable(fn):
                try:
                    return fn(task_id)  # type: ignore[misc]
                except KeyError:
                    return None
                except Exception as e:  # pragma: no cover
                    log.debug("proofs_adapter.%s failed", fn_name, exc_info=e)
                    break

    return None


def _consume_record(task_id: bytes, height: int) -> bool:
    """
    Attempt to persist a 'consumed' mark. Returns True if persisted or already consumed.
    """
    if _HAS_RESULT_STORE and _result_store is not None:
        for fn_name in ("consume", "mark_consumed", "set_consumed"):
            fn = getattr(_result_store, fn_name, None)
            if callable(fn):
                try:
                    fn(task_id, height)  # type: ignore[misc]
                    return True
                except KeyError:
                    return False
                except Exception as e:  # pragma: no cover
                    log.debug("result_store.%s failed", fn_name, exc_info=e)
                    break
    # No durable consume path available
    return False


def _validate_task_id(task_id: bytes) -> None:
    if not isinstance(task_id, (bytes, bytearray)) or len(task_id) == 0:
        raise CapError("read_result: task_id must be non-empty bytes")
    # Recommend 32-byte ids (sha3_256) but accept others; nudge via log only.
    if len(task_id) != 32:
        log.debug("read_result: task_id is not 32 bytes", extra={"len": len(task_id)})


def _min_read_height(ready_height: int) -> int:
    # Enforce "next-block" visibility for deterministic consumption.
    return ready_height + 1


# ----------------------------
# Provider function
# ----------------------------


def _read_result(
    ctx: SyscallContext, *, task_id: bytes, consume: bool = False
) -> Dict[str, Any]:
    """
    Deterministically read a result record for `task_id`.

    Visibility requires ctx.height >= ready_height + 1.
    """
    _validate_task_id(task_id)

    rec = _get_record(task_id)
    ready_h, result_b, consumed = _normalize_record(rec)

    # No record yet anywhere
    if rec is None or ready_h is None:
        return {
            "task_id": bytes(task_id),
            "status": "PENDING",
            "ready_height": None,
            "min_read_height": None,
            "consumed": False,
            "result": None,
        }

    # Enforce next-block rule (determinism)
    min_h = _min_read_height(ready_h)
    if ctx.height < min_h:
        return {
            "task_id": bytes(task_id),
            "status": "NOT_YET",
            "ready_height": int(ready_h),
            "min_read_height": int(min_h),
            "consumed": bool(consumed),
            "result": None,
        }

    # At/after min height, result may be revealed.
    # Consume if requested and supported by the underlying store.
    did_consume = False
    if consume:
        did_consume = _consume_record(task_id, ctx.height)
        consumed = consumed or did_consume

    # The result bytes should already be canonical; if missing, supply a stable digest-only
    # placeholder rather than raising, to keep the call deterministic.
    if result_b is None:
        # Construct a deterministic placeholder to avoid None surprises in callers.
        h = hashlib.sha3_256()
        h.update(b"animica:capabilities:result:missing:v1")
        h.update(task_id)
        placeholder = h.digest()
        return {
            "task_id": bytes(task_id),
            "status": "READY",
            "ready_height": int(ready_h),
            "min_read_height": int(min_h),
            "consumed": bool(consumed),
            "result": placeholder,
        }

    return {
        "task_id": bytes(task_id),
        "status": "READY",
        "ready_height": int(ready_h),
        "min_read_height": int(min_h),
        "consumed": bool(consumed),
        "result": bytes(result_b),
    }


# Mark as deterministic for the registry
_read_result._deterministic = True  # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    registry.register(RESULT_READ, _read_result)


# Auto-register on import (idempotent)
try:  # pragma: no cover
    register(get_registry())
except Exception as _e:  # pragma: no cover
    log.debug("result_read provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = ["register"]
