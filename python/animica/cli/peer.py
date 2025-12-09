"""
Peer management CLI for Animica.

Provides commands to interact with the node's peer-to-peer network,
including listing peers, adding/removing peers, and viewing peer details.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import typer
from animica.config import load_network_config

app = typer.Typer(help="Manage P2P network peers.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
DEFAULT_STORE_PATH = Path.home() / ".animica" / "p2p" / "peers.json"
STORE_ENV = "ANIMICA_PEER_STORE"


async def rpc_call(
    method: str, params: Optional[List[Any]] = None, *, rpc_url: str
) -> Any:
    """Make a JSON-RPC call to the node."""
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
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


def _read_peer_store(store_path: Path) -> List[Dict[str, Any]]:
    """
    Read peers from local store, supporting both JSON and SQLite formats.
    
    Args:
        store_path: Path to peer store file
        
    Returns:
        List of peer dictionaries in standardized format
    """
    peers = []
    
    # Try reading as SQLite database first (peers.db)
    db_path = store_path.parent / "peers.db" if store_path.name == "peers.json" else store_path
    if db_path.exists() and db_path.suffix in [".db", ""]:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM peers ORDER BY last_seen DESC")
            for row in cursor.fetchall():
                # Get addresses for this peer
                addr_cursor = conn.execute(
                    "SELECT address FROM peer_addresses WHERE peer_id=? ORDER BY last_seen DESC",
                    (row["peer_id"],)
                )
                addrs = [addr_row["address"] for addr_row in addr_cursor.fetchall()]
                
                peer = {
                    "id": row["peer_id"],
                    "peer_id": row["peer_id"],
                    "addr": row["address"],
                    "address": row["address"],
                    "addrs": addrs,
                    "status": row["status"],
                    "last_seen": row["last_seen"],
                    "score": row["score"],
                }
                peers.append(peer)
            conn.close()
            return peers
        except (sqlite3.Error, KeyError):
            # Fall through to JSON
            pass
    
    # Try reading as JSON (peers.json)
    if store_path.exists():
        try:
            with store_path.open("r") as f:
                data = json.load(f)
            json_peers = data.get("peers", [])
            
            for jp in json_peers:
                # Convert JSON peer format to standardized format
                peer_id = jp.get("peer_id", "")
                addrs = jp.get("addrs", [])
                primary_addr = addrs[0] if addrs else "unknown"
                
                peer = {
                    "id": peer_id,
                    "peer_id": peer_id,
                    "addr": primary_addr,
                    "address": primary_addr,
                    "addrs": addrs,
                    "status": "connected" if jp.get("connected", False) else "disconnected",
                    "last_seen": jp.get("last_seen"),
                    "score": jp.get("score", 0.0),
                }
                peers.append(peer)
            return peers
        except (json.JSONDecodeError, IOError, KeyError):
            pass
    
    return peers


@app.command(name="list")
def list_peers(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    store: Optional[str] = typer.Option(
        None, "--store", help="Path to local peer store (fallback)", envvar=STORE_ENV
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show detailed peer information"
    ),
) -> None:
    """
    List all connected peers.

    Shows information about peers currently connected to the node,
    including their peer ID, address, status, and connection metrics.
    
    If RPC peer listing is unavailable, falls back to reading from
    local peer store (~/.animica/p2p/peers.json by default).

    Examples:
        animica peer list
        animica peer list --verbose
        animica peer list --rpc-url http://localhost:8545
        animica peer list --store ~/.animica/p2p/peers.json
    """
    url = _resolve_rpc_url(rpc_url)

    # Try different RPC method names that might be available
    methods_to_try = [
        "p2p.listPeers",
        "p2p.getPeers",
        "p2p.peers",
        "admin_peers",
        "net_peers",
    ]

    peers = None
    rpc_failed = False
    for method in methods_to_try:
        try:
            peers = asyncio.run(rpc_call(method, [], rpc_url=url))
            break
        except Exception:
            continue

    if peers is None:
        rpc_failed = True
        # Try fallback to local peer store
        store_path = Path(store) if store else DEFAULT_STORE_PATH
        
        # Check if store file exists (either .json or .db)
        db_path = store_path.parent / "peers.db" if store_path.name == "peers.json" else store_path
        store_exists = store_path.exists() or (db_path.exists() and db_path.suffix in [".db", ""])
        
        if not store_exists:
            typer.echo(
                "Error: Unable to retrieve peers. Node may not support peer listing RPC methods.",
                err=True,
            )
            typer.echo(
                f"\nNote: Ensure the node is running and RPC endpoint is accessible.",
                err=True,
            )
            typer.echo(
                f"      Or check local peer store at: {store_path}",
                err=True,
            )
            raise typer.Exit(code=1)
        
        peers = _read_peer_store(store_path)

    # Handle empty peer list
    if not peers or len(peers) == 0:
        typer.secho("No peers connected.", fg=typer.colors.YELLOW)
        if rpc_failed:
            typer.echo("\n(Showing peers from local peer store)")
        return

    # Display peers
    source_msg = " (from local peer store)" if rpc_failed else ""
    typer.secho(f"\nConnected Peers: {len(peers)}{source_msg}", fg=typer.colors.CYAN, bold=True)
    typer.echo()

    if verbose:
        # Detailed view
        typer.echo(_pretty(peers))
    else:
        # Summary view
        for i, peer in enumerate(peers, 1):
            peer_id = peer.get("id") or peer.get("peerId") or peer.get("peer_id") or "unknown"
            addr = peer.get("addr") or peer.get("address") or peer.get("multiaddr") or "unknown"
            status = peer.get("status") or peer.get("state") or "connected"

            typer.echo(f"{i}. Peer: {peer_id}")
            typer.echo(f"   Address: {addr}")
            typer.echo(f"   Status: {status}")
            typer.echo()


@app.command(name="add")
def add_peer(
    address: str = typer.Argument(..., help="Peer address (multiaddr or host:port)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """
    Add a peer to the node's peer list.

    Attempts to connect to the specified peer and add it to the node's
    active peer list.

    Examples:
        animica peer add /ip4/1.2.3.4/tcp/30303/p2p/QmPeerId...
        animica peer add 1.2.3.4:30303
    """
    url = _resolve_rpc_url(rpc_url)

    # Try different RPC method names
    methods_to_try = [
        ("p2p.addPeer", [address]),
        ("admin_addPeer", [address]),
        ("net_addPeer", [address]),
    ]

    success = False
    last_error = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            success = True
            break
        except Exception as e:
            last_error = e
            continue

    if success:
        typer.secho(f"✓ Successfully added peer: {address}", fg=typer.colors.GREEN, bold=True)
    else:
        typer.echo(
            f"Error: Failed to add peer '{address}'.",
            err=True,
        )
        if last_error:
            typer.echo(f"Last error: {last_error}", err=True)
        typer.echo(
            "\nNote: Ensure the address is valid and the node supports peer management.",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command(name="remove")
def remove_peer(
    peer_id: str = typer.Argument(..., help="Peer ID to remove"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """
    Remove a peer from the node's peer list.

    Disconnects from the specified peer and removes it from the active peer list.

    Examples:
        animica peer remove QmPeerId...
        animica peer remove 12D3KooWPeerId...
    """
    url = _resolve_rpc_url(rpc_url)

    # Try different RPC method names
    methods_to_try = [
        ("p2p.removePeer", [peer_id]),
        ("admin_removePeer", [peer_id]),
        ("net_removePeer", [peer_id]),
    ]

    success = False
    last_error = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            success = True
            break
        except Exception as e:
            last_error = e
            continue

    if success:
        typer.secho(f"✓ Successfully removed peer: {peer_id}", fg=typer.colors.GREEN, bold=True)
    else:
        typer.echo(
            f"Error: Failed to remove peer '{peer_id}'.",
            err=True,
        )
        if last_error:
            typer.echo(f"Last error: {last_error}", err=True)
        typer.echo(
            "\nNote: Ensure the peer ID is valid and the node supports peer management.",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command(name="info")
def peer_info(
    peer_id: str = typer.Argument(..., help="Peer ID to get information about"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """
    Show detailed information about a specific peer.

    Displays comprehensive details about a peer, including connection status,
    network metrics, and capabilities.

    Examples:
        animica peer info QmPeerId...
        animica peer info 12D3KooWPeerId...
    """
    url = _resolve_rpc_url(rpc_url)

    # Try different RPC method names
    methods_to_try = [
        ("p2p.getPeerInfo", [peer_id]),
        ("admin_peerInfo", [peer_id]),
        ("net_peerInfo", [peer_id]),
    ]

    peer_data = None
    last_error = None

    for method, params in methods_to_try:
        try:
            peer_data = asyncio.run(rpc_call(method, params, rpc_url=url))
            break
        except Exception as e:
            last_error = e
            continue

    if peer_data is None:
        # If specific peer info not available, try to find it in the peer list
        try:
            peers = asyncio.run(rpc_call("p2p.listPeers", [], rpc_url=url))
            if not peers:
                peers = asyncio.run(rpc_call("p2p.getPeers", [], rpc_url=url))
            
            if peers:
                for peer in peers:
                    pid = peer.get("id") or peer.get("peerId") or peer.get("peer_id")
                    if pid == peer_id:
                        peer_data = peer
                        break
        except Exception as e:
            last_error = e

    if peer_data is None:
        typer.echo(
            f"Error: Unable to retrieve information for peer '{peer_id}'.",
            err=True,
        )
        if last_error:
            typer.echo(f"Last error: {last_error}", err=True)
        typer.echo(
            "\nNote: Ensure the peer ID is valid and the node is connected to this peer.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Display peer information
    typer.secho(f"\nPeer Information: {peer_id}", fg=typer.colors.CYAN, bold=True)
    typer.echo()
    typer.echo(_pretty(peer_data))


if __name__ == "__main__":
    app()
