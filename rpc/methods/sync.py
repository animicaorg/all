from __future__ import annotations

import asyncio
import threading
import time
import typing as t
from dataclasses import is_dataclass
from datetime import date, datetime

from rpc import deps
from rpc.methods import method

P2P_UNAVAILABLE_ERROR = "P2P disabled/unavailable"
SYNC_DUMP_LOCK_TIMEOUT_S = 0.05
SYNC_DUMP_RECURSION_LIMIT = 5


def _get_p2p_service() -> t.Any:
    try:
        import p2p

        if hasattr(p2p, "get_service"):
            svc = p2p.get_service()
            if svc is not None:
                return svc
    except Exception:
        pass

    try:
        ctx = deps.get_ctx()
        if hasattr(ctx, "p2p_service"):
            return ctx.p2p_service
    except Exception:
        return None
    return None


def _get_core_p2p_service() -> t.Any:
    try:
        ctx = deps.get_ctx()
        if hasattr(ctx, "core_p2p_service"):
            return ctx.core_p2p_service
    except Exception:
        return None
    return None


def _to_jsonable(value: t.Any, *, _depth: int = 0) -> t.Any:
    if _depth > SYNC_DUMP_RECURSION_LIMIT:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, bytearray):
        return bytes(value).hex()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return _to_jsonable(value.__dict__, _depth=_depth + 1)
    if isinstance(value, dict):
        return {
            str(key): _to_jsonable(val, _depth=_depth + 1)
            for key, val in list(value.items())
        }
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item, _depth=_depth + 1) for item in list(value)]
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        try:
            return _to_jsonable(value.to_dict(), _depth=_depth + 1)
        except Exception:
            return "<unserializable>"
    if hasattr(value, "__dict__"):
        return _to_jsonable(value.__dict__, _depth=_depth + 1)
    return str(value)


def _mark_unavailable(
    result: dict[str, t.Any],
    section: str,
    exc: Exception,
) -> None:
    result[section] = {"unavailable": True}
    result["errors"].append(
        {
            "section": section,
            "type": exc.__class__.__name__,
            "message": str(exc)[:200],
        }
    )


async def _try_async_lock(lock: t.Any, timeout_s: float) -> bool:
    try:
        return await asyncio.wait_for(lock.acquire(), timeout=timeout_s)
    except Exception:
        return False


