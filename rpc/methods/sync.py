from __future__ import annotations

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


@method("sync.force", desc="Trigger a P2P sync round and return status")
async def sync_force(clear_cache: bool = False) -> dict[str, t.Any]:
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

    result: dict[str, t.Any] = {}
    if svc is not None and hasattr(svc, "force_sync_with_cache"):
        try:
            result = await svc.force_sync_with_cache(clear_cache=bool(clear_cache))
        except Exception as exc:  # pragma: no cover - defensive
            result = {"success": False, "error": str(exc)}
    elif svc is not None and hasattr(svc, "force_sync"):
        try:
            result = await svc.force_sync()
        except Exception as exc:  # pragma: no cover - defensive
            result = {"success": False, "error": str(exc)}
    elif core_svc is not None:
        result = await _core_force_sync(core_svc)
    else:
        result = {"success": False, "error": "force_sync not implemented"}

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
async def sync_get_status() -> dict[str, t.Any]:
    svc = _get_p2p_service()
    fatal_error = None
    try:
        ctx = deps.get_ctx()
        fatal_error = getattr(ctx, "p2p_start_error", None)
    except Exception:
        fatal_error = None
    if svc is not None and hasattr(svc, "sync_status_snapshot"):
        snap = svc.sync_status_snapshot()
        if hasattr(snap, "to_dict"):
            payload = snap.to_dict()
            if fatal_error and not payload.get("fatal_error"):
                payload["fatal_error"] = fatal_error
            return payload
        if isinstance(snap, dict):
            if fatal_error and not snap.get("fatal_error"):
                snap["fatal_error"] = fatal_error
            return snap
    return {
        "phase": "IDLE",
        "head_height": 0,
        "head_hash": None,
        "best_header_height": 0,
        "best_header_hash": None,
        "best_block_height": 0,
        "best_block_hash": None,
        "in_flight": 0,
        "in_flight_headers": 0,
        "in_flight_blocks": 0,
        "queued_blocks_count": 0,
        "last_progress_at": None,
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
        "active_peer_for_headers": None,
        "active_peer_for_blocks": None,
        "active_peers_for_headers": [],
        "active_peers_for_blocks": [],
        "eligible_peers_for_headers": [],
        "ineligible_peers_for_headers": {},
        "pending_header_batches": 0,
        "checkpoint_height": None,
        "checkpoint_hash": None,
        "checkpoint_mode_enabled": False,
        "checkpoint_validation": None,
        "last_checkpoint_action": None,
        "synchronized": False,
        "paused": False,
        "target_height": None,
        "peers_total": 0,
        "cache_size_bytes": 0,
        "cache_entries": 0,
        "peer_penalties": {},
        "cache_interval_ms": 0,
        "cache_age_ms": 0,
        "cache_hits": 0,
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
async def sync_status() -> dict[str, t.Any]:
    return await sync_get_status()


@method("node.syncStatus", desc="Return current sync status (compat alias)")
async def node_sync_status() -> dict[str, t.Any]:
    return await sync_get_status()


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
