from __future__ import annotations

import logging
import time
import typing as t

from rpc import deps
from rpc.methods import method

log = logging.getLogger("animica.rpc.node")


def _safe_peer_counts(p2p_status: dict[str, t.Any]) -> dict[str, int]:
    """
    Safely coerce peer counts to integers with logging for type mismatches.
    """
    def _coerce(key: str, value: t.Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        # Log unexpected type for debugging
        if value is not None:
            log.warning(
                "Peer count type mismatch",
                extra={
                    "key": key,
                    "value": value,
                    "type": type(value).__name__,
                    "fallback": 0,
                },
            )
        return 0

    return {
        "total": _coerce("peers_total", p2p_status.get("peers_total")),
        "inbound": _coerce("peers_inbound", p2p_status.get("peers_inbound")),
        "outbound": _coerce("peers_outbound", p2p_status.get("peers_outbound")),
        # NEW: Include connected peer counts for mining/sync gating
        "connected": _coerce("peers_connected", p2p_status.get("peers_connected")),
        "handshaking": _coerce("peers_handshaking", p2p_status.get("peers_handshaking")),
    }


def _head_summary(head: dict[str, t.Any]) -> dict[str, t.Any]:
    return {
        "height": head.get("height"),
        "hash": head.get("hash"),
        "chainId": head.get("chainId") or head.get("chain_id"),
    }


@method("node.ping", desc="Lightweight liveness check")
def node_ping() -> dict[str, t.Any]:
    return {"ok": True, "timestamp": time.time()}


@method("node.getStatus", desc="Return a live snapshot of chain, P2P, and sync status")
async def node_get_status(hashrate_window: int | None = None) -> dict[str, t.Any]:
    from rpc.methods import chain as chain_methods

    head = chain_methods.chain_get_head()
    init_error = None
    try:
        ctx = deps.get_ctx()
        init_error = ctx.init_error
    except Exception:
        init_error = None
    try:
        network_hashrate = chain_methods.chain_get_network_hashrate(
            window_blocks=hashrate_window
        )
    except Exception as exc:
        network_hashrate = {
            "hashrate_hsps": None,
            "window_blocks": int(hashrate_window or 120),
            "window_seconds": None,
            "height_start": None,
            "height_end": head.get("height"),
            "method": "theta_micro_expected_trials",
            "unknown_reason": f"error: {exc}",
        }
    p2p_status: dict[str, t.Any]
    sync_status: dict[str, t.Any]

    try:
        from rpc.methods import p2p as p2p_methods

        p2p_status = await p2p_methods.get_status()
    except Exception:
        p2p_status = {
            "p2p_running": False,
            "listen_addrs": [],
            "peers_total": 0,
            "peers_inbound": 0,
            "peers_outbound": 0,
            "bootstrap_attempts_last_5m": 0,
        }

    try:
        from rpc.methods import sync as sync_methods

        sync_status = await sync_methods.sync_get_status()
    except Exception:
        sync_status = {
            "phase": "IDLE",
            "head_height": 0,
            "head_hash": None,
            "best_header_height": 0,
            "best_header_hash": None,
            "best_block_height": 0,
            "best_block_hash": None,
        }

    return {
        "rpc_reachable": True,
        "init_error": init_error,
        "chain": {
            "head": head,
            "summary": _head_summary(head),
            "chain_id": head.get("chainId") or head.get("chain_id") or deps.get_chain_id(),
            "identity": deps.get_chain_identity(),
        },
        "network_hashrate": network_hashrate,
        "p2p": {
            **p2p_status,
            "peer_counts": _safe_peer_counts(p2p_status),
        },
        "sync": sync_status,
    }


@method("node.syncTrigger", desc="Trigger sync and report queued status")
async def node_sync_trigger() -> dict[str, t.Any]:
    try:
        from rpc.methods import sync as sync_methods
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "queued": False, "error": f"sync methods unavailable: {exc}"}

    trigger = await sync_methods.sync_force()
    sync_status = await sync_methods.sync_get_status()

    peer_count = 0
    try:
        from rpc.methods import p2p as p2p_methods

        p2p_status = await p2p_methods.get_status()
        peer_count = _safe_peer_counts(p2p_status).get("total", 0)
    except Exception:
        peer_count = 0

    ok = bool(trigger.get("success") or trigger.get("started") or trigger.get("ok"))
    return {
        "ok": ok,
        "queued": ok,
        "peerCount": peer_count,
        "phase": sync_status.get("phase") or sync_status.get("state"),
        "trigger": trigger,
    }


__all__ = ["node_get_status", "node_ping", "node_sync_trigger"]