@method("sync.dump", desc="Return a best-effort sync diagnostic snapshot")
async def sync_dump() -> dict[str, t.Any]:
    result: dict[str, t.Any] = {
        "rpc_url": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "head": {},
        "sync": {},
        "queues": {},
        "in_flight": {},
        "orphans": {},
        "peers": {},
        "cache": {},
        "errors": [],
    }

    ctx = None
    try:
        ctx = deps.get_ctx()
    except Exception as exc:
        _mark_unavailable(result, "head", exc)
        ctx = None

    try:
        head = ctx.get_head() if ctx else None
        if isinstance(head, dict):
            result["head"] = {
                "height": head.get("height"),
                "hash": head.get("hash"),
            }
        else:
            result["head"] = {"height": None, "hash": None}
    except Exception as exc:
        _mark_unavailable(result, "head", exc)

    svc = _get_p2p_service()
    if svc is None:
        result["sync"] = {"unavailable": True, "error": P2P_UNAVAILABLE_ERROR}
        result["errors"].append(
            {"section": "sync", "type": "Unavailable", "message": P2P_UNAVAILABLE_ERROR}
        )
        return result

    sync_lock = getattr(svc, "_sync_lock", None)
    peer_lock = getattr(svc, "_peer_lock", None)
    sync_locked = False
    peer_locked = False

    try:
        if sync_lock is not None:
            sync_locked = await _try_async_lock(sync_lock, SYNC_DUMP_LOCK_TIMEOUT_S)
            if not sync_locked:
                raise TimeoutError("sync lock busy")

        sync_phase = getattr(svc, "_sync_phase", None)
        sync_best_header = getattr(svc, "_sync_best_header", None)
        sync_best_header_height = (
            getattr(sync_best_header, "height", None) if sync_best_header else None
        )
        sync_best_header_hash = (
            sync_best_header.hash.hex() if getattr(sync_best_header, "hash", None) else None
        )
        sync_target_height = getattr(svc, "_sync_target_height", None)
        network_best_height = None
        if hasattr(svc, "_network_best_height"):
            try:
                network_best_height = svc._network_best_height()
            except Exception:
                network_best_height = None

        result["sync"] = _to_jsonable(
            {
                "phase": sync_phase,
                "best_header_height": sync_best_header_height,
                "best_header_hash": sync_best_header_hash,
                "target_height": sync_target_height,
                "network_best_height": network_best_height,
                "last_progress_at": getattr(svc, "_sync_last_progress_at", None),
                "last_header_error": getattr(svc, "_sync_last_header_error", None),
                "last_block_error": getattr(svc, "_sync_last_block_error", None),
                "last_block_error_peer": getattr(svc, "_sync_last_block_error_peer", None),
                "stall_reason": getattr(svc, "_sync_last_block_error", None)
                or getattr(svc, "_sync_last_header_error", None),
                "stall_elapsed_s": _to_jsonable(
                    max(0.0, time.time() - float(getattr(svc, "_sync_last_progress_at", 0.0) or 0.0))
                ),
                "recovery_attempts": getattr(svc, "_sync_recovery_attempts", None),
                "last_recovery_action": getattr(svc, "_sync_last_recovery_action", None),
                "last_checkpoint_action": getattr(svc, "_sync_last_checkpoint_action", None),
            }
        )

        queues_snapshot = {
            "pending_header_batches": len(getattr(svc, "_sync_header_queue", [])),
            "queued_blocks": len(getattr(svc, "_sync_block_queue", [])),
            "orphan_pool_size": len(getattr(svc, "_sync_block_buffer", {})),
        }
        result["queues"] = _to_jsonable(queues_snapshot)

        inflight_snapshot = {
            "in_flight_headers": int(getattr(svc, "_sync_inflight_headers", 0)),
            "in_flight_blocks": len(getattr(svc, "_sync_inflight_blocks", {})),
            "inflight_block_samples": [],
        }
        try:
            inflight_snapshot["inflight_block_samples"] = _to_jsonable(
                svc._inflight_block_samples(limit=10)
                if hasattr(svc, "_inflight_block_samples")
                else []
            )
        except Exception as exc:
            _mark_unavailable(result, "in_flight", exc)
        else:
            result["in_flight"] = _to_jsonable(inflight_snapshot)

        try:
            result["orphans"] = _to_jsonable(
                {
                    "samples": svc._orphan_block_samples(limit=10)
                    if hasattr(svc, "_orphan_block_samples")
                    else [],
                }
            )
        except Exception as exc:
            _mark_unavailable(result, "orphans", exc)

    except Exception as exc:
        _mark_unavailable(result, "sync", exc)
        _mark_unavailable(result, "queues", exc)
        _mark_unavailable(result, "in_flight", exc)
        _mark_unavailable(result, "orphans", exc)
    finally:
        if sync_locked and sync_lock is not None:
            sync_lock.release()

    try:
        if peer_lock is not None:
            peer_locked = await _try_async_lock(peer_lock, SYNC_DUMP_LOCK_TIMEOUT_S)
            if not peer_locked:
                raise TimeoutError("peer lock busy")
        peers = []
        for peer in list(getattr(svc, "_peers", {}).values()):
            hello = getattr(peer, "hello", {}) or {}
            peers.append(
                {
                    "remote": getattr(peer, "remote", None),
                    "peer_id": getattr(peer, "peer_id", None),
                    "direction": getattr(peer, "direction", None),
                    "handshake_done": bool(getattr(peer, "hello_done", threading.Event()).is_set()),
                    "ready_for_sync": getattr(peer, "ready_for_sync", None),
                    "version": hello.get("version"),
                    "agent": hello.get("agent"),
                    "chain_id": hello.get("chain_id"),
                    "head_height": hello.get("head_height"),
                    "head_hash": bytes(hello.get("head_hash") or b"").hex()
                    if hello.get("head_hash")
                    else None,
                    "last_msg_at": getattr(peer, "last_msg_at", None),
                    "last_progress_at": getattr(peer, "last_progress_at", None),
                }
            )
        scores = []
        if hasattr(svc, "_peer_score_snapshot"):
            try:
                scores = svc._peer_score_snapshot()
            except Exception:
                scores = []
        result["peers"] = _to_jsonable(
            {
                "connected": peers,
                "scores": scores,
                "timeouts_by_peer": dict(getattr(svc, "_sync_timeouts_by_peer", {})),
                "retries_by_peer": dict(getattr(svc, "_sync_retries_by_peer", {})),
            }
        )
    except Exception as exc:
        _mark_unavailable(result, "peers", exc)
    finally:
        if peer_locked and peer_lock is not None:
            peer_lock.release()

    try:
        cache = {
            "sync_status_cache_at": getattr(svc, "_sync_status_cache_at", None),
            "sync_status_cache_hits": getattr(svc, "_sync_status_cache_hits", None),
            "sync_status_cache_refreshes": getattr(svc, "_sync_status_cache_refreshes", None),
            "sync_status_cache_interval_s": getattr(svc, "_sync_status_cache_interval", None),
        }
        result["cache"] = _to_jsonable(cache)
    except Exception as exc:
        _mark_unavailable(result, "cache", exc)

    return _to_jsonable(result)


