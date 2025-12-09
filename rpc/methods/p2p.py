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

import logging
import typing as t

from rpc.methods import method

log = logging.getLogger("animica.rpc.p2p")

# Optional P2P service imports with graceful fallbacks
_p2p_service: t.Any = None
_connection_manager: t.Any = None


def _get_connection_manager() -> t.Any | None:
    """
    Attempt to retrieve the global P2P ConnectionManager instance.
    
    Returns None if P2P service is not running or not available.
    This allows the RPC server to work even without P2P enabled.
    """
    global _p2p_service, _connection_manager
    
    if _connection_manager is not None:
        return _connection_manager
    
    # Try the global P2P service registry
    try:
        import p2p
        if hasattr(p2p, "get_connection_manager"):
            _connection_manager = p2p.get_connection_manager()
            if _connection_manager is not None:
                return _connection_manager
    except Exception:
        pass
    
    # Try to get from RPC deps if it was injected
    try:
        from rpc import deps
        ctx = deps.get_ctx()
        if hasattr(ctx, "p2p_service") and ctx.p2p_service is not None:
            _p2p_service = ctx.p2p_service
            if hasattr(_p2p_service, "connmgr"):
                _connection_manager = _p2p_service.connmgr
                return _connection_manager
    except Exception:
        pass
    
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
    cm = _get_connection_manager()
    if cm is None:
        log.debug("P2P ConnectionManager not available, returning empty peer list")
        return []
    
    try:
        # ConnectionManager.list_peers() returns List[Peer]
        peers = cm.list_peers() if callable(getattr(cm, "list_peers", None)) else []
        
        # Convert to JSON-serializable format
        result = [_peer_to_dict(peer) for peer in peers]
        
        log.debug("Listed %d peers", len(result))
        return result
    
    except Exception as e:
        log.error("Failed to list peers: %s", e, exc_info=True)
        # Return empty list instead of failing - allows graceful degradation
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
    cm = _get_connection_manager()
    if cm is None:
        return {
            "success": False,
            "error": "P2P service not available",
        }
    
    try:
        # ConnectionManager.connect(address) returns Optional[Peer]
        if not callable(getattr(cm, "connect", None)):
            return {
                "success": False,
                "error": "P2P ConnectionManager does not support adding peers",
            }
        
        peer = await cm.connect(address)
        
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
        if not callable(getattr(cm, "disconnect", None)):
            return {
                "success": False,
                "error": "P2P ConnectionManager does not support removing peers",
            }
        
        success = await cm.disconnect(peer_id)
        
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
        peers = cm.list_peers() if callable(getattr(cm, "list_peers", None)) else []
        
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
]
