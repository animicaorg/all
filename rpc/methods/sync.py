from __future__ import annotations

import typing as t

from rpc import deps
from rpc.methods import method


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
async def sync_force() -> dict[str, t.Any]:
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
            "error": "P2P service not available",
            "height": head_height,
            "peerCount": 0,
        }

    result: dict[str, t.Any] = {}
    if svc is not None and hasattr(svc, "force_sync"):
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


__all__ = ["sync_force"]
