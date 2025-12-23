"""
Peer management CLI for Animica.

Provides commands to interact with the node's peer-to-peer network,
including listing peers, adding/removing peers, and viewing peer details.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import typer
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.cli.rpc_utils import is_local_rpc_url
from animica.config import load_network_config
from animica.seeds import get_default_ports, get_seed_nodes
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

app = typer.Typer(help="Manage P2P network peers.")

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
_DEFAULT_DATA_DIR = Path(load_network_config().data_dir).expanduser()
DEFAULT_STORE_PATH = _DEFAULT_DATA_DIR / "p2p" / "peers.json"
STORE_ENV = "ANIMICA_PEER_STORE"


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


async def _rpc_call_with_error(
    method: str,
    params: Optional[List[Any]] = None,
    *,
    rpc_url: str,
    timeout: Optional[float] = None,
) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Make a JSON-RPC call and return a (result, error) tuple without raising on RPC errors."""
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
    error = data.get("error")
    if error:
        if isinstance(error, dict):
            return None, error
        return None, {"message": str(error)}
    return data.get("result"), None


def _rpc_error_message(error: Optional[Dict[str, Any]]) -> Optional[str]:
    if not error:
        return None
    message = error.get("message") or error.get("error")
    return str(message) if message is not None else str(error)


def _is_method_not_found_error(error: Optional[Dict[str, Any]]) -> bool:
    if not error:
        return False
    if error.get("code") == -32601:
        return True
    message = str(error.get("message", "")).lower()
    return "method not found" in message


def _is_bootstrap_only_error(error: Optional[Dict[str, Any]]) -> bool:
    if not error:
        return False
    if error.get("code") == -32070:
        return True
    message = str(error.get("message", "")).lower()
    return "bootstrap_only" in message or "bootstrap-only" in message


def _probe_rpc_for_peer_injection(rpc_url: str) -> tuple[bool, bool, Optional[str]]:
    """Return (running, policy_blocked, error_message)."""
    try:
        _result, error = asyncio.run(_rpc_call_with_error("node.ping", [], rpc_url=rpc_url))
    except Exception as exc:
        return False, False, str(exc)

    if error:
        if _is_bootstrap_only_error(error):
            return True, True, _rpc_error_message(error)
        if _is_method_not_found_error(error):
            try:
                _result, error = asyncio.run(_rpc_call_with_error("chain.getHead", [], rpc_url=rpc_url))
            except Exception as exc:
                return False, False, str(exc)
            if error:
                if _is_bootstrap_only_error(error):
                    return True, True, _rpc_error_message(error)
                return False, False, _rpc_error_message(error)
        else:
            return False, False, _rpc_error_message(error)

    try:
        _, error = asyncio.run(_rpc_call_with_error("p2p.listPeers", [], rpc_url=rpc_url))
    except Exception as exc:
        return False, False, str(exc)

    if error and _is_bootstrap_only_error(error):
        return True, True, _rpc_error_message(error)

    return True, False, None


def _resolve_rpc_url(
    rpc_url: Optional[str],
    *,
    allow_remote_rpc: bool = False,
    method: str | None = None,
) -> str:
    """Resolve RPC URL from option, env, or default and enforce bootstrap guard."""
    resolved = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(resolved, allow_remote=allow_remote_rpc, method=method)
    return resolved


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


def _normalize_to_multiaddr(address: str) -> str:
    """
    Convert a user-provided address into a multiaddr-like string that the P2P stack understands.

    Examples:
        "1.2.3.4:30333" -> "/ip4/1.2.3.4/tcp/30333"
        "node.animica.org:30333" -> "/dns4/node.animica.org/tcp/30333"
        "/ip4/1.2.3.4/tcp/30333" -> (returned as-is)
    """
    # Already a multiaddr
    if address.startswith("/"):
        return address

    # Strip known url-like prefixes (tcp://, quic://, ws://)
    if "://" in address:
        address = address.split("://", 1)[1]

    host, port = _parse_address(address)

    # If no port or host, leave as-is (caller is responsible for filling)
    if port is None or not host:
        return address

    # Decide ip4/ip6 vs dns
    try:
        ip_obj = ipaddress.ip_address(host)
        ip_tag = "ip6" if ip_obj.version == 6 else "ip4"
    except ValueError:
        ip_tag = "dns4"

    return f"/{ip_tag}/{host}/tcp/{port}"


