"""
P2P network JSON-RPC methods.

Provides methods to query and manage peer connections:
  - p2p.listPeers (aliases: p2p.getPeers, p2p.peers, admin_peers, net_peers)
  - p2p.addPeer
  - p2p.removePeer
  - p2p.getPeerInfo

These methods access the P2P ConnectionManager if available. If the P2P service
is not running, methods return empty results or appropriate errors.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import typing as t

from rpc.methods import method

log = logging.getLogger("animica.rpc.p2p")

# Optional P2P service imports with graceful fallbacks
_p2p_service: t.Any = None
_connection_manager: t.Any = None


async def _safe_call_method(
    obj: t.Any, method_name: str, *args: t.Any, **kwargs: t.Any
) -> t.Any:
    """
    Safely call a method on an object, handling both sync and async methods.
    
    Returns None if the method doesn't exist or is not callable.
    """
    method = getattr(obj, method_name, None)
    if not callable(method):
        return None
    
    result = method(*args, **kwargs)
    
    # Handle async methods
    if inspect.iscoroutine(result) or asyncio.isfuture(result):
        return await result
    
    return result


def _get_p2p_service() -> t.Any | None:
    """
    Attempt to retrieve the global P2P service instance.
    
    Returns None if P2P service is not running or not available.
    This allows the RPC server to work even without P2P enabled.
    """
    global _p2p_service
    
    if _p2p_service is not None:
        return _p2p_service
    
    # Try the global P2P service registry
    try:
        import p2p
        if hasattr(p2p, "get_service"):
            _p2p_service = p2p.get_service()
            if _p2p_service is not None:
                return _p2p_service
    except Exception:
        pass
    
    # Try to get from RPC deps if it was injected
    try:
        from rpc import deps
        ctx = deps.get_ctx()
        if hasattr(ctx, "p2p_service") and ctx.p2p_service is not None:
            _p2p_service = ctx.p2p_service
            return _p2p_service
    except Exception:
        pass
    
    return None


def _get_connection_manager() -> t.Any | None:
    """
    Attempt to retrieve the P2P ConnectionManager instance.
    
    First tries to get the full NodeService's ConnectionManager,
    then falls back to the lightweight P2PService which uses a different structure.
    
    Returns None if P2P service is not running or not available.
    This allows the RPC server to work even without P2P enabled.
    """
    global _connection_manager
    
    if _connection_manager is not None:
        return _connection_manager
    
    # Try the global P2P service registry (for full NodeService with connmgr)
    try:
        import p2p
        if hasattr(p2p, "get_connection_manager"):
            _connection_manager = p2p.get_connection_manager()
            if _connection_manager is not None:
                return _connection_manager
    except Exception:
        pass
    
    # For lightweight P2PService, we don't have a separate ConnectionManager
    # The service itself manages connections via _peers dict
    # RPC methods will use the service's peers property directly
    return None


def _peer_to_dict(peer: t.Any) -> dict[str, t.Any]:
    """
    Convert a Peer object to a JSON-serializable dict.
    
    Handles various peer object shapes from the P2P stack.
    """
    if peer is None:
        return {}
    
    # If already a dict, return as-is
    if isinstance(peer, dict):
        return peer
    
    # Extract common peer attributes
    peer_dict: dict[str, t.Any] = {}
    
    # ID fields (try various names)
    for id_field in ("peer_id", "peerId", "id"):
        if hasattr(peer, id_field):
            peer_dict["id"] = str(getattr(peer, id_field))
            break
    
    # Address fields
    for addr_field in ("address", "addr", "remote_addr", "multiaddr"):
        if hasattr(peer, addr_field):
            peer_dict["addr"] = str(getattr(peer, addr_field))
            break
    
    # Status/state
    if hasattr(peer, "status"):
        peer_dict["status"] = str(getattr(peer, "status"))
    elif hasattr(peer, "state"):
        peer_dict["status"] = str(getattr(peer, "state"))
    else:
        peer_dict["status"] = "connected"
    
    # Direction (inbound/outbound)
    if hasattr(peer, "direction"):
        peer_dict["direction"] = str(getattr(peer, "direction"))
    
    # Latency/RTT
    if hasattr(peer, "last_rtt_ms"):
        rtt = getattr(peer, "last_rtt_ms")
        if rtt is not None:
            peer_dict["latencyMs"] = float(rtt)
    elif hasattr(peer, "rtt_ms"):
        rtt = getattr(peer, "rtt_ms")
        if rtt is not None:
            peer_dict["latencyMs"] = float(rtt)
    
    # Last seen timestamp
    if hasattr(peer, "last_seen"):
        peer_dict["lastSeen"] = float(getattr(peer, "last_seen"))
    
    # Metadata/capabilities
    if hasattr(peer, "meta"):
        meta = getattr(peer, "meta")
        if meta and isinstance(meta, dict):
            peer_dict["meta"] = meta
    
    # Streams/protocols
    if hasattr(peer, "streams"):
        streams = getattr(peer, "streams")
        if streams:
            peer_dict["streams"] = len(streams) if hasattr(streams, "__len__") else 0
    
    return peer_dict


@method(
    "p2p.listPeers",
    desc="List all connected peers",
    aliases=["p2p.getPeers", "p2p.peers", "admin_peers", "net_peers"],
)
async def list_peers() -> list[dict[str, t.Any]]:
    """
    List all connected peers.
    
    Returns a list of peer objects with the following fields:
      - id: Peer ID (string)
      - addr: Peer address/multiaddr (string)
      - status: Connection status (string, e.g., "connected")
      - direction: Connection direction ("inbound" or "outbound")
      - latencyMs: Round-trip latency in milliseconds (optional, float)
      - lastSeen: Unix timestamp of last activity (optional, float)
      - meta: Additional metadata (optional, dict)
      - streams: Number of active streams (optional, int)
    
    Returns empty list if P2P service is not available or no peers connected.
    
    Examples:
        >>> result = await list_peers()
        >>> len(result)
        3
        >>> result[0]["id"]
        "12D3KooWPeer..."
    """
    # First try to get the P2P service directly
    p2p_svc = _get_p2p_service()
    
    # If we have a P2PService with a peers property, use it
    if p2p_svc is not None and hasattr(p2p_svc, "peers"):
        try:
            peers_dict = p2p_svc.peers  # Returns Dict[str, Dict[str, Any]]
            result = []
            for remote_addr, peer_info in peers_dict.items():
                peer_dict = {
                    "id": peer_info.get("peer_id", "unknown"),
                    "addr": str(peer_info.get("remote", remote_addr)),
                    "status": "connected" if peer_info.get("connected", True) else "disconnected",
                }
                # Add optional fields if available
                if "direction" in peer_info:
                    peer_dict["direction"] = peer_info["direction"]
                if "last_seen" in peer_info:
                    peer_dict["lastSeen"] = peer_info["last_seen"]
                if "height" in peer_info:
                    peer_dict["height"] = peer_info["height"]
                if "info" in peer_info:
                    peer_dict["meta"] = peer_info["info"]
                result.append(peer_dict)
            
            log.debug("Listed %d peers from P2P service", len(result))
            return result
        except Exception as e:
            log.debug("Failed to get peers from P2P service: %s", e)
    
    # Try the ConnectionManager (for full NodeService)
    cm = _get_connection_manager()
    if cm is not None:
        try:
            # ConnectionManager.list_peers() returns List[Peer]
            peers = await _safe_call_method(cm, "list_peers")
            if peers is None:
                peers = []
            
            # Convert to JSON-serializable format
            result = [_peer_to_dict(peer) for peer in peers]
            
            log.debug("Listed %d peers from ConnectionManager", len(result))
            return result
        
        except Exception as e:
            log.error("Failed to list peers from ConnectionManager: %s", e, exc_info=True)
    
    # Fall back to persistent store if available
    try:
        from rpc import deps
        ctx = deps.get_ctx()
        if hasattr(ctx, "p2p_service") and ctx.p2p_service is not None:
            p2p_svc = ctx.p2p_service
            # Check if P2PService has a peerstore attribute
            if hasattr(p2p_svc, "peerstore"):
                from p2p.peer.peerstore import PeerStatus
                known_peers = p2p_svc.peerstore.list_known(
                    limit=100, 
                    status_in=[PeerStatus.CONNECTED]
                )
                result = []
                for peer in known_peers:
                    peer_dict = {
                        "id": peer.peer_id,
                        "addr": peer.address,
                        "status": peer.status.value if hasattr(peer.status, 'value') else str(peer.status),
                        "lastSeen": peer.last_seen_s if hasattr(peer, 'last_seen_s') else None,
                    }
                    # Include direction if available
                    if hasattr(peer, 'direction') and peer.direction:
                        peer_dict["direction"] = peer.direction
                    result.append(peer_dict)
                log.debug("Listed %d peers from persistent store", len(result))
                return result
    except Exception as e:
        log.debug("Failed to list peers from store: %s", e)
    
    log.debug("P2P service not available, returning empty peer list")
    return []


@method("p2p.addPeer", desc="Add a peer by address")
async def add_peer(address: str) -> dict[str, t.Any]:
    """
    Add a peer by address and attempt to connect.
    
    Args:
        address: Peer address (multiaddr or host:port format)
    
    Returns:
        Success status and peer info if connection succeeds, or error details.
    
    Examples:
        >>> await add_peer("/ip4/203.0.113.10/tcp/30303/p2p/12D3Koo...")
        {"success": True, "peer": {...}}
        >>> await add_peer("example.com:30303")
        {"success": True, "peer": {...}}
    """
    # First try P2PService.dial() method
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None and hasattr(p2p_svc, "dial"):
        try:
            await p2p_svc.dial(address)
            return {
                "success": True,
                "message": f"Dialing {address}",
            }
        except Exception as e:
            log.error("Failed to dial peer %s: %s", address, e, exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    # Try ConnectionManager (for full NodeService)
    cm = _get_connection_manager()
    if cm is None:
        return {
            "success": False,
            "error": "P2P service not available",
        }
    
    try:
        # ConnectionManager.connect(address) returns Optional[Peer]
        peer = await _safe_call_method(cm, "connect", address)
        
        if peer is None:
            return {
                "success": False,
                "error": f"Failed to connect to {address}",
            }
        
        return {
            "success": True,
            "peer": _peer_to_dict(peer),
        }
    
    except Exception as e:
        log.error("Failed to add peer %s: %s", address, e, exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@method("p2p.importPeers", desc="Persist and dial a list of peers")
async def import_peers(addresses: list[str]) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is None:
        return {"success": False, "error": "P2P service not available"}

    if hasattr(svc, "import_peers"):
        try:
            result = await svc.import_peers(addresses)
            result.setdefault("success", True)
            return result
        except Exception as e:  # pragma: no cover - defensive
            log.error("import_peers failed", exc_info=True)
            return {"success": False, "error": str(e)}

    # Fallback: seed peerstore directly if available
    added = 0
    try:
        peerstore = getattr(svc, "peerstore", None)
        if peerstore is None:
            return {"success": False, "error": "Peerstore unavailable"}
        for addr in addresses:
            peer_id = addr
            try:
                peerstore.add(peer_id=peer_id, addrs=[addr], direction="outbound")
                added += 1
            except Exception:
                continue
        return {"success": True, "added": added}
    except Exception as e:  # pragma: no cover - defensive
        return {"success": False, "error": str(e)}


@method("p2p.removePeer", desc="Remove a peer by ID")
async def remove_peer(peer_id: str) -> dict[str, t.Any]:
    """
    Disconnect from a peer by peer ID.
    
    Args:
        peer_id: Peer ID to disconnect
    
    Returns:
        Success status.
    
    Examples:
        >>> await remove_peer("12D3KooWPeer...")
        {"success": True}
    """
    cm = _get_connection_manager()
    if cm is None:
        return {
            "success": False,
            "error": "P2P service not available",
        }
    
    try:
        success = await _safe_call_method(cm, "disconnect", peer_id)
        
        return {
            "success": bool(success),
        }
    
    except Exception as e:
        log.error("Failed to remove peer %s: %s", peer_id, e, exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@method("p2p.getPeerInfo", desc="Get detailed information about a specific peer")
async def get_peer_info(peer_id: str) -> dict[str, t.Any] | None:
    """
    Get detailed information about a specific peer.
    
    Args:
        peer_id: Peer ID to query
    
    Returns:
        Peer information dict, or None if peer not found.
    
    Examples:
        >>> await get_peer_info("12D3KooWPeer...")
        {"id": "12D3Koo...", "addr": "/ip4/...", ...}
    """
    cm = _get_connection_manager()
    if cm is None:
        return None
    
    try:
        # Get all peers and find the matching one
        peers = await _safe_call_method(cm, "list_peers")
        if peers is None:
            return None
        
        for peer in peers:
            peer_dict = _peer_to_dict(peer)
            if peer_dict.get("id") == peer_id:
                return peer_dict
        
        return None
    
    except Exception as e:
        log.error("Failed to get peer info for %s: %s", peer_id, e, exc_info=True)
        return None


# Export for RPC method discovery
__all__ = [
    "list_peers",
    "add_peer",
    "remove_peer",
    "get_peer_info",
    "import_peers",
]
