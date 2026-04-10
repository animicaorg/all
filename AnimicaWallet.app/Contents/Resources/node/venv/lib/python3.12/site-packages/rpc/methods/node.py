from __future__ import annotations

import time
import typing as t

from rpc import deps
from rpc.methods import method

# Node startup time for uptime_seconds calculation
_NODE_START_TIME = time.monotonic()


def _safe_peer_counts(p2p_status: dict[str, t.Any]) -> dict[str, int]:
    def _coerce(value: t.Any) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0

    return {
        "total": _coerce(p2p_status.get("peers_total")),
        "inbound": _coerce(p2p_status.get("peers_inbound")),
        "outbound": _coerce(p2p_status.get("peers_outbound")),
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

    mempool_status: dict[str, t.Any] = {"enabled": False, "fallback_enabled": False}
    try:
        mempool = getattr(ctx, "mempool", None) if "ctx" in locals() else None
        if mempool is not None and hasattr(mempool, "persistence_status"):
            persistence = mempool.persistence_status()
            mempool_status = {
                "enabled": True,
                "fallback_enabled": bool(persistence.get("fallback_active")),
                "persistence": persistence,
            }
    except Exception:
        mempool_status = {"enabled": False, "fallback_enabled": False}

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
        "mempool": mempool_status,
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


__all__ = ["node_get_status", "node_ping", "node_sync_trigger", "node_health", "node_capabilities"]


def _get_da_policy() -> dict[str, t.Any]:
    """Return DA policy/status summary for capabilities endpoint."""
    try:
        from rpc.methods.da import _get_store
        store = _get_store()
        cfg = store.config
        return {
            "enabled": bool(cfg.enabled),
            "allow_remote_put": bool(cfg.allow_remote_put),
            "allow_remote_get": bool(cfg.allow_remote_get),
            "effective_dir": store.root_dir,
            "policy_reason_if_disabled": (
                None if cfg.enabled else "DA store is not enabled (da.configure to enable)"
            ),
        }
    except Exception:
        return {
            "enabled": False,
            "allow_remote_put": False,
            "allow_remote_get": False,
            "effective_dir": None,
            "policy_reason_if_disabled": "DA not available or not configured",
        }


def _get_aicf_available() -> bool:
    """Check if AICF methods are available."""
    try:
        from rpc.methods import get_methods
        return "aicf.status" in get_methods()
    except Exception:
        return False


def _get_mempool_size() -> t.Optional[int]:
    """Get current mempool size if available."""
    try:
        ctx = deps.get_ctx()
        mempool = getattr(ctx, "mempool", None)
        if mempool is not None and hasattr(mempool, "size"):
            return int(mempool.size())
        if mempool is not None and hasattr(mempool, "__len__"):
            return len(mempool)
    except Exception:
        pass
    return None


@method(
    "node.health",
    aliases=("system.health", "node_health"),
    desc="Lightweight health check — returns compact node health snapshot suitable for polling",
)
def node_health() -> dict[str, t.Any]:
    """
    Lightweight health check.  Returns quickly without expensive scans.

    Schema:
      ok, rpc_ready, chain_ready, syncing, head_height, head_hash,
      peers_connected, mempool_size, uptime_seconds, version,
      chain_id, network_name, recommended_poll_ms, server_time
    """
    import rpc.version as rpc_ver

    head_height: t.Optional[int] = None
    head_hash: t.Optional[str] = None
    chain_ready = False
    chain_id: t.Optional[int] = None
    syncing = False

    try:
        head = deps.get_head()
        head_height = head.get("height")
        head_hash = head.get("hash")
        chain_id = head.get("chainId") or head.get("chain_id")
        chain_ready = head_height is not None
    except Exception:
        pass

    if chain_id is None:
        try:
            chain_id = deps.get_chain_id()
        except Exception:
            pass

    peers_connected = 0
    try:
        ctx = deps.get_ctx()
        p2p = getattr(ctx, "p2p_service", None)
        if p2p is not None and hasattr(p2p, "peer_count"):
            peers_connected = int(p2p.peer_count())
    except Exception:
        pass

    mempool_size = _get_mempool_size()

    uptime_seconds = int(time.monotonic() - _NODE_START_TIME)

    # Infer network name
    network_name: t.Optional[str] = None
    _cid_to_net = {1: "mainnet", 2: "testnet", 1337: "devnet"}
    if chain_id is not None:
        network_name = _cid_to_net.get(chain_id, f"custom-{chain_id}")

    version = getattr(rpc_ver, "__version__", "dev")
    ok = chain_ready  # basic: chain data is accessible

    return {
        "ok": ok,
        "rpc_ready": True,
        "chain_ready": chain_ready,
        "syncing": syncing,
        "head_height": head_height,
        "head_hash": head_hash,
        "peers_connected": peers_connected,
        "mempool_size": mempool_size,
        "uptime_seconds": uptime_seconds,
        "version": version,
        "chain_id": chain_id,
        "network_name": network_name,
        "recommended_poll_ms": 5000,
        "server_time": int(time.time() * 1000),
    }


@method(
    "node.capabilities",
    aliases=("system.capabilities", "node_capabilities"),
    desc="Return Studio-facing feature flags, DA policy, AICF availability, and readiness stage",
)
def node_capabilities() -> dict[str, t.Any]:
    """
    Return node capability summary for Studio integration.

    Schema:
      version, chain_id, methods_hash,
      supports: { da, aicf, tx_receipts, tx_status, ws_subscriptions,
                  address_balance_query, deploy_simulation },
      da: { enabled, allow_remote_put, allow_remote_get, effective_dir, policy_reason_if_disabled },
      aicf: { enabled, methods },
      param_forms: { object_supported_methods, positional_supported_methods },
      readiness: { stage, not_ready_reasons },
      polling_guidance: { tx_receipt_poll_ms, tx_status_poll_ms, da_status_poll_ms }
    """
    import hashlib, json
    import rpc.version as rpc_ver

    # Gather all method names for hash
    try:
        from rpc.methods import get_methods
        method_names = sorted(get_methods().keys())
    except Exception:
        method_names = []

    methods_hash = hashlib.sha256(json.dumps(method_names).encode()).hexdigest()[:16]

    chain_id: t.Optional[int] = None
    try:
        chain_id = deps.get_chain_id()
    except Exception:
        pass

    da_policy = _get_da_policy()
    aicf_available = _get_aicf_available()

    # Readiness stage detection
    chain_ready = False
    try:
        head = deps.get_head()
        chain_ready = head.get("height") is not None
    except Exception:
        pass

    readiness_stage = "fully_ready" if chain_ready else "rpc_listening"
    not_ready_reasons: t.List[str] = []
    if not chain_ready:
        not_ready_reasons.append("chain not yet initialized — no genesis block")

    # Methods that support object params
    object_supported = [
        "aicf.creditsByAddress", "aicf.credits_by_address", "aicf_creditsByAddress",
        "aicf.getClaimable", "aicf.claim", "aicf.buildClaimTx",
        "tx.getTransactionByHash", "tx.getTransactionReceipt", "tx.getStatus",
        "da.configure", "da.put", "da.putBlob", "da.getBlob", "da.getIngestDir", "da.getDataRoot", "da.statPath", "da.getHostMountHints", "da.ingestLocal",
        "state.getBalance", "state.getNonce", "state.getAccount",
    ]
    positional_supported = [
        "chain.getChainId", "chain.getHead",
        "tx.getTransactionByHash", "tx.getTransactionReceipt",
        "state.getBalance", "state.getNonce",
        "da.getBlob",
    ]

    return {
        "version": getattr(rpc_ver, "__version__", "dev"),
        "chain_id": chain_id,
        "methods_hash": methods_hash,
        "supports": {
            "da": da_policy["enabled"],
            "aicf": aicf_available,
            "tx_receipts": True,
            "tx_status": True,
            "ws_subscriptions": False,
            "address_balance_query": True,
            "deploy_simulation": False,
        },
        "da": da_policy,
        "aicf": {
            "enabled": aicf_available,
            "methods": [
                "aicf.creditsByAddress",
                "aicf.getClaimable",
                "aicf.claim",
                "aicf.buildClaimTx",
                "aicf.status",
            ] if aicf_available else [],
        },
        "param_forms": {
            "object_supported_methods": object_supported,
            "positional_supported_methods": positional_supported,
        },
        "readiness": {
            "stage": readiness_stage,
            "not_ready_reasons": not_ready_reasons,
        },
        "polling_guidance": {
            "tx_receipt_poll_ms": 2000,
            "tx_status_poll_ms": 1000,
            "da_status_poll_ms": 5000,
        },
    }
