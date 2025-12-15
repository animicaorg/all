"""
Peer management CLI for Animica.

Provides commands to interact with the node's peer-to-peer network,
including listing peers, adding/removing peers, and viewing peer details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
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


def _resolve_store_paths(store_path: Path) -> tuple[Path, Path]:
    """
    Resolve both JSON and SQLite store paths.
    
    Args:
        store_path: User-provided path (can be .json, .db, or directory)
        
    Returns:
        Tuple of (json_path, db_path)
    """
    # If path is a directory, look for standard files inside
    if store_path.is_dir():
        return (store_path / "peers.json", store_path / "peers.db")
    
    # If path ends with .json, look for peers.db in same directory
    if store_path.suffix == ".json":
        return (store_path, store_path.parent / "peers.db")
    
    # If path ends with .db or has no extension, use as-is for db
    if store_path.suffix in [".db", ""]:
        return (store_path.parent / "peers.json", store_path)
    
    # Default: treat as JSON path
    return (store_path, store_path.with_suffix(".db"))


def _generate_peer_id(address: str) -> str:
    """
    Generate a peer ID from an address.
    
    For simple host:port addresses, we generate a deterministic ID.
    For multiaddr format with explicit peer ID, we extract it.
    
    Args:
        address: Peer address (multiaddr or host:port)
        
    Returns:
        Generated or extracted peer ID
    """
    # Check if address contains a peer ID in multiaddr format
    # Format: /ip4/x.x.x.x/tcp/port/p2p/PeerID or /ipfs/PeerID
    if "/p2p/" in address:
        parts = address.split("/p2p/")
        if len(parts) > 1:
            return parts[1].split("/")[0]
    if "/ipfs/" in address:
        parts = address.split("/ipfs/")
        if len(parts) > 1:
            return parts[1].split("/")[0]
    
    # Generate a deterministic peer ID from the address
    # Use first 32 chars of hex hash for adequate collision resistance
    hash_obj = hashlib.sha256(address.encode())
    return f"peer_{hash_obj.hexdigest()[:32]}"


def _write_peer_to_store(store_path: Path, peer_id: str, address: str) -> None:
    """
    Write a peer to the local JSON store.
    
    Creates or updates the peer store with the new peer entry.
    
    Args:
        store_path: Path to peer store file
        peer_id: Peer identifier
        address: Peer address
    """
    json_path, _ = _resolve_store_paths(store_path)
    
    # Ensure parent directory exists
    json_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Read existing data
    data = {"peers": []}
    if json_path.exists():
        try:
            with json_path.open("r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    
    # Find existing peer or create new entry
    peers = data.get("peers", [])
    existing_peer = None
    for peer in peers:
        if peer.get("peer_id") == peer_id:
            existing_peer = peer
            break
    
    if existing_peer:
        # Update existing peer
        if address not in existing_peer.get("addrs", []):
            existing_peer.setdefault("addrs", []).append(address)
        existing_peer["last_seen"] = time.time()
    else:
        # Add new peer
        peers.append({
            "peer_id": peer_id,
            "addrs": [address],
            "score": 0.0,
            "last_seen": time.time(),
            "connected": False,
        })
    
    # Write back to file
    data["peers"] = peers
    with json_path.open("w") as f:
        json.dump(data, f, indent=2)


def _remove_peer_from_store(store_path: Path, peer_id: str) -> bool:
    """
    Remove a peer from the local JSON store.
    
    Args:
        store_path: Path to peer store file
        peer_id: Peer identifier to remove
        
    Returns:
        True if peer was found and removed, False otherwise
    """
    json_path, _ = _resolve_store_paths(store_path)
    
    if not json_path.exists():
        return False
    
    try:
        with json_path.open("r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    
    peers = data.get("peers", [])
    original_len = len(peers)
    
    # Filter out the peer to remove
    peers = [p for p in peers if p.get("peer_id") != peer_id]
    
    if len(peers) == original_len:
        return False  # Peer not found
    
    # Write back to file
    data["peers"] = peers
    with json_path.open("w") as f:
        json.dump(data, f, indent=2)
    
    return True


def _read_peer_store(store_path: Path) -> List[Dict[str, Any]]:
    """
    Read peers from local store, supporting both JSON and SQLite formats.
    
    Args:
        store_path: Path to peer store file
        
    Returns:
        List of peer dictionaries in standardized format
    """
    peers = []
    json_path, db_path = _resolve_store_paths(store_path)
    
    # Try reading as SQLite database first (peers.db)
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM peers ORDER BY last_seen DESC")
                for row in cursor.fetchall():
                    # Get addresses for this peer
                    addr_cursor = conn.execute(
                        "SELECT address FROM peer_addresses WHERE peer_id=? ORDER BY last_seen DESC",
                        (row["peer_id"],)
                    )
                    addrs = [addr_row["address"] for addr_row in addr_cursor.fetchall()]
                    
                    # Note: Duplicate fields (id/peer_id, addr/address) are intentional
                    # to maintain compatibility with different RPC response formats
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
                return peers
        except (sqlite3.Error, KeyError):
            # Fall through to JSON
            pass
    
    # Try reading as JSON (peers.json)
    if json_path.exists():
        try:
            with json_path.open("r") as f:
                data = json.load(f)
            json_peers = data.get("peers", [])
            
            for jp in json_peers:
                # Convert JSON peer format to standardized format
                peer_id = jp.get("peer_id", "")
                addrs = jp.get("addrs", [])
                primary_addr = addrs[0] if addrs else "unknown"
                
                # Note: Duplicate fields (id/peer_id, addr/address) are intentional
                # to maintain compatibility with different RPC response formats
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
        json_path, db_path = _resolve_store_paths(store_path)
        store_exists = json_path.exists() or db_path.exists()
        
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
    store: Optional[str] = typer.Option(
        None, "--store", help="Path to local peer store (fallback)", envvar=STORE_ENV
    ),
) -> None:
    """
    Add a peer to the node's peer list.

    Attempts to connect to the specified peer and add it to the node's
    active peer list via RPC. Also persists the peer to the local store
    as a backup, or falls back to store-only if RPC is unavailable.

    Examples:
        animica peer add /ip4/1.2.3.4/tcp/30303/p2p/QmPeerId...
        animica peer add 1.2.3.4:30303
        animica peer add 5.6.7.8:30333 --store ~/.animica/p2p/peers.json
    """
    url = _resolve_rpc_url(rpc_url)
    store_path = Path(store) if store else DEFAULT_STORE_PATH

    # Generate peer ID from address
    peer_id = _generate_peer_id(address)

    # Try different RPC method names
    methods_to_try = [
        ("p2p.addPeer", [address]),
        ("admin_addPeer", [address]),
        ("net_addPeer", [address]),
    ]

    rpc_success = False
    last_error = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            rpc_success = True
            break
        except Exception as e:
            last_error = e
            continue

    # Write to local store regardless of RPC success (as backup)
    try:
        _write_peer_to_store(store_path, peer_id, address)
        store_written = True
    except Exception as e:
        store_written = False
        if not rpc_success:
            # Only show store error if RPC also failed
            typer.echo(f"Warning: Failed to write to local store: {e}", err=True)

    if rpc_success:
        typer.secho(f"✓ Successfully added peer: {address}", fg=typer.colors.GREEN, bold=True)
        if store_written:
            typer.echo(f"  (Also saved to local peer store: {store_path})")
    elif store_written:
        # RPC failed but store succeeded - this is the fallback case
        typer.secho(
            f"✓ RPC unavailable, but peer saved to local store: {address}",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"  Peer ID: {peer_id}")
        typer.echo(f"  Store: {store_path}")
        typer.echo(
            "\nNote: The peer is saved locally. When the node starts or syncs,\n"
            "      it may attempt to connect to this peer."
        )
    else:
        # Both RPC and store failed
        typer.echo(
            f"Error: Failed to add peer '{address}'.",
            err=True,
        )
        if last_error:
            typer.echo(f"Last RPC error: {last_error}", err=True)
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
    store: Optional[str] = typer.Option(
        None, "--store", help="Path to local peer store (fallback)", envvar=STORE_ENV
    ),
) -> None:
    """
    Remove a peer from the node's peer list.

    Disconnects from the specified peer and removes it from the active peer list
    via RPC. Also removes the peer from the local store, or falls back to
    store-only removal if RPC is unavailable.

    Examples:
        animica peer remove QmPeerId...
        animica peer remove 12D3KooWPeerId...
        animica peer remove peer_abc123 --store ~/.animica/p2p/peers.json
    """
    url = _resolve_rpc_url(rpc_url)
    store_path = Path(store) if store else DEFAULT_STORE_PATH

    # Try different RPC method names
    methods_to_try = [
        ("p2p.removePeer", [peer_id]),
        ("admin_removePeer", [peer_id]),
        ("net_removePeer", [peer_id]),
    ]

    rpc_success = False
    last_error = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            rpc_success = True
            break
        except Exception as e:
            last_error = e
            continue

    # Also remove from local store
    store_removed = False
    try:
        store_removed = _remove_peer_from_store(store_path, peer_id)
    except Exception as e:
        if not rpc_success:
            # Only show store error if RPC also failed
            typer.echo(f"Warning: Failed to remove from local store: {e}", err=True)

    if rpc_success:
        typer.secho(f"✓ Successfully removed peer: {peer_id}", fg=typer.colors.GREEN, bold=True)
        if store_removed:
            typer.echo(f"  (Also removed from local peer store: {store_path})")
        elif store_path.exists():
            typer.echo(f"  (Peer not found in local store)")
    elif store_removed:
        # RPC failed but store removal succeeded - this is the fallback case
        typer.secho(
            f"✓ RPC unavailable, but peer removed from local store: {peer_id}",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"  Store: {store_path}")
    else:
        # Both RPC and store removal failed
        typer.echo(
            f"Error: Failed to remove peer '{peer_id}'.",
            err=True,
        )
        if last_error:
            typer.echo(f"Last RPC error: {last_error}", err=True)
        typer.echo(
            "\nNote: Ensure the peer ID is valid and exists in either the node or local store.",
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
