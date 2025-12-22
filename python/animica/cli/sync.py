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
    _resolve_store_paths,
    _rpc_operation_succeeded,
    _write_peer_to_sqlite,
    _write_peer_to_store,
)
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.seeds import get_seed_nodes
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage blockchain synchronization.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
BOOTSTRAP_RPC_ENV = "ANIMICA_BOOTSTRAP_RPC_URL"


async def rpc_call(
    method: str,
    params: Optional[List[Any]] = None,
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
        response = await client.post(rpc_url, json=payload)
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
) -> tuple[str, Optional[str]]:
    """Resolve target and bootstrap RPC endpoints."""
    net_cfg = load_network_config()
    target = rpc_url or os.environ.get(RPC_ENV) or net_cfg.rpc_url
    bootstrap = bootstrap_rpc or os.environ.get(BOOTSTRAP_RPC_ENV) or net_cfg.bootstrap_url
    target = target.strip()
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
        "node.syncStatus",
        "sync.status",
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


def _fetch_bootstrap_seeds(net_cfg, bootstrap_url: Optional[str]) -> tuple[list[str], list[str]]:
    """Fetch seed peers from bootstrap RPC or static defaults."""

    seeds: list[str] = []
    fetch_errors: list[str] = []

    if bootstrap_url:
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
        seeds = get_seed_nodes(net_cfg.name)

    return list(dict.fromkeys(seeds)), fetch_errors


def _seed_local_peerstores(
    net_cfg,
    *,
    target_rpc_url: str,
    bootstrap_url: Optional[str],
    quiet: bool = False,
) -> tuple[int, bool, list[str]]:
    """Persist bootstrap seeds locally and push them into a running node."""

    seeds, fetch_errors = _fetch_bootstrap_seeds(net_cfg, bootstrap_url)
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
    try:
        import_resp = asyncio.run(rpc_call("p2p.importPeers", [seeds], rpc_url=target_rpc_url))
        rpc_added, rpc_error = _rpc_operation_succeeded(import_resp)
        if not rpc_added:
            rpc_error = rpc_error or "p2p.importPeers did not report success"
    except Exception as exc:
        rpc_error = str(exc)

    if not quiet:
        if stored:
            typer.secho(f"✓ Added {stored} seed(s) to local peer store", fg=typer.colors.GREEN)
        if rpc_added:
            typer.secho("✓ Seeds imported into running node", fg=typer.colors.GREEN)
        else:
            typer.secho("⚠ Could not push seeds into running node", fg=typer.colors.YELLOW)
            if rpc_error:
                typer.echo(f"  Reason: {rpc_error}")
            typer.echo("  The node will read the persisted peer store on next start.")

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
    if note:
        payload["note"] = note

    state_path.write_text(json.dumps(payload, indent=2))


async def _trigger_sync(rpc_url: str) -> bool:
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
        "sync.force",
        "sync.start",
        "node.startSync",
        "sync.trigger",
        "p2p.sync",
    ]
    
    for method in methods_to_try:
        try:
            result = await rpc_call(method, [], rpc_url=rpc_url, timeout=DEFAULT_RPC_TIMEOUT)
            if _trigger_succeeded(result):
                return True
        except Exception:
            continue
    
    return False


