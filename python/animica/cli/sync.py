"""
Blockchain sync management CLI for Animica.

Provides commands to monitor and control blockchain synchronization,
including viewing sync status, forcing resyncs, and diagnosing sync issues.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
import typer
from animica.config import load_network_config
from animica.cli.peer import (
    _generate_peer_id,
    _rpc_call_with_error,
    _rpc_error_message,
    _is_method_not_found_error,
    _is_unauthorized_error,
    _probe_rpc_for_peer_injection,
    _rpc_operation_succeeded,
    _rpc_headers,
    _fetch_peer_status,
    _print_peer_status,
    _write_peer_to_sqlite,
    _write_peer_to_store,
)
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.seeds import get_seed_nodes
from animica.cli.rpc_utils import candidate_rpc_urls, is_local_rpc_url, is_method_not_found
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage blockchain synchronization.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
BOOTSTRAP_RPC_ENV = "ANIMICA_BOOTSTRAP_RPC_URL"


async def rpc_call(
    method: str,
    params: Optional[List[Any] | Dict[str, Any]] = None,
    *,
    rpc_url: str,
    timeout: Optional[float] = None,
) -> Any:
    """Make a JSON-RPC call to the node."""
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(rpc_url, json=payload, headers=_rpc_headers())
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            body = response.text.strip()
            snippet = body[:200] + ("..." if len(body) > 200 else "")
            detail = snippet if snippet else "<empty response>"
            raise RuntimeError(
                f"RPC returned non-JSON response (status {response.status_code}): {detail}"
            ) from exc
    if "error" in data:
        error_info = data["error"]
        if isinstance(error_info, dict):
            error_msg = error_info.get("message", str(error_info))
        else:
            error_msg = str(error_info)
        raise RuntimeError(error_msg)
    return data.get("result")


def _resolve_sync_endpoints(
    rpc_url: Optional[str],
    bootstrap_rpc: Optional[str],
    *,
    allow_bootstrap_rpc: bool = False,
) -> tuple[str, Optional[str]]:
    """Resolve target and bootstrap RPC endpoints."""
    net_cfg = load_network_config()
    target = rpc_url if rpc_url and rpc_url.strip() else None
    if not target:
        env_url = os.environ.get(RPC_ENV)
        target = env_url.strip() if env_url and env_url.strip() else None
    target = target or net_cfg.rpc_url
    bootstrap = bootstrap_rpc if bootstrap_rpc and bootstrap_rpc.strip() else None
    if not bootstrap:
        env_bootstrap = os.environ.get(BOOTSTRAP_RPC_ENV)
        bootstrap = env_bootstrap.strip() if env_bootstrap and env_bootstrap.strip() else None
    bootstrap = bootstrap or net_cfg.bootstrap_url
    if not allow_bootstrap_rpc:
        bootstrap = None
    bootstrap = bootstrap.strip() if bootstrap else None
    return target, bootstrap


def _sync_state_path(net_cfg) -> Path:
    data_dir = Path(os.path.expanduser(net_cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "sync" / "progress.json"


def _load_cached_bootstrap_head(
    net_cfg,
    bootstrap_url: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not bootstrap_url:
        return None
    state_path = _sync_state_path(net_cfg)
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return None
    if payload.get("rpc_url") != bootstrap_url:
        return None
    height = payload.get("height")
    if height is None:
        return None
    head = {
        "height": height,
        "hash": payload.get("head_hash"),
        "chainId": payload.get("chain_id"),
    }
    return head


def _extract_height(head_info: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract the height field while preserving zero values."""
    if not head_info:
        return None
    for key in ("height", "number", "blockNumber"):
        if key in head_info:
            value = head_info.get(key)
            if value is not None:
                return value
    return None


