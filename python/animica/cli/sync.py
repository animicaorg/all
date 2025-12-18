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
    _write_peer_to_sqlite,
    _write_peer_to_store,
)
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.seeds import get_seed_nodes
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage blockchain synchronization.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"


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
        data = response.json()
    if "error" in data:
        error_info = data["error"]
        if isinstance(error_info, dict):
            error_msg = error_info.get("message", str(error_info))
        else:
            error_msg = str(error_info)
        raise RuntimeError(error_msg)
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or default."""
    return rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url


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


async def _trigger_sync(rpc_url: str) -> bool:
    """
    Trigger a sync operation on the node.
    
    Returns True if sync was triggered successfully, False otherwise.
    """
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
            # Consider any non-error response as success
            return True
        except Exception:
            continue
    
    return False


@app.command(name="status")
def sync_status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
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
    url = _resolve_rpc_url(rpc_url)
    peer_count_error: Optional[Exception] = None
    
    try:
        # Gather all information concurrently
        async def gather_info():
            return await asyncio.gather(
                _get_head_info(url),
                _get_sync_status(url),
                _get_peer_count(url),
                _get_peers(url),
                return_exceptions=True
            )
        
        head_info, sync_status, peer_count_result, peers = asyncio.run(gather_info())
        
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
        
    except Exception as e:
        typer.echo(f"Error: Unable to connect to node at {url}", err=True)
        typer.echo(f"Details: {e}", err=True)
        typer.echo(
            "\nHint: Ensure the node is running with: animica node status",
            err=True
        )
        raise typer.Exit(code=1)
    
    # Extract info
    height = None
    head_hash = None
    chain_id = None
    
    if head_info:
        height = head_info.get("height") or head_info.get("number") or head_info.get("blockNumber")
        head_hash = head_info.get("hash") or head_info.get("blockHash")
        chain_id = head_info.get("chainId") or head_info.get("chain_id")
    
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
            "chain_id": chain_id,
            "height": height,
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
    typer.echo(f"RPC URL:     {url}")
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
        if target_height and height:
            progress_pct = (height / target_height * 100) if target_height > 0 else 0
            typer.echo(f"  Progress:  {height} / {target_height} ({progress_pct:.1f}%)")
            remaining = target_height - height
            typer.echo(f"  Remaining: {remaining} blocks")
    else:
        if height is not None and height > 0:
            typer.secho("  Status:    SYNCHRONIZED", fg=typer.colors.GREEN, bold=True)
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
    else:
        typer.secho("✓ Node is synchronized with the network", fg=typer.colors.GREEN)


@app.command(name="force")
def force_sync(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
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
    url = _resolve_rpc_url(rpc_url)
    net_cfg = load_network_config()

    bootstrap_host = urlparse(net_cfg.bootstrap_url).hostname if net_cfg.bootstrap_url else None
    target_host = urlparse(url).hostname if url else None
    if bootstrap_host and target_host and bootstrap_host == target_host:
        typer.secho(
            "Using bootstrap RPC for discovery only. Seeding local peer store...",
            fg=typer.colors.YELLOW,
        )
        seeds: list[str] = []
        try:
            guard_bootstrap_rpc(
                url,
                allow_bootstrap_methods=True,
                method="net.getBootstrapSeeds",
                bootstrap_url=net_cfg.bootstrap_url,
            )
            resp = asyncio.run(rpc_call("net.getBootstrapSeeds", [], rpc_url=url))
            seeds = list((resp or {}).get("seeds") or [])
        except Exception as exc:
            typer.echo(f"Bootstrap RPC error: {exc}", err=True)

        if not seeds:
            seeds = get_seed_nodes(net_cfg.name)

        if seeds:
            store_path = Path(net_cfg.data_dir).expanduser() / "p2p" / "peers.json"
            for seed in list(dict.fromkeys(seeds)):
                peer_id = _generate_peer_id(seed)
                try:
                    _write_peer_to_store(store_path, peer_id, seed)
                    _write_peer_to_sqlite(store_path, peer_id, seed, direction="outbound")
                except Exception as exc:
                    typer.secho(f"⚠ Failed to persist {seed}: {exc}", fg=typer.colors.YELLOW)

            try:
                import_resp = asyncio.run(rpc_call("p2p.importPeers", [seeds], rpc_url=net_cfg.rpc_url))
                if not (isinstance(import_resp, dict) and import_resp.get("success")):
                    typer.secho("⚠ Could not import seeds via local RPC (node may be offline)", fg=typer.colors.YELLOW)
                else:
                    typer.secho("✓ Seeds sent to local node", fg=typer.colors.GREEN)
            except Exception as exc:
                typer.secho(f"⚠ Seed import via local RPC failed: {exc}", fg=typer.colors.YELLOW)
        else:
            typer.secho("⚠ No seeds available; cannot bootstrap peers", fg=typer.colors.YELLOW)

        url = net_cfg.rpc_url
        typer.echo(f"Switching to local RPC for sync: {url}")

    typer.secho("\n🔄 Forcing blockchain synchronization...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"RPC URL: {url}")
    typer.echo()
    
    # Get initial state
    try:
        initial_head = asyncio.run(_get_head_info(url))
        initial_height = initial_head.get("height", 0) if initial_head else 0
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
                current_height = head_info.get("height", 0) if head_info else 0
                
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
            except Exception:
                typer.echo("\n⚠ Connection error", nl=False)
    
    typer.echo()
    typer.echo()
    
    # Final status
    try:
        final_head = asyncio.run(_get_head_info(url))
        final_height = final_head.get("height", 0) if final_head else 0
        
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
    except Exception as e:
        typer.echo(f"Error checking final status: {e}", err=True)
        raise typer.Exit(code=1)
    
    typer.echo()
    typer.echo("Use 'animica sync status' to check current sync state.")


if __name__ == "__main__":
    app()