@app.command(name="status")
def sync_status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    bootstrap_rpc: Optional[str] = typer.Option(
        None,
        "--bootstrap-rpc",
        help="Bootstrap RPC endpoint for discovery/seed info",
        envvar=BOOTSTRAP_RPC_ENV,
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
    url, bootstrap_url = _resolve_sync_endpoints(rpc_url, bootstrap_rpc)
    net_cfg = load_network_config()
    peer_count_error: Optional[Exception] = None
    bootstrap_source = "live"
    
    try:
        # Gather all information concurrently
        async def gather_info():
            return await asyncio.gather(
                _get_head_info(url),
                _get_sync_status(url),
                _get_peer_count(url),
                _get_peers(url),
                _get_bootstrap_head_info(bootstrap_url),
                return_exceptions=True
            )
        
        head_info, sync_status, peer_count_result, peers, bootstrap_head = asyncio.run(gather_info())
        
        # Handle exceptions
        if isinstance(head_info, Exception):
            head_info = None
        if isinstance(sync_status, Exception):
            sync_status = None
        peer_count_error: Optional[Exception] = None
        if isinstance(peer_count_result, Exception):
            peer_count_error = peer_count_result
            peer_count_result = None
        if isinstance(peers, Exception):
            peer_count_error = peer_count_error or peers
            peers = []
        if isinstance(bootstrap_head, Exception):
            bootstrap_head = None

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
    
    is_syncing = False
    sync_progress = None
    target_height = None
    
    if sync_status:
        is_syncing = sync_status.get("syncing", False)
        if isinstance(is_syncing, dict):
            # Some nodes return syncing status as a dict
            sync_progress = is_syncing
            is_syncing = True
        elif is_syncing:
            sync_progress = sync_status
        
        if sync_progress:
            target_height = (
                sync_progress.get("highestBlock") or
                sync_progress.get("targetHeight") or
                sync_progress.get("target_height")
            )
    
    peer_count: Optional[int] = None
    if isinstance(peer_count_result, int):
        peer_count = peer_count_result
    elif peers:
        peer_count = len(peers)
    
    # JSON output
    peer_error_msg = None
    if peer_count_result is None and peer_count is None and "peer_count_result" in locals():
        peer_error_msg = "RPC peer methods unavailable"
    if peer_count_error:
        peer_error_msg = str(peer_count_error)

    rpc_unavailable = head_info is None and sync_status is None and peer_error_msg
    if rpc_unavailable and not peers:
        typer.secho(f"RPC unavailable at {url}: {peer_error_msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if json_output:
        output = {
            "rpc_url": url,
            "bootstrap_rpc": bootstrap_url,
            "chain_id": chain_id,
            "height": height,
            "bootstrap_height": bootstrap_height,
            "head_hash": head_hash,
            "syncing": is_syncing,
            "peer_count": peer_count,
        }
        if sync_progress:
            output["sync_progress"] = sync_progress
        if peer_error_msg:
            output["peer_error"] = peer_error_msg
        if verbose and peers:
            output["peers"] = peers
        typer.echo(_pretty(output))
        return
    
    # Human-readable output
    typer.secho("\n╔═══════════════════════════════════════════════════════╗", fg=typer.colors.CYAN)
    typer.secho("║        Blockchain Synchronization Status              ║", fg=typer.colors.CYAN, bold=True)
    typer.secho("╚═══════════════════════════════════════════════════════╝", fg=typer.colors.CYAN)
    typer.echo()
    
    # Connection info
    typer.echo(f"Target RPC:    {url}")
    bootstrap_label = bootstrap_url or "not configured"
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
        typer.echo(f"  Height:    Unknown")
    
    if head_hash:
        typer.echo(f"  Hash:      {head_hash}")
    typer.echo()
    
    # Sync status
    typer.secho("Sync Status:", fg=typer.colors.BRIGHT_BLUE, bold=True)
    if is_syncing:
        typer.secho("  Status:    SYNCING", fg=typer.colors.YELLOW, bold=True)
        if target_height is not None and height is not None:
            progress_pct = (height / target_height * 100) if target_height > 0 else 0
            typer.echo(f"  Progress:  {height} / {target_height} ({progress_pct:.1f}%)")
            remaining = target_height - height
            typer.echo(f"  Remaining: {remaining} blocks")
    else:
        if height is not None and height > 0:
            typer.secho("  Status:    SYNCHRONIZED", fg=typer.colors.GREEN, bold=True)
        elif height == 0 and bootstrap_height and bootstrap_height > 0:
            typer.secho(
                f"  Status:    NOT SYNCED (local=0, network={bootstrap_height})",
                fg=typer.colors.RED,
                bold=True,
            )
        elif height == 0:
            typer.secho("  Status:    IDLE (genesis)", fg=typer.colors.YELLOW)
        else:
            typer.secho("  Status:    IDLE (no blocks)", fg=typer.colors.YELLOW)
    typer.echo()
    
    # Peer info
    typer.secho("Network:", fg=typer.colors.BRIGHT_BLUE, bold=True)
    if peer_count is None:
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
    elif is_syncing:
        typer.secho("💡 Syncing in progress... Check back later or run:", fg=typer.colors.CYAN)
        typer.echo("   animica sync status")
    elif height == 0 and bootstrap_height and bootstrap_height > 0:
        typer.secho("⚠ Node is not synced yet. Wait for peers or run sync force.", fg=typer.colors.YELLOW)
    else:
        typer.secho("✓ Node is synchronized with the network", fg=typer.colors.GREEN)


@app.command(name="force")
def force_sync(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    bootstrap_rpc: Optional[str] = typer.Option(
        None,
        "--bootstrap-rpc",
        help="Bootstrap RPC endpoint for discovery/seed info",
        envvar=BOOTSTRAP_RPC_ENV,
    ),
    timeout: int = typer.Option(
        300, "--timeout", help="Maximum time to wait for sync to start (seconds)"
    ),
    check_interval: int = typer.Option(
        5, "--check-interval", help="How often to check sync progress (seconds)"
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
    """
    net_cfg = load_network_config()
    url, bootstrap_url = _resolve_sync_endpoints(rpc_url, bootstrap_rpc)

    bootstrap_host = urlparse(bootstrap_url).hostname if bootstrap_url else None
    target_host = urlparse(url).hostname if url else None

    if bootstrap_url and bootstrap_host and target_host and bootstrap_host != target_host:
        _seed_local_peerstores(net_cfg, target_rpc_url=url, bootstrap_url=bootstrap_url, quiet=True)

    typer.secho("\n🔄 Forcing blockchain synchronization...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Target RPC:    {url}")
    typer.echo(f"Bootstrap RPC: {bootstrap_url or 'not configured'}")
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

            typer.echo("Auto-bootstrapping peers from configured seeds...")
            stored, rpc_added, _ = _seed_local_peerstores(
                net_cfg,
                target_rpc_url=url,
                bootstrap_url=bootstrap_url,
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
    triggered = asyncio.run(_trigger_sync(url))
    
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
    
    with typer.progressbar(
        length=timeout,
        label="Waiting for sync progress",
        show_eta=True,
    ) as progress:
        elapsed = 0
        while elapsed < timeout:
            time.sleep(min(check_interval, timeout - elapsed))
            elapsed = int(time.time() - start_time)
            progress.update(check_interval)
            
            try:
                head_info = asyncio.run(_get_head_info(url))
                current_height = _extract_height(head_info) or 0
                try:
                    peers = asyncio.run(_get_peers(url))
                except Exception:
                    peers = []
                _persist_connected_peers(net_cfg, peers, quiet=True)
                _persist_sync_state(net_cfg, rpc_url=url, head_info=head_info, peers=peers)
                
                if current_height > last_height:
                    blocks_synced = current_height - last_height
                    typer.echo(
                        f"\n✓ Progress: height {current_height} "
                        f"(+{blocks_synced} blocks)",
                        nl=False
                    )
                    last_height = current_height
                    stall_count = 0
                else:
                    stall_count += 1
                    if stall_count >= max_stalls:
                        typer.echo(
                            f"\n⚠ No progress for {stall_count * check_interval} seconds",
                            nl=False
                        )
                        # Try to re-import seeds and retrigger sync to keep progress moving
                        added, _, _ = _seed_local_peerstores(
                            net_cfg,
                            target_rpc_url=url,
                            bootstrap_url=bootstrap_url,
                            quiet=True,
                        )
                        if added:
                            try:
                                peers = asyncio.run(_get_peers(url))
                                peer_count = len(peers)
                                typer.echo(f"\n✓ Re-seeded peers; {peer_count} connected")
                            except Exception:
                                typer.secho("\n⚠ Could not refresh peer list after re-seeding", fg=typer.colors.YELLOW, nl=False)
                        asyncio.run(_trigger_sync(url))
            except Exception:
                typer.echo("\n⚠ Connection error", nl=False)
    
    typer.echo()
    typer.echo()
    
    # Final status
    try:
        final_head = asyncio.run(_get_head_info(url))
        final_height = _extract_height(final_head) or 0
        
        blocks_synced = final_height - initial_height
        
        typer.secho("━" * 60, fg=typer.colors.CYAN)
        typer.secho("Final Status:", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  Initial height: {initial_height}")
        typer.echo(f"  Final height:   {final_height}")
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
            typer.echo("Check peer status with: animica peer list")
        try:
            peers = asyncio.run(_get_peers(url))
        except Exception:
            peers = []
        _persist_connected_peers(net_cfg, peers, quiet=True)
        _persist_sync_state(net_cfg, rpc_url=url, head_info=final_head, peers=peers, note="final")
    except Exception as e:
        typer.echo(f"Error checking final status: {e}", err=True)
        raise typer.Exit(code=1)
    
    typer.echo()
    typer.echo("Use 'animica sync status' to check current sync state.")


if __name__ == "__main__":
    app()