async def _core_force_sync(core_svc: t.Any) -> dict[str, t.Any]:
    try:
        connman = getattr(core_svc, "connman", None)
        net_processing = getattr(core_svc, "net_processing", None)
        if connman is None or net_processing is None:
            return {"success": False, "error": "core P2P service not ready"}
        peers = connman.peers()
        if not peers:
            return {"success": False, "error": "no core peers connected", "peerCount": 0}
        msg = net_processing.sync.build_getheaders()
        payload = msg.serialize()
        for peer in peers.values():
            await connman._send(peer, "getheaders", payload)
        return {"success": True, "started": True, "peerCount": len(peers)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _run_in_background(coro: t.Awaitable[t.Any]) -> None:
    try:
        await coro
    except Exception:
        return


@method("sync.force", desc="Trigger a P2P sync round and return status")
async def sync_force(
    clear_cache: bool = False,
    boost_seconds: int | None = None,
    boost_tick_ms: int | None = None,
) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    core_svc = _get_core_p2p_service()
    head_height = None
    try:
        head = deps.ensure_started().get_head()
        head_height = head.get("height") if isinstance(head, dict) else None
    except Exception:
        head_height = None

    if svc is None and core_svc is None:
        return {
            "success": False,
            "error": P2P_UNAVAILABLE_ERROR,
            "height": head_height,
            "peerCount": 0,
        }

    queued = False
    if svc is not None:
        if hasattr(svc, "enable_sync"):
            try:
                svc.enable_sync(True)
            except Exception:
                pass
        if hasattr(svc, "_sync_wakeup"):
            try:
                svc._sync_wakeup.set()
            except Exception:
                pass
        if boost_seconds and hasattr(svc, "boost_sync"):
            try:
                svc.boost_sync(duration_s=float(boost_seconds), tick_ms=boost_tick_ms)
            except Exception:
                pass
        if hasattr(svc, "force_sync_with_cache"):
            asyncio.create_task(
                _run_in_background(
                    svc.force_sync_with_cache(clear_cache=bool(clear_cache))
                )
            )
            queued = True
        elif hasattr(svc, "force_sync"):
            asyncio.create_task(_run_in_background(svc.force_sync()))
            queued = True
    elif core_svc is not None:
        asyncio.create_task(_run_in_background(_core_force_sync(core_svc)))
        queued = True

    if not queued:
        return {"success": False, "error": "force_sync not implemented"}

    result: dict[str, t.Any] = {"success": True, "queued": True, "started": True}

    try:
        if svc is not None:
            peer_count = svc.peer_count() if hasattr(svc, "peer_count") else len(getattr(svc, "peers", {}))
        else:
            peer_count = len(getattr(core_svc.connman, "peers", lambda: {})())
    except Exception:
        peer_count = 0

    result.setdefault("peerCount", peer_count)
    result.setdefault("success", bool(result.get("started")))
    if head_height is not None:
        result.setdefault("height", head_height)
    return result


@method("sync.trigger", desc="Trigger a P2P sync round (canonical alias)")
async def sync_trigger() -> dict[str, t.Any]:
    return await sync_force()


@method("sync.start", desc="Trigger a P2P sync round (legacy alias)")
async def sync_start() -> dict[str, t.Any]:
    return await sync_force()


@method("sync.getStatus", desc="Return current sync status")
async def sync_get_status(opts: dict[str, t.Any] | str | None = None) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    fatal_error = None
    chain_head_height = None
    chain_head_hash = None
    try:
        ctx = deps.get_ctx()
        fatal_error = getattr(ctx, "p2p_start_error", None)
        head = ctx.get_head()
        if isinstance(head, dict):
            chain_head_height = head.get("height")
            chain_head_hash = head.get("hash")
    except Exception:
        fatal_error = None
    refresh = False
    if isinstance(opts, dict):
        source = opts.get("source") or opts.get("cache_source")
        refresh = bool(opts.get("refresh")) or str(source).lower() == "refresh"
    elif isinstance(opts, str):
        refresh = opts.lower() == "refresh"
    if svc is not None and hasattr(svc, "sync_status_snapshot"):
        try:
            snap = svc.sync_status_snapshot(refresh=refresh)
        except TypeError:
            snap = svc.sync_status_snapshot()
        if hasattr(snap, "to_dict"):
            payload = snap.to_dict()
            if fatal_error and not payload.get("fatal_error"):
                payload["fatal_error"] = fatal_error
            if chain_head_height is not None:
                payload.setdefault("chain_head_height", chain_head_height)
                payload.setdefault("chain_head_hash", chain_head_hash)
                if not payload.get("head_height"):
                    payload["head_height"] = chain_head_height
                    payload["head_hash"] = chain_head_hash
            if "p2p_init_failed" not in payload:
                payload["p2p_init_failed"] = bool(fatal_error)
                payload["p2p_init_error"] = fatal_error
            return payload
        if isinstance(snap, dict):
            if fatal_error and not snap.get("fatal_error"):
                snap["fatal_error"] = fatal_error
            if chain_head_height is not None:
                snap.setdefault("chain_head_height", chain_head_height)
                snap.setdefault("chain_head_hash", chain_head_hash)
                if not snap.get("head_height"):
                    snap["head_height"] = chain_head_height
                    snap["head_hash"] = chain_head_hash
            if "p2p_init_failed" not in snap:
                snap["p2p_init_failed"] = bool(fatal_error)
                snap["p2p_init_error"] = fatal_error
            return snap
    head_height = int(chain_head_height or 0)
    return {
        "phase": "IDLE",
        "head_height": head_height,
        "head_hash": chain_head_hash,
        "best_header_height": 0,
        "best_header_hash": None,
        "best_block_height": head_height,
        "best_block_hash": chain_head_hash,
        "network_best_height": None,
        "in_flight": 0,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
        "last_progress_at": None,
        "last_head_height": 0,
        "last_head_hash": None,
        "last_header_height": 0,
        "last_block_fetch_height": 0,
        "last_header_progress_at": None,
        "last_block_progress_at": None,
        "last_header_at": None,
        "last_block_at": None,
        "last_header_request_at": None,
        "last_header_response_at": None,
        "last_header_response_count": 0,
        "last_block_request_at": None,
        "last_block_response_at": None,
        "last_header_request_peer": None,
        "last_header_response_peer": None,
        "last_header_error": None,
        "last_header_error_at": None,
        "last_block_error": None,
        "fatal_error": fatal_error,
        "p2p_init_failed": bool(fatal_error),
        "p2p_init_error": fatal_error,
        "active_peer_for_headers": None,
        "active_peer_for_blocks": None,
        "active_peers_for_headers": [],
        "active_peers_for_blocks": [],
        "eligible_peers_for_headers": [],
        "ineligible_peers_for_headers": {},
        "eligible_peers_for_blocks": [],
        "ineligible_peers_for_blocks": {},
        "chain_head_height": chain_head_height,
        "chain_head_hash": chain_head_hash,
        "pending_header_batches": 0,
        "checkpoint_height": None,
        "checkpoint_hash": None,
        "checkpoint_mode_enabled": False,
        "checkpoint_validation": None,
        "last_checkpoint_action": None,
        "synchronized": False,
        "paused": False,
        "sync_enabled": False,
        "target_height": None,
        "peers_total": 0,
        "cache_size_bytes": 0,
        "cache_entries": 0,
        "peer_penalties": {},
        "last_block_error_peer": None,
        "block_error_summary": {},
        "next_block_needed_height": None,
        "next_block_needed_hash": None,
        "orphan_pool_size": 0,
        "orphan_cascade_successes": 0,
        "orphan_seen_count_entries": 0,
        "inflight_block_samples": [],
        "orphan_block_samples": [],
        "peer_scores": [],
        "retries_by_peer": {},
        "timeouts_by_peer": {},
        "blocks_failed": 0,
        "orphan_added": 0,
        "orphan_resolved": 0,
        "orphan_evicted": 0,
        "stall_recovery_actions": {},
        "stall_timeout_s": 0,
        "stall_reason": None,
        "stall_elapsed_s": 0,
        "cache_interval_ms": 0,
        "cache_age_ms": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_refreshes": 0,
        "cache_last_refresh_at": None,
        "cache_source": "refresh",
    }


@method("sync.pause", desc="Pause background sync")
async def sync_pause() -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is not None and hasattr(svc, "pause_sync"):
        try:
            return t.cast(dict[str, t.Any], svc.pause_sync())
        except Exception as exc:  # pragma: no cover - defensive
            return {"paused": False, "error": str(exc)}
    return {"paused": False, "error": P2P_UNAVAILABLE_ERROR}


@method("sync.resume", desc="Resume background sync")
async def sync_resume() -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is not None and hasattr(svc, "resume_sync"):
        try:
            return t.cast(dict[str, t.Any], svc.resume_sync())
        except Exception as exc:  # pragma: no cover - defensive
            return {"paused": True, "error": str(exc)}
    return {"paused": True, "error": P2P_UNAVAILABLE_ERROR}


@method("sync.setTarget", desc="Set sync target height")
async def sync_set_target(height: int | None = None) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is not None and hasattr(svc, "set_sync_target"):
        try:
            return t.cast(dict[str, t.Any], svc.set_sync_target(height))
        except Exception as exc:  # pragma: no cover - defensive
            return {"target_height": None, "error": str(exc)}
    return {"target_height": None, "error": P2P_UNAVAILABLE_ERROR}


@method("sync.status", desc="Return current sync status (alias)")
async def sync_status(opts: dict[str, t.Any] | str | None = None) -> dict[str, t.Any]:
    return await sync_get_status(opts)


@method("node.syncStatus", desc="Return current sync status (compat alias)")
async def node_sync_status(opts: dict[str, t.Any] | str | None = None) -> dict[str, t.Any]:
    return await sync_get_status(opts)


__all__ = [
    "sync_dump",
    "sync_force",
    "sync_trigger",
    "sync_start",
    "sync_get_status",
    "sync_status",
    "node_sync_status",
    "sync_pause",
    "sync_resume",
    "sync_set_target",
]
