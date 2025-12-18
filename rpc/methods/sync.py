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


@method("sync.force", desc="Trigger a P2P sync round and return status")
async def sync_force() -> dict[str, t.Any]:
    svc = _get_p2p_service()
    head_height = None
    try:
        head = deps.ensure_started().get_head()
        head_height = head.get("height") if isinstance(head, dict) else None
    except Exception:
        head_height = None

    if svc is None:
        return {
            "success": False,
            "error": "P2P service not available",
            "height": head_height,
            "peerCount": 0,
        }

    result: dict[str, t.Any] = {}
    if hasattr(svc, "force_sync"):
        try:
            result = await svc.force_sync()
        except Exception as exc:  # pragma: no cover - defensive
            result = {"success": False, "error": str(exc)}
    else:
        result = {"success": False, "error": "force_sync not implemented"}

    try:
        peer_count = svc.peer_count() if hasattr(svc, "peer_count") else len(getattr(svc, "peers", {}))
    except Exception:
        peer_count = 0

    result.setdefault("peerCount", peer_count)
    result.setdefault("success", bool(result.get("started")))
    if head_height is not None:
        result.setdefault("height", head_height)
    return result


__all__ = ["sync_force"]
