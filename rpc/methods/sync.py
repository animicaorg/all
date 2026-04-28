from __future__ import annotations

import asyncio
import typing as t

from rpc import deps
from rpc.methods import method

P2P_UNAVAILABLE_ERROR = "P2P disabled/unavailable"


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
    force_result: dict[str, t.Any] | None = None
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
            try:
                force_result = await asyncio.wait_for(
                    svc.force_sync_with_cache(clear_cache=bool(clear_cache)),
                    timeout=5.0,
                )
                queued = True
            except asyncio.TimeoutError:
                asyncio.create_task(
                    _run_in_background(
                        svc.force_sync_with_cache(clear_cache=bool(clear_cache))
                    )
                )
                queued = True
                force_result = {
                    "started": False,
                    "queued": True,
                    "blockingReason": "force_sync_timeout",
                }
        elif hasattr(svc, "force_sync"):
            try:
                force_result = await asyncio.wait_for(svc.force_sync(), timeout=5.0)
                queued = True
            except asyncio.TimeoutError:
                asyncio.create_task(_run_in_background(svc.force_sync()))
                queued = True
                force_result = {
                    "started": False,
                    "queued": True,
                    "blockingReason": "force_sync_timeout",
                }
    elif core_svc is not None:
        asyncio.create_task(_run_in_background(_core_force_sync(core_svc)))
        queued = True

    if not queued:
        return {"success": False, "error": "force_sync not implemented"}

    result: dict[str, t.Any] = {"success": True, "queued": True, "started": True}
    if isinstance(force_result, dict):
        result.update(force_result)

    try:
        if svc is not None:
            peer_count = svc.peer_count() if hasattr(svc, "peer_count") else len(getattr(svc, "peers", {}))
        else:
            peer_count = len(getattr(core_svc.connman, "peers", lambda: {})())
    except Exception:
        peer_count = 0

    result.setdefault("peerCount", peer_count)
    started = bool(result.get("started"))
    result["success"] = started
    if not started and "error" not in result:
        result["error"] = "force-sync-not-started"
    if not started and "blockingReason" not in result:
        blocking = result.get("phase")
        if blocking in {"IDLE", "TARGET_REACHED", "SYNCED"}:
            result["blockingReason"] = f"sync_phase:{blocking.lower()}"
        else:
            result["blockingReason"] = "no-work-scheduled"
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


@method("sync.forceCanonical", desc="Force canonical chain selection and reorg")
async def sync_force_canonical(force: bool = False) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is None or not hasattr(svc, "force_canonical_reorg"):
        return {"success": False, "error": P2P_UNAVAILABLE_ERROR}
    try:
        result = svc.force_canonical_reorg(force=bool(force))
        if isinstance(result, dict):
            return result
        return {"success": bool(result)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