def _extract_chain_id(head_info: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract the chain id field while preserving zero values."""
    if not head_info:
        return None
    for key in ("chainId", "chain_id"):
        if key in head_info:
            value = head_info.get(key)
            if value is not None:
                return value
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _extract_sync_metrics(sync_status: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize sync status payload into consistent metrics."""
    metrics: Dict[str, Any] = {
        "syncing": False,
        "synchronized": False,
        "best_header_height": None,
        "best_block_height": None,
        "phase": None,
        "target_height": None,
    }

    if not isinstance(sync_status, dict):
        return metrics

    metrics["phase"] = sync_status.get("phase") or sync_status.get("state")
    metrics["synchronized"] = bool(sync_status.get("synchronized"))

    best_header_height = _coerce_int(
        sync_status.get("best_header_height")
        if "best_header_height" in sync_status
        else None
    )
    if best_header_height is None:
        best_header_height = _coerce_int(sync_status.get("bestHeaderHeight"))
    if best_header_height is None:
        best_header_height = _coerce_int(sync_status.get("best_header"))

    best_block_height = _coerce_int(
        sync_status.get("best_block_height")
        if "best_block_height" in sync_status
        else None
    )
    if best_block_height is None:
        best_block_height = _coerce_int(sync_status.get("bestBlockHeight"))
    if best_block_height is None:
        best_block_height = _coerce_int(sync_status.get("best_block"))

    sync_flag = sync_status.get("syncing")
    sync_progress: Optional[Dict[str, Any]] = None
    if isinstance(sync_flag, dict):
        metrics["syncing"] = True
        sync_progress = sync_flag
    elif isinstance(sync_flag, bool):
        metrics["syncing"] = sync_flag
        if sync_flag:
            sync_progress = sync_status
    elif sync_status.get("currentBlock") is not None or sync_status.get("highestBlock") is not None:
        metrics["syncing"] = True
        sync_progress = sync_status

    if sync_progress:
        current_block = _coerce_int(
            sync_progress.get("currentBlock")
            or sync_progress.get("current_block")
            or sync_progress.get("height")
        )
        target_height = _coerce_int(
            sync_progress.get("highestBlock")
            or sync_progress.get("targetHeight")
            or sync_progress.get("target_height")
        )
        if current_block is not None and best_block_height is None:
            best_block_height = current_block
        if target_height is not None and best_header_height is None:
            best_header_height = target_height
        metrics["target_height"] = target_height

    metrics["best_header_height"] = best_header_height
    metrics["best_block_height"] = best_block_height
    if (
        best_header_height is not None
        and best_block_height is not None
        and best_header_height > best_block_height
    ):
        metrics["synchronized"] = False
    if best_block_height == 0 and metrics.get("synchronized"):
        metrics["synchronized"] = False
    return metrics


def _format_sync_age(ts: Optional[float]) -> str:
    if not ts:
        return "n/a"
    try:
        age = max(0, int(time.time() - float(ts)))
    except Exception:
        return "n/a"
    return f"{age}s ago"


def _sync_diagnostics_lines(sync_status: Optional[Dict[str, Any]]) -> list[str]:
    if not isinstance(sync_status, dict):
        return []
    lines: list[str] = []
    eligible = sync_status.get("eligible_peers_for_headers") or []
    ineligible = sync_status.get("ineligible_peers_for_headers") or {}
    last_req_peer = sync_status.get("last_header_request_peer")
    last_resp_peer = sync_status.get("last_header_response_peer")
    last_req_at = sync_status.get("last_header_request_at")
    last_resp_at = sync_status.get("last_header_response_at")
    last_resp_count = sync_status.get("last_header_response_count")
    last_error = sync_status.get("last_header_error")
    if eligible:
        lines.append(f"  eligible_peers_for_headers: {eligible}")
    if ineligible:
        lines.append(f"  ineligible_peers_for_headers: {ineligible}")
    if last_req_peer or last_req_at:
        lines.append(
            f"  last_header_request: peer={last_req_peer or 'n/a'} "
            f"({ _format_sync_age(last_req_at) })"
        )
    if last_resp_peer or last_resp_at:
        lines.append(
            f"  last_header_response: peer={last_resp_peer or 'n/a'} "
            f"count={last_resp_count if last_resp_count is not None else 'n/a'} "
            f"({ _format_sync_age(last_resp_at) })"
        )
    if last_error:
        lines.append(f"  last_header_error: {last_error}")
    return lines


def _compute_sync_state(
    *,
    head_height: Optional[int],
    network_height: Optional[int],
    metrics: Dict[str, Any],
    near_tip_blocks: int = 10,
) -> str:
    """Compute a truthful sync state label based on local and network data."""
    if head_height is None:
        return "UNKNOWN"

    best_header_height = metrics.get("best_header_height")
    best_block_height = metrics.get("best_block_height")
    phase = metrics.get("phase")
    syncing = bool(metrics.get("syncing"))
    synchronized = bool(metrics.get("synchronized"))

    if best_header_height is not None and best_block_height is not None and best_header_height > best_block_height:
        if phase and str(phase).lower() in {"headers", "header", "syncing_headers"}:
            return "SYNCING_HEADERS"
        return "SYNCING_BLOCKS"

    if syncing:
        return "SYNCING"

    if head_height == 0:
        if network_height is not None and network_height > 0:
            return "SYNCING"
        return "IDLE"

    if network_height is not None:
        delta = network_height - head_height
        if delta <= 0:
            return "SYNCHRONIZED"
        if delta <= near_tip_blocks:
            return "NEAR_TIP"
        return "SYNCING"

    if synchronized:
        return "SYNCHRONIZED"

    return "IDLE"


def _extract_peer_head_height(peer: Dict[str, Any]) -> Optional[int]:
    for key in ("height", "head", "headHeight", "head_height", "blockNumber", "block_number"):
        if key in peer:
            return _coerce_int(peer.get(key))
    meta = peer.get("meta") if isinstance(peer.get("meta"), dict) else None
    if meta:
        for key in ("headHeight", "height", "head", "blockNumber"):
            if key in meta:
                return _coerce_int(meta.get(key))
    return None


def _extract_peer_head_hash(peer: Dict[str, Any]) -> Optional[str]:
    for key in ("headHash", "head_hash", "hash"):
        value = peer.get(key)
        if isinstance(value, str) and value:
            return value
    meta = peer.get("meta") if isinstance(peer.get("meta"), dict) else None
    if meta:
        for key in ("headHash", "head_hash", "hash"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _select_best_peer_head(peers: List[Dict[str, Any]]) -> tuple[Optional[int], Optional[str], Optional[Dict[str, Any]]]:
    best_height = None
    best_hash = None
    best_peer = None
    for peer in peers:
        height = _extract_peer_head_height(peer)
        if height is None:
            continue
        if best_height is None or height > best_height:
            best_height = height
            best_hash = _extract_peer_head_hash(peer)
            best_peer = peer
    return best_height, best_hash, best_peer


def _pretty(obj: Any) -> str:
    """Pretty-print JSON object."""
    return json.dumps(obj, indent=2)


def _format_timestamp(ts: Optional[float]) -> str:
    """Format a Unix timestamp as human-readable string."""
    if ts is None:
        return "N/A"
    try:
        from datetime import datetime
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return f"{ts}"


async def _get_sync_status(rpc_url: str) -> Optional[Dict[str, Any]]:
    """
    Get sync status from the node using various RPC methods.
    
    Tries multiple possible RPC method names for compatibility.
    """
    methods_to_try = [
        "sync.getStatus",
        "sync.status",
        "node.syncStatus",
        "chain.syncing",
        "sync.isSyncing",
        "eth_syncing",  # Ethereum compatibility
    ]
    
    for method in methods_to_try:
        try:
            result = await rpc_call(method, [], rpc_url=rpc_url)
            # If result is False (not syncing), return a standardized status
            if result is False:
                return {
                    "syncing": False,
                    "synchronized": True,
                }
            # If result is a dict, use it as-is
            if isinstance(result, dict):
                return result
        except Exception:
            continue
    
    # No sync status method available
    return None


def _extract_node_status(payload: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    chain_info = payload.get("chain") if isinstance(payload, dict) else None
    head_info = None
    if isinstance(chain_info, dict):
        head_info = chain_info.get("head") or chain_info.get("summary")
    if head_info is None:
        head_info = payload.get("head")

    sync_status = payload.get("sync") if isinstance(payload, dict) else None
    p2p_status = payload.get("p2p") if isinstance(payload, dict) else None
    return head_info if isinstance(head_info, dict) else None, sync_status if isinstance(sync_status, dict) else None, p2p_status if isinstance(p2p_status, dict) else None


async def _get_head_info(rpc_url: str) -> Optional[Dict[str, Any]]:
    """Get current chain head information."""
    return await rpc_call("chain.getHead", [], rpc_url=rpc_url)


async def _get_bootstrap_head_info(bootstrap_url: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch chain head from bootstrap RPC for network tip comparison."""
    if not bootstrap_url:
        return None
    guard_bootstrap_rpc(
        bootstrap_url,
        allow_bootstrap_methods=True,
        method="chain.getHead",
        bootstrap_url=bootstrap_url,
        quiet=True,
    )
    return await _get_head_info(bootstrap_url)


async def _get_peers(rpc_url: str) -> List[Dict[str, Any]]:
    """Get list of connected peers."""
    methods_to_try = [
        "net.peers",
        "p2p.listPeers",
        "p2p.getPeers",
        "p2p.peers",
        "admin_peers",
        "net_peers",
    ]
    
    for method in methods_to_try:
        try:
            peers = await rpc_call(method, [], rpc_url=rpc_url)
            if peers is not None:
                return peers if isinstance(peers, list) else []
        except Exception:
            continue
    
    return []


async def _get_peer_count(rpc_url: str) -> Optional[int]:
    """Get peer count using lightweight count methods."""
    methods_to_try = [
        "net.peerCount",
        "p2p.peerCount",
        "p2p.peer_count",
        "net_peerCount",
    ]

    for method in methods_to_try:
        try:
            result = await rpc_call(method, [], rpc_url=rpc_url)
            if isinstance(result, int):
                return result
            if isinstance(result, str) and result.isdigit():
                return int(result)
        except Exception:
            continue
    return None


def _fetch_bootstrap_seeds(
    net_cfg,
    bootstrap_url: Optional[str],
    *,
    allow_bootstrap_rpc: bool = False,
) -> tuple[list[str], list[str]]:
    """Fetch seed peers from discovery sources or bootstrap RPC (optional)."""

    seeds: list[str] = []
    fetch_errors: list[str] = []

    if allow_bootstrap_rpc and bootstrap_url:
        try:
            guard_bootstrap_rpc(
                bootstrap_url,
                allow_bootstrap_methods=True,
                method="net.getBootstrapSeeds",
                bootstrap_url=bootstrap_url,
            )
            resp = asyncio.run(rpc_call("net.getBootstrapSeeds", [], rpc_url=bootstrap_url))
            seeds = list((resp or {}).get("seeds") or [])
            if not seeds:
                alt_resp = asyncio.run(rpc_call("bootstrap.getSeeds", [], rpc_url=bootstrap_url))
                seeds = list((alt_resp or {}).get("seeds") or [])
        except Exception as exc:
            fetch_errors.append(str(exc))

    if not seeds:
        try:
            os.environ.setdefault("ANIMICA_P2P_CHAIN_ID", str(net_cfg.chain_id))
            from p2p.config import load_config as load_p2p_config

            p2p_cfg = load_p2p_config()
            seeds = list(getattr(p2p_cfg, "seeds", []) or [])
        except Exception as exc:
            fetch_errors.append(f"P2P config seeds unavailable: {exc}")

    if not seeds:
        try:
            from p2p.discovery import seeds as seed_discovery

            bundle = asyncio.run(
                seed_discovery.discover_for_network(
                    net_cfg.chain_id, resolve=False, include_fallbacks=False
                )
            )
            discovered = []
            for endpoint in bundle.endpoints:
                if getattr(endpoint, "scheme", "") != "tcp":
                    continue
                host = getattr(endpoint, "host", "")
                port = getattr(endpoint, "port", None)
                if host and port:
                    discovered.append(f"{host}:{port}")
            seeds = discovered
        except Exception as exc:
            fetch_errors.append(f"DNS seed discovery unavailable: {exc}")

    if not seeds:
        seeds = get_seed_nodes(net_cfg.name)

    return list(dict.fromkeys(seeds)), fetch_errors


def _seed_local_peerstores(
    net_cfg,
    *,
    target_rpc_url: str,
    bootstrap_url: Optional[str],
    allow_bootstrap_rpc: bool = False,
    quiet: bool = False,
) -> tuple[int, bool, list[str]]:
    """Persist bootstrap seeds locally and push them into a running node."""

    seeds, fetch_errors = _fetch_bootstrap_seeds(
        net_cfg, bootstrap_url, allow_bootstrap_rpc=allow_bootstrap_rpc
    )
    store_path = Path(net_cfg.data_dir).expanduser() / "p2p" / "peers.json"

    if not seeds:
        if not quiet:
            typer.secho("⚠ No seeds available; cannot bootstrap peers", fg=typer.colors.YELLOW)
        return 0, False, fetch_errors

    stored = 0
    for seed in seeds:
        peer_id = _generate_peer_id(seed)
        try:
            _write_peer_to_store(store_path, peer_id, seed)
            _write_peer_to_sqlite(store_path, peer_id, seed, direction="outbound")
            stored += 1
        except Exception as exc:
            if not quiet:
                typer.secho(f"⚠ Failed to persist {seed}: {exc}", fg=typer.colors.YELLOW)

    rpc_added = False
    rpc_error: Optional[str] = None
    node_running, probe_error = _probe_rpc_for_peer_injection(target_rpc_url)
    if not node_running:
        rpc_error = probe_error
    else:
        for method_name in ("p2p.addPeers", "p2p.importPeers"):
            try:
                import_resp, error = asyncio.run(
                    _rpc_call_with_error(method_name, [seeds], rpc_url=target_rpc_url)
                )
            except Exception as exc:
                rpc_error = str(exc)
                break

            if error and _is_unauthorized_error(error):
                rpc_error = _rpc_error_message(error) or "UNAUTHORIZED"
                break
            if error and _is_method_not_found_error(error):
                rpc_error = "RPC method not available on this node"
                continue

            rpc_added, rpc_error = _rpc_operation_succeeded(import_resp)
            if rpc_added:
                break
            rpc_error = rpc_error or _rpc_error_message(error) or f"{method_name} did not report success"

    if not quiet:
        if stored:
            typer.secho(f"✓ Added {stored} seed(s) to local peer store", fg=typer.colors.GREEN)
        if rpc_added:
            typer.secho("✓ Seeds imported into running node", fg=typer.colors.GREEN)
            status, status_error = _fetch_peer_status(target_rpc_url)
            if status:
                _print_peer_status(status)
            elif status_error:
                typer.secho(f"⚠ Unable to refresh peer status: {status_error}", fg=typer.colors.YELLOW)
        elif not node_running:
            if rpc_error:
                typer.secho(f"⚠ RPC not reachable: {rpc_error}", fg=typer.colors.YELLOW)
            typer.secho(
                "✓ Saved seeds. Start your node and re-run sync force.",
                fg=typer.colors.GREEN,
            )
        elif rpc_error:
            if "method not available" in rpc_error.lower():
                typer.secho(
                    "⚠ RPC method missing: update the node to enable peer injection.",
                    fg=typer.colors.YELLOW,
                )
            elif "unauthorized" in rpc_error.lower():
                typer.secho("⚠ Peer injection unauthorized.", fg=typer.colors.YELLOW)
                if is_local_rpc_url(target_rpc_url):
                    typer.echo("  - Not local: RPC did not treat this request as localhost.")
                else:
                    typer.echo("  - Not local: RPC URL is not localhost.")
                if not os.getenv("ANIMICA_RPC_ADMIN_TOKEN"):
                    typer.echo("  - Missing token: set ANIMICA_RPC_ADMIN_TOKEN.")
                typer.echo("  Fix: use localhost RPC or provide X-Animica-Admin-Token.")
            else:
                typer.secho(
                    f"⚠ Unable to push seeds into running node: {rpc_error}",
                    fg=typer.colors.YELLOW,
                )

        if fetch_errors:
            typer.secho("Bootstrap RPC errors (using fallback seeds if available):", fg=typer.colors.YELLOW)
            for err in fetch_errors:
                typer.echo(f"  - {err}")

    return stored, rpc_added, fetch_errors


def _persist_connected_peers(net_cfg, peers: List[Dict[str, Any]], *, quiet: bool = True) -> int:
    """Persist connected peers into the local peer stores."""

    if not peers:
        return 0

    store_path = Path(net_cfg.data_dir).expanduser() / "p2p" / "peers.json"
    stored = 0
    for peer in peers:
        peer_id = peer.get("id") or peer.get("peerId") or peer.get("peer_id")
        addr = peer.get("addr") or peer.get("address") or peer.get("multiaddr")
        if not peer_id or not addr:
            continue
        try:
            _write_peer_to_store(store_path, peer_id, addr)
            _write_peer_to_sqlite(store_path, peer_id, addr, direction="inbound")
            stored += 1
        except Exception as exc:
            if not quiet:
                typer.secho(f"⚠ Failed to persist peer {peer_id}: {exc}", fg=typer.colors.YELLOW)

    return stored


def _sync_state_path(net_cfg) -> Path:
    """Return the path for persisting sync progress state."""

    return Path(net_cfg.data_dir).expanduser() / "sync" / "progress.json"


def _persist_sync_state(
    net_cfg,
    *,
    rpc_url: str,
    head_info: Optional[Dict[str, Any]],
    peers: List[Dict[str, Any]],
    network_head_height: Optional[int] = None,
    network_head_hash: Optional[str] = None,
    network_peer: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> None:
    """Persist sync progress to disk for continuity across restarts."""

    state_path = _sync_state_path(net_cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    height = _extract_height(head_info)
    head_hash = head_info.get("hash") if head_info else None
    chain_id = _extract_chain_id(head_info)

    payload = {
        "rpc_url": rpc_url,
        "height": height,
        "head_hash": head_hash,
        "chain_id": chain_id,
        "peer_count": len(peers),
        "updated_at": time.time(),
        "peers": peers,
    }
    if network_head_height is not None:
        payload["network_height"] = network_head_height
    if network_head_hash is not None:
        payload["network_head_hash"] = network_head_hash
    if network_peer:
        payload["network_peer_id"] = network_peer.get("id") or network_peer.get("peerId") or network_peer.get("peer_id")
        payload["network_peer_addr"] = network_peer.get("addr") or network_peer.get("address")
    if note:
        payload["note"] = note

    state_path.write_text(json.dumps(payload, indent=2))


async def _trigger_sync(rpc_url: str, *, clear_cache: bool = False) -> bool:
    """
    Trigger a sync operation on the node.
    
    Returns True if sync was triggered successfully, False otherwise.
    """
    def _trigger_succeeded(result: Any) -> bool:
        if isinstance(result, dict):
            if result.get("error"):
                return False
            for key in ("success", "started", "ok", "triggered"):
                if key in result:
                    return bool(result.get(key))
            status = result.get("status")
            if isinstance(status, str):
                normalized = status.strip().lower()
                if normalized in {"ok", "success", "started", "triggered", "running"}:
                    return True
                if normalized in {"error", "failed", "failure"}:
                    return False
            inner = result.get("result")
            if isinstance(inner, bool):
                return inner
            return True
        if isinstance(result, bool):
            return result
        if isinstance(result, str):
            normalized = result.strip().lower()
            if normalized in {"ok", "success", "started", "triggered", "running", "true"}:
                return True
            if any(token in normalized for token in ("error", "fail")):
                return False
            return True
        if result is None:
            return False
        return True

    methods_to_try = [
        "node.syncTrigger",
        "sync.trigger",
        "sync.force",
        "sync.start",
        "node.startSync",
        "node.triggerSync",
        "p2p.sync",
    ]
    
    for method in methods_to_try:
        try:
            params: list[Any] | dict[str, Any] = []
            if method == "sync.force" and clear_cache:
                params = {"clear_cache": True}
            result = await rpc_call(method, params, rpc_url=rpc_url, timeout=DEFAULT_RPC_TIMEOUT)
            if _trigger_succeeded(result):
                return True
        except Exception:
            continue
    
    return False


async def _get_local_sync_snapshot(
    rpc_url: str,
) -> Dict[str, Any]:
    """Fetch local node sync snapshot with best-effort fallbacks."""
    head_info: Optional[Dict[str, Any]] = None
    sync_status: Optional[Dict[str, Any]] = None
    p2p_status: Optional[Dict[str, Any]] = None
    peer_count: Optional[int] = None
    peers: List[Dict[str, Any]] = []

    try:
        node_status = await rpc_call("node.getStatus", [], rpc_url=rpc_url, timeout=DEFAULT_RPC_TIMEOUT)
        if isinstance(node_status, dict):
            head_info, sync_status, p2p_status = _extract_node_status(node_status)
            if p2p_status:
                peer_count = p2p_status.get("peers_total")
    except Exception as exc:
        if not is_method_not_found(exc):
            raise

    if head_info is None:
        head_info = await _get_head_info(rpc_url)
    if sync_status is None:
        sync_status = await _get_sync_status(rpc_url)
    if peer_count is None:
        peer_count = await _get_peer_count(rpc_url)
    if peer_count is None:
        peers = await _get_peers(rpc_url)
        peer_count = len(peers) if peers else None

    return {
        "head_info": head_info,
        "sync_status": sync_status,
        "p2p_status": p2p_status,
        "peer_count": peer_count,
        "peers": peers,
    }


@app.command(name="status")
def sync_status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    bootstrap_rpc: Optional[str] = typer.Option(
        None,
        "--bootstrap-rpc",
        help="Bootstrap RPC endpoint (discovery/snapshots only; never used for local progress)",
        envvar=BOOTSTRAP_RPC_ENV,
    ),
    allow_bootstrap_rpc: bool = typer.Option(
        False,
        "--allow-bootstrap-rpc/--no-allow-bootstrap-rpc",
        help="Allow bootstrap RPC usage for optional network tip comparison",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON format"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed information"
    ),
) -> None:
    """
    Show current blockchain synchronization status.
    
    Displays information about:
    - Current head height and hash
    - Sync progress (if syncing)
    - Connected peers
    - Network activity
    
    Examples:
        animica sync status
        animica sync status --json
        animica sync status --verbose
    """
    url, bootstrap_url = _resolve_sync_endpoints(
        rpc_url, bootstrap_rpc, allow_bootstrap_rpc=allow_bootstrap_rpc
    )
    net_cfg = load_network_config()
    peer_count_error: Optional[Exception] = None
    bootstrap_source = "live"
    
    try:
        candidate_urls = candidate_rpc_urls(url)
        head_info: Optional[Dict[str, Any]] = None
        sync_status: Optional[Dict[str, Any]] = None
        p2p_status: Optional[Dict[str, Any]] = None
        peer_count_result: Optional[int] = None
        peer_count_error = None
        peers: List[Dict[str, Any]] = []
        bootstrap_head: Optional[Dict[str, Any]] = None
        last_error: Optional[Exception] = None
        used_url: Optional[str] = None

        for candidate in candidate_urls:
            try:
                node_status = asyncio.run(
                    rpc_call("node.getStatus", [], rpc_url=candidate, timeout=DEFAULT_RPC_TIMEOUT)
                )
                if isinstance(node_status, dict):
                    head_info, sync_status, p2p_status = _extract_node_status(node_status)
                    used_url = candidate
                    break
            except Exception as exc:
                if is_method_not_found(exc):
                    try:
                        async def gather_info():
                            return await asyncio.gather(
                                _get_head_info(candidate),
                                _get_sync_status(candidate),
                                _get_peer_count(candidate),
                                _get_peers(candidate),
                                _get_bootstrap_head_info(bootstrap_url),
                                return_exceptions=True
                            )

                        head_info, sync_status, peer_count_result, peers, bootstrap_head = asyncio.run(gather_info())

                        if isinstance(head_info, Exception):
                            head_info = None
                        if isinstance(sync_status, Exception):
                            sync_status = None
                        if isinstance(peer_count_result, Exception):
                            peer_count_error = peer_count_result
                            peer_count_result = None
                        if isinstance(peers, Exception):
                            peer_count_error = peer_count_error or peers
                            peers = []
                        if isinstance(bootstrap_head, Exception):
                            bootstrap_head = None

                        used_url = candidate
                        break
                    except Exception as legacy_exc:
                        last_error = legacy_exc
                        continue
                last_error = exc
                continue

        if used_url is None:
            raise RuntimeError(
                f"All connection attempts failed (tried: {', '.join(candidate_urls)}): {last_error}"
            )

        url = used_url

        if bootstrap_head is None and bootstrap_url:
            cached_bootstrap = _load_cached_bootstrap_head(net_cfg, bootstrap_url)
            if cached_bootstrap:
                bootstrap_head = cached_bootstrap
                bootstrap_source = "cached"
        
    except Exception as e:
        typer.echo(f"Error: Unable to connect to node at {url}", err=True)
        typer.echo(f"Details: {e}", err=True)
        typer.echo(
            "\nHint: Ensure the node is running with: animica node status",
            err=True
        )
        raise typer.Exit(code=1)
    
    # Extract info
    height = _extract_height(head_info)
    head_hash = head_info.get("hash") or head_info.get("blockHash") if head_info else None
    chain_id = _extract_chain_id(head_info)
    bootstrap_height = _extract_height(bootstrap_head)
    bootstrap_hash = bootstrap_head.get("hash") if bootstrap_head else None

    best_peer_height, best_peer_hash, best_peer = _select_best_peer_head(peers)
    network_height = bootstrap_height
    network_hash = bootstrap_hash
    network_source = None
    if bootstrap_height is not None:
        network_source = "bootstrap"
    if best_peer_height is not None and (network_height is None or best_peer_height > network_height):
        network_height = best_peer_height
        network_hash = best_peer_hash
        network_source = "peer"
    
    metrics = _extract_sync_metrics(sync_status)
    is_syncing = bool(metrics.get("syncing"))
    target_height = metrics.get("target_height")
    best_header_height = metrics.get("best_header_height")
    best_block_height = metrics.get("best_block_height")
    sync_state = _compute_sync_state(
        head_height=height or 0 if height is not None else None,
        network_height=network_height,
        metrics=metrics,
    )
    
    peer_count: Optional[int] = None
    if isinstance(peer_count_result, int):
        peer_count = peer_count_result
    elif peers:
        peer_count = len(peers)
    elif p2p_status:
        peer_count = p2p_status.get("peers_total")
    
    # JSON output
    peer_error_msg = None
    if p2p_status is None:
        if peer_count_result is None and peer_count is None and "peer_count_result" in locals():
            peer_error_msg = "RPC peer methods unavailable"
        if peer_count_error:
            peer_error_msg = str(peer_count_error)

    rpc_unavailable = head_info is None and sync_status is None and (peer_error_msg or peer_count_error)
    if rpc_unavailable and not peers:
        typer.secho(f"RPC unavailable at {url}: {peer_error_msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_output:
        output = {
            "rpc_url": url,
            "rpc_reachable": True,
            "bootstrap_rpc": bootstrap_url,
            "chain_id": chain_id,
            "height": height,
            "bootstrap_height": bootstrap_height,
            "head_hash": head_hash,
            "syncing": is_syncing,
            "sync_state": sync_state,
            "peer_count": peer_count,
        }
        if network_height is not None:
            output["network_height"] = network_height
            output["network_head_hash"] = network_hash
            output["network_source"] = network_source
        if target_height is not None:
            output["target_height"] = target_height
        if best_header_height is not None:
            output["best_header_height"] = best_header_height
        if best_block_height is not None:
            output["best_block_height"] = best_block_height
        if p2p_status:
            output["p2p_running"] = p2p_status.get("p2p_running")
            output["peers_inbound"] = p2p_status.get("peers_inbound")
            output["peers_outbound"] = p2p_status.get("peers_outbound")
        if peer_error_msg:
            output["peer_error"] = peer_error_msg
        if verbose and peers:
            output["peers"] = peers
        _persist_sync_state(
            net_cfg,
            rpc_url=url,
            head_info=head_info,
            peers=peers,
            network_head_height=network_height,
            network_head_hash=network_hash,
            network_peer=best_peer,
            note="status",
        )
        typer.echo(_pretty(output))
        return
    
    # Human-readable output
    typer.secho("\n╔═══════════════════════════════════════════════════════╗", fg=typer.colors.CYAN)
    typer.secho("║        Blockchain Synchronization Status              ║", fg=typer.colors.CYAN, bold=True)
    typer.secho("╚═══════════════════════════════════════════════════════╝", fg=typer.colors.CYAN)
    typer.echo()
    
    # Connection info
    typer.echo(f"Target RPC:    {url}")
    typer.echo("RPC reachable: yes")
    bootstrap_label = "disabled" if not allow_bootstrap_rpc else (bootstrap_url or "not configured")
    if bootstrap_source == "cached" and bootstrap_url:
        bootstrap_label = f"{bootstrap_label} (cached head)"
    typer.echo(f"Bootstrap RPC: {bootstrap_label}")
    if chain_id:
        typer.echo(f"Chain ID:    {chain_id}")
    typer.echo()
    
    # Head info
    typer.secho("Current Head:", fg=typer.colors.BRIGHT_BLUE, bold=True)
    if head_info is None:
        typer.echo("  Height:    RPC unavailable")
    elif height is not None:
        typer.echo(f"  Height:    {height}")
    else:
        typer.echo("  Height:    Unknown")
    
    if head_hash:
        typer.echo(f"  Hash:      {head_hash}")
    typer.echo()

    if network_height is not None and network_height > 0 and (height is None or height < network_height):
        source_label = "bootstrap" if network_source == "bootstrap" else "peer"
        typer.secho("Network Head:", fg=typer.colors.BRIGHT_BLUE, bold=True)
        typer.echo(f"  Height:    {network_height} ({source_label})")
        if network_hash:
            typer.echo(f"  Hash:      {network_hash}")
        if best_peer and network_source == "peer":
            peer_id = best_peer.get("id") or best_peer.get("peerId") or best_peer.get("peer_id")
            peer_addr = best_peer.get("addr") or best_peer.get("address")
            if peer_id or peer_addr:
                typer.echo(f"  Source:    {peer_id or 'unknown'} {f'({peer_addr})' if peer_addr else ''}")
        typer.echo()
    
    # Sync status
    typer.secho("Sync Status:", fg=typer.colors.BRIGHT_BLUE, bold=True)
    if sync_state in {"SYNCING_HEADERS", "SYNCING_BLOCKS", "SYNCING"}:
        typer.secho(f"  Status:    {sync_state}", fg=typer.colors.YELLOW, bold=True)
        if best_header_height is not None or best_block_height is not None:
            typer.echo(
                f"  Headers:   {best_header_height or 0} | Blocks: {best_block_height or 0}"
            )
        if target_height is not None and height is not None:
            progress_pct = (height / target_height * 100) if target_height > 0 else 0
            typer.echo(f"  Progress:  {height} / {target_height} ({progress_pct:.1f}%)")
            remaining = target_height - height
            typer.echo(f"  Remaining: {remaining} blocks")
    elif sync_state == "SYNCHRONIZED":
        typer.secho("  Status:    SYNCHRONIZED", fg=typer.colors.GREEN, bold=True)
    elif sync_state == "NEAR_TIP":
        typer.secho("  Status:    NEAR_TIP", fg=typer.colors.YELLOW, bold=True)
    elif sync_state == "UNKNOWN":
        typer.secho("  Status:    UNKNOWN", fg=typer.colors.YELLOW)
    elif height == 0:
        typer.secho("  Status:    IDLE (genesis)", fg=typer.colors.YELLOW)
    else:
        typer.secho("  Status:    IDLE (no blocks)", fg=typer.colors.YELLOW)
    typer.echo()
    
    # Peer info
    typer.secho("Network:", fg=typer.colors.BRIGHT_BLUE, bold=True)
    if p2p_status:
        typer.echo(f"  P2P running: {p2p_status.get('p2p_running')}")
        inbound = p2p_status.get("peers_inbound")
        outbound = p2p_status.get("peers_outbound")
        if peer_count is None:
            peer_count = p2p_status.get("peers_total")
        if peer_count is not None:
            typer.echo(f"  Peers:     {peer_count} connected (in={inbound}, out={outbound})")
        elif peer_error_msg:
            typer.echo(f"  Peers:     unavailable ({peer_error_msg})")
    elif peer_count is None:
        typer.echo(
            f"  Peers:     unavailable{f' ({peer_error_msg})' if peer_error_msg else ''}"
        )
    else:
        typer.echo(f"  Peers:     {peer_count} connected")
    
    if peer_count == 0:
        typer.secho(
            "\n⚠ Warning: No peers connected. Sync will not progress without peers.",
            fg=typer.colors.YELLOW
        )
        typer.echo("  Try: animica peer bootstrap")
        typer.echo("       animica peer add <address>")
    
    if verbose and peers:
        typer.echo()
        typer.secho("Connected Peers:", fg=typer.colors.BRIGHT_BLUE, bold=True)
        for i, peer in enumerate(peers[:10], 1):  # Show max 10 peers
            peer_id = peer.get("id") or peer.get("peerId") or peer.get("peer_id") or "unknown"
            addr = peer.get("addr") or peer.get("address") or "unknown"
            status = peer.get("status") or "connected"
            typer.echo(f"  {i}. {peer_id[:16]}... ({addr}) - {status}")
        if len(peers) > 10:
            typer.echo(f"  ... and {len(peers) - 10} more peers")
    
    # Recommendations
    typer.echo()
    if peer_count == 0:
        typer.secho("💡 Tip: Connect to seed nodes to start syncing:", fg=typer.colors.CYAN)
        typer.echo("   animica peer bootstrap")
    elif peer_count is None and peer_error_msg:
        typer.secho("💡 RPC peer data unavailable. Check node RPC or logs.", fg=typer.colors.YELLOW)
    elif sync_state in {"SYNCING_HEADERS", "SYNCING_BLOCKS", "SYNCING", "NEAR_TIP"}:
        typer.secho("💡 Syncing in progress... Check back later or run:", fg=typer.colors.CYAN)
        typer.echo("   animica sync status")
    elif height == 0 and network_height and network_height > 0:
        typer.secho("⚠ Node is not synced yet. Wait for peers or run sync force.", fg=typer.colors.YELLOW)
    elif height == 0:
        typer.secho("⚠ Node is at genesis; waiting for sync data.", fg=typer.colors.YELLOW)
        typer.echo("   Try: animica sync force")
    elif sync_state == "SYNCHRONIZED":
        typer.secho("✓ Node is synchronized with the network", fg=typer.colors.GREEN)
    else:
        typer.secho("⚠ Node is not yet synchronized.", fg=typer.colors.YELLOW)

    _persist_sync_state(
        net_cfg,
        rpc_url=url,
        head_info=head_info,
        peers=peers,
        network_head_height=network_height,
        network_head_hash=network_hash,
        network_peer=best_peer,
        note="status",
    )


@app.command(name="pause")
def pause_sync(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """Pause background sync on the node."""
    url, _ = _resolve_sync_endpoints(rpc_url, None, allow_bootstrap_rpc=False)
    try:
        result = asyncio.run(rpc_call("sync.pause", [], rpc_url=url))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Failed to pause sync: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if isinstance(result, dict) and result.get("paused") is True:
        typer.secho("✓ Sync paused", fg=typer.colors.GREEN)
    else:
        typer.secho("⚠ Sync pause requested but may not be supported", fg=typer.colors.YELLOW)


@app.command(name="resume")
def resume_sync(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """Resume background sync on the node."""
    url, _ = _resolve_sync_endpoints(rpc_url, None, allow_bootstrap_rpc=False)
    try:
        result = asyncio.run(rpc_call("sync.resume", [], rpc_url=url))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Failed to resume sync: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if isinstance(result, dict) and result.get("paused") is False:
        typer.secho("✓ Sync resumed", fg=typer.colors.GREEN)
    else:
        typer.secho("⚠ Sync resume requested but may not be supported", fg=typer.colors.YELLOW)


@app.command(name="force")
def force_sync(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    bootstrap_rpc: Optional[str] = typer.Option(
        None,
        "--bootstrap-rpc",
        help="Bootstrap RPC endpoint (discovery/snapshots only; never used for local progress)",
        envvar=BOOTSTRAP_RPC_ENV,
    ),
    allow_bootstrap_rpc: bool = typer.Option(
        False,
        "--allow-bootstrap-rpc/--no-allow-bootstrap-rpc",
        help="Allow bootstrap RPC usage for discovery fallback only",
    ),
    timeout: int = typer.Option(
        300, "--timeout", help="Maximum time to wait for sync to start (seconds)"
    ),
    check_interval: int = typer.Option(
        5, "--check-interval", help="How often to check sync progress (seconds)"
    ),
    clear_cache: bool = typer.Option(
        False,
        "--clear-cache",
        help="Clear sync cache before forcing sync",
    ),
    ) -> None:
    """
    Force a blockchain resynchronization.
    
    This command triggers the node to start or restart synchronization with
    peers. It will attempt to:
    1. Trigger sync via RPC
    2. Monitor progress for a specified timeout period
    3. Report final status
    
    Use this when:
    - Sync appears stuck
    - After adding new peers
    - After network connectivity issues
    
    Examples:
        animica sync force
        animica sync force --timeout 600
        animica sync force --clear-cache
    """
    net_cfg = load_network_config()
    url, bootstrap_url = _resolve_sync_endpoints(
        rpc_url, bootstrap_rpc, allow_bootstrap_rpc=allow_bootstrap_rpc
    )

    bootstrap_host = urlparse(bootstrap_url).hostname if bootstrap_url else None
    target_host = urlparse(url).hostname if url else None

    if bootstrap_url and bootstrap_host and target_host and bootstrap_host != target_host:
        _seed_local_peerstores(
            net_cfg,
            target_rpc_url=url,
            bootstrap_url=bootstrap_url,
            allow_bootstrap_rpc=allow_bootstrap_rpc,
            quiet=True,
        )

    typer.secho("\n🔄 Forcing blockchain synchronization...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Target RPC:    {url}")
    bootstrap_label = "disabled" if not allow_bootstrap_rpc else (bootstrap_url or "not configured")
    typer.echo(f"Bootstrap RPC: {bootstrap_label}")
    typer.echo()
    
    # Get initial state
    try:
        initial_head = asyncio.run(_get_head_info(url))
        initial_height = _extract_height(initial_head) or 0
        typer.echo(f"Current height: {initial_height}")
    except Exception as e:
        typer.echo(f"Error: Unable to connect to node at {url}")
        typer.echo(f"Details: {e}")
        raise typer.Exit(code=1)
    
    # Check peer count
    try:
        peers = asyncio.run(_get_peers(url))
        peer_count = len(peers)
        typer.echo(f"Connected peers: {peer_count}")
        _persist_connected_peers(net_cfg, peers, quiet=True)
        _persist_sync_state(net_cfg, rpc_url=url, head_info=initial_head, peers=peers, note="initial")

        if peer_count == 0:
            typer.secho(
                "\n⚠ Warning: No peers connected. Cannot sync without peers.",
                fg=typer.colors.YELLOW,
                bold=True
            )
            typer.echo()
            typer.echo("Please connect to peers first:")
            typer.echo("  animica peer bootstrap")
            typer.echo("  animica peer add <address>")
            typer.echo()

            typer.echo("Auto-bootstrapping peers from discovery sources...")
            stored, rpc_added, _ = _seed_local_peerstores(
                net_cfg,
                target_rpc_url=url,
                bootstrap_url=bootstrap_url,
                allow_bootstrap_rpc=allow_bootstrap_rpc,
                quiet=False,
            )
            if stored:
                try:
                    peers = asyncio.run(_get_peers(url))
                    peer_count = len(peers)
                    typer.echo(f"Connected peers after bootstrap: {peer_count}")
                    _persist_connected_peers(net_cfg, peers, quiet=True)
                    _persist_sync_state(
                        net_cfg,
                        rpc_url=url,
                        head_info=initial_head,
                        peers=peers,
                        note="post-bootstrap",
                    )
                except Exception:
                    typer.secho("Warning: Could not refresh peer list after bootstrap", fg=typer.colors.YELLOW)
    except Exception:
        peer_count = 0
        typer.secho("Warning: Could not check peer count", fg=typer.colors.YELLOW)
    
    typer.echo()
    typer.echo("Attempting to trigger sync...")
    
    # Try to trigger sync
    triggered = asyncio.run(_trigger_sync(url, clear_cache=clear_cache))
    
    if not triggered:
        typer.secho(
            "⚠ Could not trigger sync via RPC (methods may not be available)",
            fg=typer.colors.YELLOW
        )
        typer.echo()
        typer.echo("The node may sync automatically if:")
        typer.echo("  - Peers are connected")
        typer.echo("  - Sync is enabled in node configuration")
        typer.echo()
        typer.echo("You can still monitor sync progress with:")
        typer.echo("  animica sync status")
        typer.echo()
        
        if not typer.confirm("Monitor sync progress anyway?"):
            raise typer.Exit(code=0)
    else:
        typer.secho("✓ Sync triggered successfully", fg=typer.colors.GREEN)
    
    # Monitor progress
    typer.echo()
    typer.echo(f"Monitoring sync progress for {timeout} seconds...")
    typer.echo(f"(Checking every {check_interval} seconds)")
    typer.echo()
    
    start_time = time.time()
    last_height = initial_height
    stall_count = 0
    max_stalls = 3

    elapsed = 0
    last_status_line: Optional[str] = None
    last_progress = last_height

    while elapsed < timeout:
        time.sleep(min(check_interval, timeout - elapsed))
        elapsed = int(time.time() - start_time)

        try:
            snapshot = asyncio.run(_get_local_sync_snapshot(url))
            head_info = snapshot.get("head_info")
            sync_status = snapshot.get("sync_status")
            peers = snapshot.get("peers") or []
            peer_count = snapshot.get("peer_count")

            metrics = _extract_sync_metrics(sync_status)
            best_header_height = metrics.get("best_header_height") or 0
            best_block_height = metrics.get("best_block_height") or 0
            current_height = _extract_height(head_info) or 0
            phase = metrics.get("phase")
            sync_state = _compute_sync_state(
                head_height=current_height,
                network_height=None,
                metrics=metrics,
            )
            phase_label = str(phase).upper() if phase else None
            display_state = phase_label or sync_state
            if best_header_height > best_block_height and display_state == "IDLE":
                display_state = "BLOCKS"

            progress_height = max(current_height, best_block_height)

            status_line = (
                f"Height {current_height} | headers {best_header_height} | "
                f"blocks {best_block_height} | peers {peer_count if peer_count is not None else 'n/a'} | "
                f"{display_state}"
            )
            if status_line != last_status_line:
                typer.echo(status_line)
                last_status_line = status_line

            _persist_connected_peers(net_cfg, peers, quiet=True)
            _persist_sync_state(net_cfg, rpc_url=url, head_info=head_info, peers=peers)

            if progress_height > last_progress:
                blocks_synced = progress_height - last_progress
                typer.echo(f"✓ Progress: +{blocks_synced} blocks")
                last_progress = progress_height
                stall_count = 0
            else:
                stall_count += 1
                if stall_count >= max_stalls:
                    typer.echo(f"⚠ No progress for {stall_count * check_interval} seconds")
                    diag_lines = _sync_diagnostics_lines(sync_status)
                    if diag_lines:
                        typer.echo("Diagnostics:")
                        for line in diag_lines:
                            typer.echo(line)
                    # Try to re-import seeds and retrigger sync to keep progress moving
                    added, _, _ = _seed_local_peerstores(
                        net_cfg,
                        target_rpc_url=url,
                        bootstrap_url=bootstrap_url,
                        allow_bootstrap_rpc=allow_bootstrap_rpc,
                        quiet=True,
                    )
                    if added:
                        try:
                            peers = asyncio.run(_get_peers(url))
                            peer_count = len(peers)
                            typer.echo(f"✓ Re-seeded peers; {peer_count} connected")
                        except Exception:
                            typer.secho("⚠ Could not refresh peer list after re-seeding", fg=typer.colors.YELLOW)
                    asyncio.run(_trigger_sync(url))
        except Exception:
            typer.echo("⚠ Connection error")
    
    typer.echo()
    typer.echo()
    
    # Final status
    try:
        snapshot = asyncio.run(_get_local_sync_snapshot(url))
        final_head = snapshot.get("head_info")
        final_height = _extract_height(final_head) or 0
        sync_status = snapshot.get("sync_status")
        metrics = _extract_sync_metrics(sync_status)
        best_header_height = metrics.get("best_header_height") or 0
        best_block_height = metrics.get("best_block_height") or 0
        phase = metrics.get("phase")
        sync_state = _compute_sync_state(
            head_height=final_height,
            network_height=None,
            metrics=metrics,
        )
        phase_label = str(phase).upper() if phase else None
        display_state = phase_label or sync_state
        if best_header_height > best_block_height and display_state == "IDLE":
            display_state = "BLOCKS"

        blocks_synced = final_height - initial_height

        typer.secho("━" * 60, fg=typer.colors.CYAN)
        typer.secho("Final Status:", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  Initial height: {initial_height}")
        typer.echo(f"  Final height:   {final_height}")
        typer.echo(f"  Headers:        {best_header_height}")
        typer.echo(f"  Blocks:         {best_block_height}")
        typer.echo(f"  Sync state:     {display_state}")
        typer.echo(f"  Blocks synced:  {blocks_synced}")

        if blocks_synced > 0:
            rate = blocks_synced / (elapsed / 60)  # blocks per minute
            typer.echo(f"  Sync rate:      {rate:.1f} blocks/minute")
            typer.secho("\n✓ Sync is progressing", fg=typer.colors.GREEN, bold=True)
        else:
            typer.secho("\n⚠ No blocks synced", fg=typer.colors.YELLOW, bold=True)
            typer.echo()
            typer.echo("Possible reasons:")
            typer.echo("  - Node is already at network head")
            typer.echo("  - No peers have newer blocks")
            typer.echo("  - Sync is disabled or stuck")
            typer.echo()
            diag_lines = _sync_diagnostics_lines(sync_status)
            if diag_lines:
                typer.echo("Diagnostics:")
                for line in diag_lines:
                    typer.echo(line)
                typer.echo()
            typer.echo("Check peer status with: animica peer list")
        peers = snapshot.get("peers") or []
        _persist_connected_peers(net_cfg, peers, quiet=True)
        _persist_sync_state(net_cfg, rpc_url=url, head_info=final_head, peers=peers, note="final")
    except Exception as e:
        typer.echo(f"Error checking final status: {e}", err=True)
        raise typer.Exit(code=1)
    
    typer.echo()
    typer.echo("Use 'animica sync status' to check current sync state.")


if __name__ == "__main__":
    app()