def _write_peer_to_sqlite(store_path: Path, peer_id: str, address: str, direction: Optional[str] = None) -> None:
    """
    Persist a peer into the SQLite peer store used by the P2P stack.

    This ensures peers added via the CLI can be dialed automatically by the node
    without requiring a successful RPC call.
    """
    try:
        from p2p.peer.peerstore import PeerStore
    except Exception:
        # If peerstore is unavailable, silently skip; JSON store still acts as fallback.
        return

    _, db_path = _resolve_store_paths(store_path)
    db_dir = db_path.parent
    db_dir.mkdir(parents=True, exist_ok=True)

    # Normalize to a multiaddr that NodeService/ConnectionManager can parse
    normalized = _normalize_to_multiaddr(address)

    store = PeerStore(db_path)
    store.add(peer_id=peer_id, addrs=[normalized], score=0.0, direction=direction)
    store.record_seen(peer_id, normalized)


def _rpc_operation_succeeded(result: Any) -> tuple[bool, Optional[str]]:
    """
    Determine whether an RPC response indicates success.

    Args:
        result: The "result" payload from a JSON-RPC response.

    Returns:
        Tuple of (success flag, error message if available).
    """
    if isinstance(result, dict):
        if "success" in result:
            return bool(result.get("success")), result.get("error") or result.get("message")
        if "result" in result and isinstance(result.get("result"), bool):
            return bool(result.get("result")), result.get("error") or result.get("message")
        for key in ("added", "connected", "removed"):
            if key in result:
                value = result.get(key)
                if isinstance(value, bool):
                    return bool(value), result.get("error") or result.get("message")
                if isinstance(value, (int, float)):
                    return value > 0, result.get("error") or result.get("message")
                if isinstance(value, (list, tuple, set)):
                    return len(value) > 0, result.get("error") or result.get("message")
        # Unknown dict payload - treat as failure to avoid false positives
        return False, result.get("error") or result.get("message") or "Unexpected RPC response"

    # Primitive responses (bool/int/str) - treat truthy as success
    return bool(result), None


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

    # Also persist to SQLite peer store so the node can autodial without RPC
    _write_peer_to_sqlite(store_path, peer_id, address, direction="outbound")


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

    # Also remove from SQLite store if present
    try:
        from p2p.peer.peerstore import PeerStore
        _, db_path = _resolve_store_paths(store_path)
        if db_path.exists():
            store = PeerStore(db_path)
            store.forget(peer_id)
    except Exception:
        # Fallback silently; JSON removal already succeeded
        pass

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
                    # Include direction if present in the database
                    if "direction" in row.keys() and row["direction"]:
                        peer["direction"] = row["direction"]
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


