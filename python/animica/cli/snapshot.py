"""
CLI commands for chain snapshot management.

Provides commands to create, list, verify, import, and manage snapshots
for fast chain synchronization.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from animica.config import load_network_config
from animica.cli.rpc_utils import candidate_rpc_urls
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage chain snapshots for fast sync.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"


async def rpc_call(
    method: str,
    params: Optional[list[Any] | dict[str, Any]] = None,
    *,
    rpc_url: str,
    timeout: Optional[float] = None,
) -> Any:
    """Make a JSON-RPC call."""
    resolved_timeout = resolve_timeout(
        "RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT
    )
    payload: dict[str, Any] = {
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
    """Resolve RPC URL from argument, environment, or config."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return DEFAULT_RPC_URL


async def _get_peers(rpc_url: str, timeout: Optional[float] = None) -> list[dict[str, Any]]:
    """Get list of connected peers from node."""
    methods_to_try = [
        "net.peers",
        "p2p.listPeers",
        "p2p.getPeers",
        "p2p.peers",
    ]
    
    for method in methods_to_try:
        try:
            peers = await rpc_call(method, [], rpc_url=rpc_url, timeout=timeout)
            if peers is not None:
                return peers if isinstance(peers, list) else []
        except Exception:
            continue
    
    return []


async def _query_peer_snapshots(
    peer_address: str,
    chain_id: Optional[int] = None,
    timeout: Optional[float] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Query a single peer for available snapshots.
    
    Returns:
        Tuple of (peer_rpc_url, snapshots_list)
    """
    # Construct RPC URL for the peer
    if not peer_address.startswith("http"):
        # Parse host:port format
        if ":" in peer_address:
            host, port = peer_address.rsplit(":", 1)
            # Assume RPC is on standard port 8545
            rpc_url = f"http://{host}:8545"
        else:
            rpc_url = f"http://{peer_address}:8545"
    else:
        rpc_url = peer_address
    
    # Ensure RPC URL has /rpc suffix
    if not rpc_url.endswith("/rpc"):
        rpc_url = f"{rpc_url}/rpc"
    
    params = {}
    if chain_id is not None:
        params["chain_id"] = chain_id
    
    try:
        result = await rpc_call("snapshot.list", params, rpc_url=rpc_url, timeout=timeout or 10.0)
        
        if result and result.get("success"):
            snapshots = result.get("snapshots", [])
            # Add source information to each snapshot (create copies to avoid side effects)
            enriched_snapshots = []
            for snap in snapshots:
                snap_copy = snap.copy()
                snap_copy["_source"] = peer_address
                snap_copy["_source_rpc"] = rpc_url
                enriched_snapshots.append(snap_copy)
            return rpc_url, enriched_snapshots
        else:
            return rpc_url, []
    except Exception:
        return rpc_url, []


async def _query_all_peers_for_snapshots(
    rpc_url: str,
    chain_id: Optional[int] = None,
    timeout: Optional[float] = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Query all connected peers for their available snapshots.
    
    Returns:
        Dictionary mapping peer RPC URLs to their snapshot lists
    """
    # Get list of connected peers
    peers = await _get_peers(rpc_url, timeout=timeout)
    
    if not peers:
        return {}
    
    # Extract peer addresses
    peer_addresses = []
    for peer in peers:
        addr = peer.get("remote") or peer.get("address") or peer.get("addr")
        if addr:
            peer_addresses.append(addr)
    
    if not peer_addresses:
        return {}
    
    # Query each peer in parallel
    tasks = [
        _query_peer_snapshots(addr, chain_id, timeout)
        for addr in peer_addresses
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Collect successful results and log errors
    snapshots_by_peer = {}
    for result in results:
        if isinstance(result, Exception):
            # Log exception but continue processing other peers
            # (could add logging here if needed)
            continue
        if isinstance(result, tuple) and len(result) == 2:
            peer_rpc, snapshots = result
            if snapshots:
                snapshots_by_peer[peer_rpc] = snapshots
    
    return snapshots_by_peer


@app.command("create")
def create(
    height: Optional[int] = typer.Option(
        None, "--height", "-h", help="Checkpoint height (default: current head)"
    ),
    compress: bool = typer.Option(
        True, "--compress/--no-compress", help="Compress snapshot chunks"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Create a chain snapshot at the specified checkpoint height.
    
    If height is not specified, creates a snapshot at the current chain head.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        typer.echo(f"Creating snapshot at height {height or 'current head'}...")
        
        params = {"compress": compress}
        if height is not None:
            params["height"] = height
        
        result = asyncio.run(
            rpc_call("snapshot.create", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo("✅ Snapshot created successfully!")
        typer.echo(f"  Chain ID: {result['chain_id']}")
        typer.echo(f"  Height: {result['checkpoint_height']}")
        typer.echo(f"  Hash: {result['checkpoint_hash']}")
        typer.echo(f"  Blocks: {result['blocks_count']}")
        typer.echo(f"  Accounts: {result['accounts_count']}")
        typer.echo(f"  Storage keys: {result['storage_keys_count']}")
        typer.echo(f"  Elapsed: {result['elapsed_seconds']}s")
        typer.echo(f"  Path: {result['path']}")
        
    except Exception as e:
        typer.echo(f"❌ Error creating snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("list")
def list_snapshots(
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Filter by chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    from_peers: bool = typer.Option(
        False, "--from-peers", help="Query connected peers for their snapshots"
    ),
    local_only: bool = typer.Option(
        False, "--local-only", help="Show only local snapshots without querying peers"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    List all available snapshots.
    
    By default, shows local snapshots AND the highest available snapshot from peers.
    Use --from-peers to see all peer snapshots, or --local-only to skip peer discovery.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        if from_peers:
            # Query all connected peers for snapshots
            typer.echo("Querying connected peers for snapshots...")
            snapshots_by_peer = asyncio.run(
                _query_all_peers_for_snapshots(url, chain_id, timeout)
            )
            
            if not snapshots_by_peer:
                typer.echo("No snapshots found on connected peers.")
                typer.echo("\n💡 Tips:")
                typer.echo("  - Ensure you have peers connected (animica peer list)")
                typer.echo("  - Peers must have snapshots available")
                typer.echo("  - Try querying the local node without --from-peers")
                return
            
            # Flatten all snapshots
            all_snapshots = []
            for peer_rpc, snapshots in snapshots_by_peer.items():
                all_snapshots.extend(snapshots)
            
            # Sort by chain_id and height (descending)
            all_snapshots.sort(key=lambda s: (s["chain_id"], -s["checkpoint_height"]))
            
            if json_output:
                typer.echo(json.dumps(all_snapshots, indent=2))
                return
            
            typer.echo(f"\nFound {len(all_snapshots)} snapshot(s) from {len(snapshots_by_peer)} peer(s):\n")
            
            for snap in all_snapshots:
                typer.echo(f"Chain {snap['chain_id']} - Height {snap['checkpoint_height']}")
                typer.echo(f"  Hash: {snap['checkpoint_hash']}")
                typer.echo(f"  Blocks: {snap['blocks_count']}")
                typer.echo(f"  Accounts: {snap['accounts_count']}")
                typer.echo(f"  Size: {snap['size_mb']:.2f} MB")
                typer.echo(f"  Source: {snap.get('_source', 'unknown')}")
                typer.echo("")
            
            # Show summary by peer
            typer.echo("\nSnapshots by peer:")
            for peer_rpc, snapshots in snapshots_by_peer.items():
                heights = [s['checkpoint_height'] for s in snapshots]
                typer.echo(f"  {peer_rpc}: {len(snapshots)} snapshot(s) at heights {heights}")
        else:
            # Query local node first
            params = {}
            if chain_id is not None:
                params["chain_id"] = chain_id
            
            result = asyncio.run(
                rpc_call("snapshot.list", params, rpc_url=url, timeout=timeout)
            )
            
            if not result.get("success"):
                typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
                raise typer.Exit(code=1)
            
            local_snapshots = result.get("snapshots", [])
            
            # Also query peers for the highest available snapshot (unless --local-only)
            highest_peer_snapshot = None
            if not local_only:
                try:
                    snapshots_by_peer = asyncio.run(
                        _query_all_peers_for_snapshots(url, chain_id, timeout)
                    )
                    
                    if snapshots_by_peer:
                        # Flatten all peer snapshots
                        all_peer_snapshots = []
                        for peer_rpc, snapshots in snapshots_by_peer.items():
                            all_peer_snapshots.extend(snapshots)
                        
                        # Find the highest peer snapshot
                        if all_peer_snapshots:
                            highest_peer_snapshot = max(
                                all_peer_snapshots, 
                                key=lambda s: s["checkpoint_height"]
                            )
                except Exception:
                    # Silently ignore peer query errors in default mode
                    pass
            
            # Prepare output
            if json_output:
                output_data = {
                    "local_snapshots": local_snapshots,
                    "highest_peer_snapshot": highest_peer_snapshot
                }
                typer.echo(json.dumps(output_data, indent=2))
                return
            
            # Display local snapshots
            if not local_snapshots:
                typer.echo("No snapshots found on local node.")
            else:
                typer.echo(f"Found {len(local_snapshots)} local snapshot(s):\n")
                
                for snap in local_snapshots:
                    typer.echo(f"Chain {snap['chain_id']} - Height {snap['checkpoint_height']}")
                    typer.echo(f"  Hash: {snap['checkpoint_hash']}")
                    typer.echo(f"  Blocks: {snap['blocks_count']}")
                    typer.echo(f"  Accounts: {snap['accounts_count']}")
                    typer.echo(f"  Size: {snap['size_mb']:.2f} MB")
                    typer.echo(f"  Path: {snap['path']}")
                    typer.echo("")
            
            # Display highest peer snapshot if available
            if highest_peer_snapshot:
                typer.echo("🌐 Highest snapshot from connected peers:\n")
                typer.echo(f"Chain {highest_peer_snapshot['chain_id']} - Height {highest_peer_snapshot['checkpoint_height']}")
                typer.echo(f"  Hash: {highest_peer_snapshot['checkpoint_hash']}")
                typer.echo(f"  Blocks: {highest_peer_snapshot['blocks_count']}")
                typer.echo(f"  Accounts: {highest_peer_snapshot['accounts_count']}")
                typer.echo(f"  Size: {highest_peer_snapshot['size_mb']:.2f} MB")
                typer.echo(f"  Source: {highest_peer_snapshot.get('_source', 'unknown')}")
                typer.echo("")
                
                # Check if peer snapshot is higher than local
                local_max_height = max([s['checkpoint_height'] for s in local_snapshots]) if local_snapshots else 0
                if highest_peer_snapshot['checkpoint_height'] > local_max_height:
                    typer.echo("💡 A higher snapshot is available from peers for faster sync!")
                    typer.echo("   The node will automatically use it during sync if ANIMICA_SNAPSHOT_SYNC_ENABLED=true")
                    typer.echo("")
            elif not local_only:
                typer.echo("\n💡 No snapshots found on connected peers.")
                
            # Show tips if no snapshots at all
            if not local_snapshots and not highest_peer_snapshot:
                typer.echo("\n💡 Tips:")
                typer.echo("  - Create snapshots with: animica snapshot create")
                typer.echo("  - Query all peer snapshots: animica snapshot list --from-peers")
                typer.echo("  - Connect to more peers: animica peer add <address>")
        
    except Exception as e:
        typer.echo(f"❌ Error listing snapshots: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("get")
def get(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Get manifest for a specific snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.get", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        manifest = result.get("manifest", {})
        typer.echo(json.dumps(manifest, indent=2))
        
    except Exception as e:
        typer.echo(f"❌ Error getting snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("verify")
def verify(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Verify the integrity of a snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        typer.echo(f"Verifying snapshot at height {height}...")
        
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.verify", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        if result.get("valid"):
            typer.echo("✅ Snapshot is valid")
        else:
            typer.echo("❌ Snapshot is invalid:")
            for error in result.get("errors", []):
                typer.echo(f"  - {error}")
            raise typer.Exit(code=1)
        
    except Exception as e:
        typer.echo(f"❌ Error verifying snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("import")
def import_snapshot(
    path: str = typer.Argument(..., help="Path to snapshot directory"),
    verify_hashes: bool = typer.Option(
        True, "--verify/--no-verify", help="Verify chunk hashes"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds (default: 600)"
    ),
):
    """
    Import a snapshot from a directory.
    
    WARNING: This will overwrite existing chain data!
    """
    url = _resolve_rpc_url(rpc_url)
    
    # Use longer default timeout for import
    if timeout is None:
        timeout = 600.0
    
    try:
        typer.echo(f"⚠️  WARNING: This will overwrite existing chain data!")
        if not typer.confirm("Are you sure you want to continue?"):
            raise typer.Exit(code=0)
        
        typer.echo(f"Importing snapshot from {path}...")
        
        result = asyncio.run(
            rpc_call(
                "snapshot.import",
                {"path": path, "verify_hashes": verify_hashes},
                rpc_url=url,
                timeout=timeout,
            )
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo("✅ Snapshot imported successfully!")
        typer.echo(f"  Chain ID: {result['chain_id']}")
        typer.echo(f"  Height: {result['checkpoint_height']}")
        typer.echo(f"  Hash: {result['checkpoint_hash']}")
        typer.echo(f"  Blocks: {result['blocks_count']}")
        typer.echo(f"  Accounts: {result['accounts_count']}")
        typer.echo(f"  Elapsed: {result['elapsed_seconds']}s")
        
    except Exception as e:
        typer.echo(f"❌ Error importing snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("delete")
def delete(
    height: int = typer.Argument(..., help="Checkpoint height"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Delete a snapshot.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        if not force:
            if not typer.confirm(f"Delete snapshot at height {height}?"):
                raise typer.Exit(code=0)
        
        params = {"height": height}
        if chain_id is not None:
            params["chain_id"] = chain_id
        
        result = asyncio.run(
            rpc_call("snapshot.delete", params, rpc_url=url, timeout=timeout)
        )
        
        if not result.get("success"):
            typer.echo(f"❌ Error: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(code=1)
        
        typer.echo(f"✅ {result.get('message', 'Snapshot deleted')}")
        
    except Exception as e:
        typer.echo(f"❌ Error deleting snapshot: {e}", err=True)
        raise typer.Exit(code=1)


@app.command("discover")
def discover(
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", "-c", help="Filter by chain ID"
    ),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", envvar=RPC_ENV, help="RPC endpoint URL"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON"
    ),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="RPC timeout in seconds"
    ),
):
    """
    Discover the best available snapshot from connected peers.
    
    Queries all connected peers and shows the highest available snapshot,
    which is the best candidate for fast sync.
    """
    url = _resolve_rpc_url(rpc_url)
    
    try:
        typer.echo("🔍 Discovering snapshots from connected peers...")
        
        snapshots_by_peer = asyncio.run(
            _query_all_peers_for_snapshots(url, chain_id, timeout)
        )
        
        if not snapshots_by_peer:
            typer.echo("\n❌ No snapshots found on connected peers.")
            typer.echo("\n💡 Troubleshooting:")
            typer.echo("  1. Check peer connections: animica peer list")
            typer.echo("  2. Ensure peers have snapshots: they must create them first")
            typer.echo("  3. Check peer RPC accessibility")
            typer.echo("  4. Try connecting to more peers: animica peer add <address>")
            raise typer.Exit(code=1)
        
        # Flatten all snapshots
        all_snapshots = []
        for peer_rpc, snapshots in snapshots_by_peer.items():
            all_snapshots.extend(snapshots)
        
        if not all_snapshots:
            typer.echo("\n❌ No snapshots found.")
            raise typer.Exit(code=1)
        
        # Find the best snapshot (highest height)
        best_snapshot = max(all_snapshots, key=lambda s: s["checkpoint_height"])
        
        if json_output:
            typer.echo(json.dumps(best_snapshot, indent=2))
            return
        
        typer.echo(f"\n✅ Found {len(all_snapshots)} total snapshot(s) from {len(snapshots_by_peer)} peer(s)")
        typer.echo(f"\n🏆 Best snapshot (highest height):")
        typer.echo(f"  Chain ID:         {best_snapshot['chain_id']}")
        typer.echo(f"  Height:           {best_snapshot['checkpoint_height']}")
        typer.echo(f"  Hash:             {best_snapshot['checkpoint_hash']}")
        typer.echo(f"  Blocks:           {best_snapshot['blocks_count']}")
        typer.echo(f"  Accounts:         {best_snapshot['accounts_count']}")
        typer.echo(f"  Size:             {best_snapshot['size_mb']:.2f} MB")
        typer.echo(f"  Source Peer:      {best_snapshot.get('_source', 'unknown')}")
        typer.echo(f"  Source RPC:       {best_snapshot.get('_source_rpc', 'unknown')}")
        
        typer.echo("\n💡 To use this snapshot for fast sync:")
        typer.echo("  1. Ensure ANIMICA_SNAPSHOT_SYNC_ENABLED=true (default)")
        typer.echo("  2. Restart your node - it will auto-discover and use this snapshot")
        typer.echo("  3. Or manually query: animica snapshot get " + str(best_snapshot['checkpoint_height']))
        
    except Exception as e:
        if isinstance(e, typer.Exit):
            raise
        typer.echo(f"❌ Error discovering snapshots: {e}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
