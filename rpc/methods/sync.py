from __future__ import annotations

import asyncio
import os
import typing as t

from rpc import deps
from rpc.methods import method

P2P_UNAVAILABLE_ERROR = "P2P disabled/unavailable"
DEFAULT_TIP_FRESHNESS_SEC = 60.0
DEFAULT_ALLOWED_LAG = 0


def _compute_sync_status(
    *,
    local_height: int,
    best_remote_height: int | None,
    allowed_lag: int,
) -> tuple[str, int | None, str | None]:
    if best_remote_height is None:
        return "UNKNOWN_REMOTE", None, "no peer tip information"
    behind_by = max(0, int(best_remote_height) - int(local_height))
    if behind_by > int(allowed_lag):
        return "SYNCING", behind_by, None
    return "SYNCHRONIZED", behind_by, None


def _allowed_lag() -> int:
    try:
        return int(os.environ.get("ANIMICA_SYNC_ALLOWED_LAG", str(DEFAULT_ALLOWED_LAG)) or DEFAULT_ALLOWED_LAG)
    except Exception:
        return DEFAULT_ALLOWED_LAG


def _tip_freshness() -> float:
    try:
        return float(
            os.environ.get("ANIMICA_SYNC_TIP_FRESHNESS_SEC", str(DEFAULT_TIP_FRESHNESS_SEC))
            or DEFAULT_TIP_FRESHNESS_SEC
        )
    except Exception:
        return DEFAULT_TIP_FRESHNESS_SEC


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
    chain_id = None
    try:
        ctx = deps.get_ctx()
        fatal_error = getattr(ctx, "p2p_start_error", None)
        head = ctx.get_head()
        if isinstance(head, dict):
            chain_head_height = head.get("height")
            chain_head_hash = head.get("hash")
        chain_id = deps.get_chain_id()
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
        best_remote = None
        if hasattr(svc, "best_remote_tip"):
            try:
                best_remote = svc.best_remote_tip(tip_freshness_s=_tip_freshness())
            except Exception:
                best_remote = None
        local_height = int(chain_head_height or 0)
        local_hash = chain_head_hash
        if local_height == 0 and hasattr(snap, "head_height"):
            try:
                local_height = int(getattr(snap, "head_height", 0) or 0)
            except Exception:
                local_height = int(chain_head_height or 0)
        if not local_hash and hasattr(snap, "head_hash"):
            local_hash = getattr(snap, "head_hash", None)
        allowed_lag = _allowed_lag()
        best_remote_height = (
            int(best_remote.get("height")) if isinstance(best_remote, dict) and best_remote.get("height") is not None else None
        )
        status, behind_by, reason = _compute_sync_status(
            local_height=local_height,
            best_remote_height=best_remote_height,
            allowed_lag=allowed_lag,
        )
        peers_summary = {"total": 0, "in": 0, "out": 0}
        if hasattr(svc, "p2p_status_snapshot"):
            try:
                p2p_snap = svc.p2p_status_snapshot()
                peers_summary = {
                    "total": int(getattr(p2p_snap, "peers_total", 0) or 0),
                    "in": int(getattr(p2p_snap, "peers_inbound", 0) or 0),
                    "out": int(getattr(p2p_snap, "peers_outbound", 0) or 0),
                }
            except Exception:
                peers_summary = {"total": 0, "in": 0, "out": 0}
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
            payload["chain_id"] = chain_id
            payload["local"] = {"height": local_height, "hash": local_hash}
            payload["best_remote"] = best_remote
            payload["behind_by"] = behind_by
            payload["status"] = status
            payload["reason"] = reason
            payload["peers"] = peers_summary
            payload["allowed_lag"] = allowed_lag
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
            snap["chain_id"] = chain_id
            snap["local"] = {"height": local_height, "hash": local_hash}
            snap["best_remote"] = best_remote
            snap["behind_by"] = behind_by
            snap["status"] = status
            snap["reason"] = reason
            snap["peers"] = peers_summary
            snap["allowed_lag"] = allowed_lag
            return snap
    head_height = int(chain_head_height or 0)
    status, behind_by, reason = _compute_sync_status(
        local_height=head_height,
        best_remote_height=None,
        allowed_lag=_allowed_lag(),
    )
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
        "chain_id": chain_id,
        "local": {"height": head_height, "hash": chain_head_hash},
        "best_remote": None,
        "behind_by": behind_by,
        "status": status,
        "reason": reason,
        "peers": {"total": 0, "in": 0, "out": 0},
        "allowed_lag": _allowed_lag(),
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