def _probe_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """
    Probe if a host:port is reachable.
    
    Args:
        host: Hostname or IP address
        port: Port number
        timeout: Connection timeout in seconds
        
    Returns:
        True if connection successful, False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        return result == 0
    except (socket.error, socket.timeout):
        return False
    finally:
        if sock:
            sock.close()


def _parse_address(address: str) -> Tuple[str, Optional[int]]:
    """
    Parse an address into host and port components.
    
    Supports formats:
    - host:port (e.g., "1.2.3.4:30303")
    - host (e.g., "1.2.3.4")
    - multiaddr (e.g., "/ip4/1.2.3.4/tcp/30303/p2p/PeerId")
    
    Args:
        address: Address to parse
        
    Returns:
        Tuple of (host, port) where port may be None if not specified
    """
    # Handle multiaddr format
    if address.startswith("/"):
        parts = address.split("/")
        host = None
        port = None
        
        # Find IP address
        for i, part in enumerate(parts):
            if part in ["ip4", "ip6"] and i + 1 < len(parts):
                host = parts[i + 1]
            elif part == "tcp" and i + 1 < len(parts):
                try:
                    port = int(parts[i + 1])
                except ValueError:
                    pass
        
        # If we couldn't parse host from multiaddr, return the full address as-is
        # This will let the caller handle the error appropriately
        if host is None:
            return (address, port)
        
        return (host, port)
    
    # Handle host:port format
    if ":" in address:
        parts = address.rsplit(":", 1)
        try:
            return (parts[0], int(parts[1]))
        except (ValueError, IndexError):
            return (address, None)
    
    # Just host
    return (address, None)


def _detect_port(host: str, probe: bool = False) -> Optional[int]:
    """
    Auto-detect the best port for a host.
    
    If probe is True, tries to connect to each port and returns the first
    working one. Otherwise, just returns the first default port.
    
    Args:
        host: Hostname or IP address
        probe: Whether to actually probe connectivity
        
    Returns:
        Port number if found, None otherwise
    """
    default_ports = get_default_ports()
    
    if not probe:
        # Just return the first default port without probing
        return default_ports[0] if default_ports else None
    
    # Probe each port to find a working one
    for port in default_ports:
        if _probe_port(host, port):
            return port
    
    return None


@app.command(name="list")
def list_peers(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
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
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.listPeers")

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
                "\nNote: Ensure the node is running and RPC endpoint is accessible.",
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
        if rpc_failed:
            typer.secho("No known peers in local peer store.", fg=typer.colors.YELLOW)
        else:
            typer.secho("No peers connected.", fg=typer.colors.YELLOW)
        return

    # Display peers
    if rpc_failed:
        source_msg = " (from local peer store)"
        header = f"Known Peers: {len(peers)}{source_msg}"
    else:
        header = f"Connected Peers: {len(peers)}"
    
    typer.secho(f"\n{header}", fg=typer.colors.CYAN, bold=True)
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
            direction = peer.get("direction")

            typer.echo(f"{i}. Peer: {peer_id}")
            typer.echo(f"   Address: {addr}")
            typer.echo(f"   Status: {status}")
            if direction:
                typer.echo(f"   Direction: {direction}")
            typer.echo()


@app.command(name="add")
def add_peer(
    address: str = typer.Argument(..., help="Peer address (multiaddr, host:port, or host)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    store: Optional[str] = typer.Option(
        None, "--store", help="Path to local peer store (fallback)", envvar=STORE_ENV
    ),
    probe: bool = typer.Option(
        False, "--probe", help="Probe connectivity before adding (auto-detect port if not specified)"
    ),
) -> None:
    """
    Add a peer to the node's peer list.

    Attempts to connect to the specified peer and add it to the node's
    active peer list via RPC. Also persists the peer to the local store
    as a backup, or falls back to store-only if RPC is unavailable.
    
    If port is not specified, will try common P2P ports (30333, 30303, 31333, 31334).
    Use --probe flag to test connectivity before adding.

    Examples:
        animica peer add /ip4/1.2.3.4/tcp/30303/p2p/QmPeerId...
        animica peer add 1.2.3.4:30303
        animica peer add 144.126.133.21 --probe
        animica peer add 5.6.7.8:30333 --store ~/.animica/p2p/peers.json
    """
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.addPeer")
    store_path = Path(store) if store else DEFAULT_STORE_PATH

    # Parse address to check if port is missing
    host, port = _parse_address(address)
    
    # If no port specified, try to detect one
    if port is None:
        if probe:
            typer.echo(f"Auto-detecting port for {host}...")
            detected_port = _detect_port(host, probe=True)
            if detected_port:
                typer.secho(f"✓ Found open port: {detected_port}", fg=typer.colors.GREEN)
                port = detected_port
                address = f"{host}:{port}"
            else:
                typer.secho(
                    f"✗ Could not find an open port on {host}. Tried: {', '.join(map(str, get_default_ports()))}",
                    fg=typer.colors.RED
                )
                typer.echo("Hint: Specify a port explicitly (e.g., host:port)")
                raise typer.Exit(code=1)
        else:
            # Use default port without probing
            default_port = _detect_port(host, probe=False)
            if default_port:
                port = default_port
                address = f"{host}:{port}"
                typer.echo(f"Using default port {port} for {host}")
    elif probe:
        # Port specified, but user wants to probe
        if _probe_port(host, port):
            typer.secho(f"✓ {host}:{port} is reachable", fg=typer.colors.GREEN)
        else:
            typer.secho(f"✗ Warning: {host}:{port} is not reachable", fg=typer.colors.YELLOW)
            if not typer.confirm("Continue anyway?"):
                raise typer.Exit(code=0)

    # Generate peer ID from address
    peer_id = _generate_peer_id(address)

    # Try different RPC method names
    methods_to_try = [
        ("p2p.addPeer", [address]),
        ("admin_addPeer", [address]),
        ("net_addPeer", [address]),
    ]

    rpc_success = False
    last_error: Optional[str] = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            method_success, method_error = _rpc_operation_succeeded(result)
            if method_success:
                rpc_success = True
                break
            last_error = method_error or f"{method} did not report success"
        except Exception as e:
            last_error = str(e)
            continue

    # Resolve storage paths for messaging
    json_store, db_store = _resolve_store_paths(store_path)

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
            typer.echo(f"  (Also saved to local peer store: {json_store} | db: {db_store})")
    elif store_written:
        # RPC failed but store succeeded - this is the fallback case
        reason = last_error or "RPC call did not succeed"
        typer.secho(
            f"✓ Peer saved to local store after RPC failure: {address}",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"  Reason: {reason}")
        typer.echo(f"  Peer ID: {peer_id}")
        typer.echo(f"  Store: {json_store} | db: {db_store}")
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
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
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
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.removePeer")
    store_path = Path(store) if store else DEFAULT_STORE_PATH

    # Try different RPC method names
    methods_to_try = [
        ("p2p.removePeer", [peer_id]),
        ("admin_removePeer", [peer_id]),
        ("net_removePeer", [peer_id]),
    ]

    rpc_success = False
    last_error: Optional[str] = None

    for method, params in methods_to_try:
        try:
            result = asyncio.run(rpc_call(method, params, rpc_url=url))
            method_success, method_error = _rpc_operation_succeeded(result)
            if method_success:
                rpc_success = True
                break
            last_error = method_error or f"{method} did not report success"
        except Exception as e:
            last_error = str(e)
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
            typer.echo("  (Peer not found in local store)")
    elif store_removed:
        # RPC failed but store removal succeeded - this is the fallback case
        reason = last_error or "RPC call did not succeed"
        typer.secho(
            f"✓ Peer removed from local store after RPC failure: {peer_id}",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        typer.echo(f"  Reason: {reason}")
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
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
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
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.getPeerInfo")

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


@app.command(name="bootstrap")
def bootstrap_peers(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    store: Optional[str] = typer.Option(
        None,
        "--peer-store",
        "--store",
        help="Path to local peer store (fallback)",
        envvar=STORE_ENV,
    ),
    network: Optional[str] = typer.Option(
        None, "--network", help="Network to bootstrap (defaults to current network)"
    ),
    probe: bool = typer.Option(
        False, "--probe", help="Probe connectivity before adding"
    ),
    push: Optional[bool] = typer.Option(
        None,
        "--push/--no-push",
        help="Push seeds into a running node (default: auto)",
    ),
    start_node: bool = typer.Option(
        False,
        "--start-node",
        help="Start a local node if it is not running, then push seeds",
    ),
) -> None:
    """
    Connect to bootstrap/seed nodes for the network.
    
    This command automatically adds known seed nodes for the specified network
    (or the currently configured network) to help bootstrap peer discovery.

    Examples:
        animica peer bootstrap
        animica peer bootstrap --network mainnet
        animica peer bootstrap --probe
    """
    net_cfg = load_network_config(network)
    store_base = Path(store).expanduser() if store else Path(net_cfg.data_dir).expanduser() / "p2p" / "peers.json"
    store_path = store_base

    bootstrap_url = rpc_url or net_cfg.bootstrap_url
    if rpc_url and not is_local_rpc_url(rpc_url):
        target_rpc = None
    else:
        target_rpc = rpc_url or net_cfg.rpc_url

    if start_node and not target_rpc:
        raise typer.BadParameter("--start-node requires a local --rpc-url endpoint")
    if start_node and push is False:
        raise typer.BadParameter("--start-node cannot be combined with --no-push")
    if start_node:
        push = True

    typer.echo(f"Fetching bootstrap peers for network: {net_cfg.name}")

    seeds: list[str] = []
    fetch_errors: list[str] = []

    if bootstrap_url:
        try:
            guard_bootstrap_rpc(
                bootstrap_url,
                allow_bootstrap_methods=True,
                method="net.getBootstrapSeeds",
                bootstrap_url=net_cfg.bootstrap_url,
            )
            resp = asyncio.run(rpc_call("net.getBootstrapSeeds", [], rpc_url=bootstrap_url))
            seeds = list((resp or {}).get("seeds") or [])
            if not seeds:
                seed_resp = asyncio.run(rpc_call("bootstrap.getSeeds", [], rpc_url=bootstrap_url))
                seeds = list((seed_resp or {}).get("seeds") or [])
        except Exception as exc:
            fetch_errors.append(str(exc))

    if not seeds:
        seeds = get_seed_nodes(net_cfg.name)

    if not seeds:
        typer.secho(
            f"No seed nodes configured for network '{net_cfg.name}'",
            fg=typer.colors.YELLOW,
        )
        if fetch_errors:
            typer.secho("Bootstrap RPC errors:", fg=typer.colors.RED)
            for err in fetch_errors:
                typer.echo(f"  - {err}")
        return

    seeds = list(dict.fromkeys(seeds))

    if fetch_errors:
        typer.secho("Bootstrap RPC errors (using fallback seeds):", fg=typer.colors.YELLOW)
        for err in fetch_errors:
            typer.echo(f"  - {err}")

    stored = 0
    for seed_address in seeds:
        typer.echo(f"Saving seed: {seed_address}")
        try:
            peer_id = _generate_peer_id(seed_address)
            _write_peer_to_store(store_path, peer_id, seed_address)
            _write_peer_to_sqlite(store_path, peer_id, seed_address, direction="outbound")
            stored += 1
        except Exception as exc:
            typer.secho(f"  ⚠ Failed to persist {seed_address}: {exc}", fg=typer.colors.YELLOW)

    typer.echo()
    typer.secho(
        f"✓ Saved {stored} seed(s) to local peer store",
        fg=typer.colors.GREEN,
        bold=True,
    )
    typer.echo(f"Store location: {_resolve_store_paths(store_path)[0]} | db: {_resolve_store_paths(store_path)[1]}")

    push_mode = "auto" if push is None else ("push" if push else "no-push")
    should_push = push is not False
    if not target_rpc:
        should_push = False
        if push_mode != "no-push":
            typer.secho(
                "ℹ️ Remote RPC endpoints do not accept peer injection (BOOTSTRAP_ONLY).",
                fg=typer.colors.CYAN,
            )
            typer.echo(
                "Next: start your local node and it will load the peer store automatically, "
                "or run: animica peer bootstrap --rpc-url http://127.0.0.1:8545/rpc --push."
            )
    elif not is_local_rpc_url(target_rpc):
        should_push = False
        if push_mode != "no-push":
            typer.secho(
                "ℹ️ Remote RPC endpoints do not accept peer injection (BOOTSTRAP_ONLY).",
                fg=typer.colors.CYAN,
            )
            typer.echo(
                "Next: start your local node and it will load the peer store automatically, "
                "or run: animica peer bootstrap --rpc-url http://127.0.0.1:8545/rpc --push."
            )

    rpc_added = False
    rpc_error: Optional[str] = None
    peer_count: Optional[int] = None
    if should_push and target_rpc:
        running, policy_blocked, _probe_error = _probe_rpc_for_peer_injection(target_rpc)
        if policy_blocked:
            typer.secho(
                "ℹ️ This RPC endpoint does not accept peer injection (BOOTSTRAP_ONLY).",
                fg=typer.colors.CYAN,
            )
            typer.echo(
                "Seeds will be loaded on next restart. "
                "Next: start your local node and it will load the peer store automatically, "
                "or run: animica peer bootstrap --rpc-url http://127.0.0.1:8545/rpc --push."
            )
        elif not running:
            if start_node:
                from animica.cli import node as node_cli

                os.environ.setdefault("ANIMICA_NETWORK", net_cfg.name)
                node_cli.up(detach=True, build=True, with_miner=False, wait_sync=False)
                running, policy_blocked, _probe_error = _probe_rpc_for_peer_injection(target_rpc)
            if not running:
                typer.secho(
                    "✓ Saved seeds. Start your local node and re-run with --push, or use --start-node.",
                    fg=typer.colors.GREEN,
                )
            elif policy_blocked:
                typer.secho(
                    "ℹ️ This RPC endpoint does not accept peer injection (BOOTSTRAP_ONLY).",
                    fg=typer.colors.CYAN,
                )
                typer.echo("Seeds will be loaded on next restart.")
        if running and not policy_blocked:
            try:
                url = _resolve_rpc_url(target_rpc, allow_remote_rpc=allow_remote_rpc, method="p2p.importPeers")
                import_result, error = asyncio.run(
                    _rpc_call_with_error("p2p.importPeers", [seeds], rpc_url=url)
                )
                if error and _is_bootstrap_only_error(error):
                    rpc_error = _rpc_error_message(error)
                    typer.secho(
                        "ℹ️ This RPC endpoint does not accept peer injection (BOOTSTRAP_ONLY).",
                        fg=typer.colors.CYAN,
                    )
                    typer.echo("Seeds will be loaded on next restart.")
                else:
                    rpc_added, rpc_error = _rpc_operation_succeeded(import_result)
                    if not rpc_added:
                        rpc_error = rpc_error or _rpc_error_message(error) or "p2p.importPeers did not report success"
            except Exception as exc:
                rpc_error = str(exc)

            if rpc_added:
                typer.secho(
                    f"✓ Pushed {stored} seed(s) into running node",
                    fg=typer.colors.GREEN,
                )
                try:
                    peers_result, peer_error = asyncio.run(
                        _rpc_call_with_error("p2p.listPeers", [], rpc_url=target_rpc)
                    )
                    if not peer_error and isinstance(peers_result, list):
                        peer_count = len(peers_result)
                except Exception:
                    peer_count = None
                if peer_count is not None:
                    typer.secho(
                        f"✓ Node now has {peer_count} peer(s); bootstrap initiated.",
                        fg=typer.colors.GREEN,
                    )
            elif rpc_error and not _is_bootstrap_only_error({"message": rpc_error}):
                typer.secho(
                    f"⚠ Unable to push seeds into running node: {rpc_error}",
                    fg=typer.colors.YELLOW,
                )

    if probe:
        typer.echo()
        typer.secho("Probe mode enabled: verifying reachability", fg=typer.colors.CYAN)
        for seed in seeds:
            host, port = _parse_address(seed)
            if host and port and _probe_port(host, port):
                typer.secho(f"  ✓ {seed} reachable", fg=typer.colors.GREEN)
            else:
                typer.secho(f"  ✗ {seed} not reachable", fg=typer.colors.YELLOW)


@app.command(name="diagnose")
def diagnose_peer(
    address: str = typer.Argument(..., help="Peer address to diagnose"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
) -> None:
    """
    Diagnose connection issues with a specific peer.
    
    Tests connectivity, DNS resolution, port availability, and
    provides detailed debugging information for troubleshooting P2P issues.
    
    Examples:
        animica peer diagnose tcp://example.com:30333
        animica peer diagnose /dns4/node.example.com/tcp/30333
    """
    # socket and time are already imported at module level
    
    typer.secho(f"\n🔍 Diagnosing peer: {address}", fg=typer.colors.CYAN, bold=True)
    typer.echo()
    
    # Parse address to extract host and port
    host, port = None, None
    try:
        if address.startswith("tcp://"):
            # Simple TCP address
            addr_part = address[6:]
            if ":" in addr_part:
                host, port_str = addr_part.rsplit(":", 1)
                port = int(port_str)
        elif "/" in address:
            # Multiaddr format
            parts = address.split("/")
            for i, part in enumerate(parts):
                if part in ["dns4", "dns6", "ip4", "ip6"] and i + 1 < len(parts):
                    host = parts[i + 1]
                if part == "tcp" and i + 1 < len(parts):
                    port = int(parts[i + 1])
        else:
            # Assume host:port
            if ":" in address:
                host, port_str = address.rsplit(":", 1)
                port = int(port_str)
            else:
                host = address
                port = 30333  # Default port
    except Exception as e:
        typer.echo(f"❌ Failed to parse address: {e}", err=True)
        raise typer.Exit(code=1)
    
    if not host:
        typer.echo("❌ Could not extract host from address", err=True)
        raise typer.Exit(code=1)
    
    typer.echo("📋 Parsed address:")
    typer.echo(f"   Host: {host}")
    typer.echo(f"   Port: {port or 'N/A'}")
    typer.echo()
    
    # DNS Resolution
    typer.secho("1️⃣  DNS Resolution", fg=typer.colors.BLUE, bold=True)
    try:
        start = time.time()
        ip_address = socket.gethostbyname(host)
        dns_time = time.time() - start
        typer.secho(f"   ✓ Resolved to: {ip_address}", fg=typer.colors.GREEN)
        typer.echo(f"   ⏱  Lookup time: {dns_time*1000:.1f}ms")
    except socket.gaierror as e:
        typer.secho(f"   ✗ DNS resolution failed: {e}", fg=typer.colors.RED)
        ip_address = None
    typer.echo()
    
    # Port Connectivity
    if port:
        typer.secho("2️⃣  Port Connectivity", fg=typer.colors.BLUE, bold=True)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            start = time.time()
            result = sock.connect_ex((host, port))
            connect_time = time.time() - start
            sock.close()
            
            if result == 0:
                typer.secho(f"   ✓ Port {port} is open", fg=typer.colors.GREEN)
                typer.echo(f"   ⏱  Connection time: {connect_time*1000:.1f}ms")
            else:
                typer.secho(f"   ✗ Port {port} is closed or filtered", fg=typer.colors.RED)
                typer.echo(f"   Error code: {result}")
        except Exception as e:
            typer.secho(f"   ✗ Connection failed: {e}", fg=typer.colors.RED)
        typer.echo()
    
    # Latency Test (Ping approximation)
    if ip_address and port:
        typer.secho("3️⃣  Latency Test (3 attempts)", fg=typer.colors.BLUE, bold=True)
        latencies = []
        for i in range(3):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                start = time.time()
                sock.connect((ip_address, port))
                latency = (time.time() - start) * 1000
                sock.close()
                latencies.append(latency)
                typer.echo(f"   Attempt {i+1}: {latency:.1f}ms")
            except Exception as e:
                typer.echo(f"   Attempt {i+1}: Failed ({e})")
        
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            typer.secho(f"   ✓ Average latency: {avg_latency:.1f}ms", fg=typer.colors.GREEN)
        typer.echo()
    
    # RPC Status Check
    typer.secho("4️⃣  Node RPC Status", fg=typer.colors.BLUE, bold=True)
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.getPeerInfo")
    try:
        result = asyncio.run(rpc_call("p2p.listPeers", [], rpc_url=url))
        typer.secho("   ✓ Node RPC accessible", fg=typer.colors.GREEN)
        typer.echo(f"   Connected peers: {len(result) if result else 0}")
    except Exception as e:
        typer.secho(f"   ✗ Node RPC failed: {e}", fg=typer.colors.RED)
    typer.echo()
    
    # Summary
    typer.secho("📊 Diagnosis Summary", fg=typer.colors.CYAN, bold=True)
    if ip_address and port and latencies:
        typer.secho("   ✓ Peer appears reachable", fg=typer.colors.GREEN)
        typer.echo("   Connection should be possible")
    elif not ip_address:
        typer.secho("   ⚠  DNS resolution issue", fg=typer.colors.YELLOW)
        typer.echo("   Check your DNS settings or use IP address directly")
    elif port and not latencies:
        typer.secho("   ⚠  Port connectivity issue", fg=typer.colors.YELLOW)
        typer.echo("   Port may be filtered by firewall or peer is offline")
    else:
        typer.secho("   ⚠  Partial connectivity", fg=typer.colors.YELLOW)
        typer.echo("   Some checks failed - review details above")
    typer.echo()


@app.command(name="test-latency")
def test_peer_latency(
    peer_id: str = typer.Argument(..., help="Peer ID to test latency"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    count: int = typer.Option(5, "--count", "-c", help="Number of pings to send"),
) -> None:
    """
    Test network latency to a connected peer.
    
    Sends multiple ping requests and measures round-trip time (RTT)
    to help diagnose network performance issues.
    
    Examples:
        animica peer test-latency QmPeerId...
        animica peer test-latency 12D3KooWPeerId... --count 10
    """
    url = _resolve_rpc_url(rpc_url, allow_remote_rpc=allow_remote_rpc, method="p2p.pingPeer")
    
    typer.secho(f"\n🏓 Testing latency to peer: {peer_id[:16]}...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"   Sending {count} pings...")
    typer.echo()
    
    latencies = []
    failures = 0
    
    for i in range(count):
        try:
            start = time.time()
            _result = asyncio.run(
                rpc_call("p2p.pingPeer", [peer_id], rpc_url=url)
            )
            # Result not used - latency is measured by wall-clock time
            latency = (time.time() - start) * 1000
            latencies.append(latency)
            
            status = "✓" if latency < 200 else "⚠" if latency < 500 else "✗"
            color = typer.colors.GREEN if latency < 200 else typer.colors.YELLOW if latency < 500 else typer.colors.RED
            
            typer.secho(f"   {status} Ping {i+1}: {latency:.1f}ms", fg=color)
            time.sleep(0.5)  # Small delay between pings
            
        except Exception as e:
            failures += 1
            typer.secho(f"   ✗ Ping {i+1}: Failed ({e})", fg=typer.colors.RED)
    
    typer.echo()
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        
        typer.secho("📊 Statistics:", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"   Sent: {count}")
        typer.echo(f"   Received: {len(latencies)}")
        typer.echo(f"   Lost: {failures} ({failures/count*100:.1f}%)")
        typer.echo(f"   Min: {min_latency:.1f}ms")
        typer.echo(f"   Max: {max_latency:.1f}ms")
        typer.echo(f"   Avg: {avg_latency:.1f}ms")
        
        if avg_latency < 100:
            typer.secho("\n✓ Excellent connection quality", fg=typer.colors.GREEN)
        elif avg_latency < 300:
            typer.secho("\n⚠  Moderate connection quality", fg=typer.colors.YELLOW)
        else:
            typer.secho("\n✗ Poor connection quality", fg=typer.colors.RED)
    else:
        typer.secho("✗ All pings failed - peer may be unreachable", fg=typer.colors.RED, bold=True)
    
    typer.echo()


if __name__ == "__main__":
    app()
