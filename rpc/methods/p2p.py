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
import socket
import typing as t
from urllib.parse import urlparse

from rpc.methods import method

log = logging.getLogger("animica.rpc.p2p")

# Public-facing error message for unavailable P2P services.
P2P_UNAVAILABLE_ERROR = "P2P disabled/unavailable"

# Optional P2P service imports with graceful fallbacks
_p2p_service: t.Any = None
_connection_manager: t.Any = None
_core_p2p_service: t.Any = None


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


def _get_core_p2p_service() -> t.Any | None:
    global _core_p2p_service

    if _core_p2p_service is not None:
        return _core_p2p_service

    try:
        from rpc import deps

        ctx = deps.get_ctx()
        if hasattr(ctx, "core_p2p_service") and ctx.core_p2p_service is not None:
            _core_p2p_service = ctx.core_p2p_service
            return _core_p2p_service
    except Exception:
        pass

    return None


def _resolve_core_host(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for family, _, _, _, addr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            return addr[0]
    return None


def _peer_counts_snapshot() -> dict[str, int]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None:
        if hasattr(p2p_svc, "status_snapshot"):
            try:
                snap = p2p_svc.status_snapshot()
                if hasattr(snap, "to_dict"):
                    snap = snap.to_dict()
                if isinstance(snap, dict):
                    return {
                        "peers_total": int(snap.get("peers_total") or 0),
                        "peers_inbound": int(snap.get("peers_inbound") or 0),
                        "peers_outbound": int(snap.get("peers_outbound") or 0),
                    }
            except Exception:
                pass
        if hasattr(p2p_svc, "status"):
            try:
                status = p2p_svc.status()
                if isinstance(status, dict):
                    return {
                        "peers_total": int(status.get("peers_total") or 0),
                        "peers_inbound": int(status.get("peers_inbound") or 0),
                        "peers_outbound": int(status.get("peers_outbound") or 0),
                    }
            except Exception:
                pass
        if hasattr(p2p_svc, "peer_count"):
            try:
                return {
                    "peers_total": int(p2p_svc.peer_count()),
                    "peers_inbound": 0,
                    "peers_outbound": 0,
                }
            except Exception:
                pass

    core_svc = _get_core_p2p_service()
    if core_svc is not None and hasattr(core_svc, "connman"):
        inbound = 0
        outbound = 0
        total = 0
        try:
            peers = core_svc.connman.peers()
            if isinstance(peers, dict):
                for peer in peers.values():
                    total += 1
                    if getattr(peer, "inbound", False):
                        inbound += 1
                    else:
                        outbound += 1
        except Exception:
            pass
        return {
            "peers_total": total,
            "peers_inbound": inbound,
            "peers_outbound": outbound,
        }

    return {"peers_total": 0, "peers_inbound": 0, "peers_outbound": 0}


def _parse_core_address(address: str) -> tuple[t.Any | None, str | None]:
    try:
        from p2p.core_p2p.netaddress import NetAddress
        from p2p.transport.multiaddr import parse_multiaddr
    except Exception:
        return None, "core p2p address parser unavailable"

    if not address:
        return None, "address is empty"

    if address.startswith("/"):
        try:
            parsed = parse_multiaddr(address)
        except Exception:
            return None, f"invalid multiaddr: {address}"
        if parsed.transport != "tcp":
            return None, f"unsupported transport: {parsed.transport}"
        if not parsed.host or not parsed.port:
            return None, "missing host or port in multiaddr"
        resolved = _resolve_core_host(parsed.host)
        if resolved is None:
            return None, f"failed to resolve host {parsed.host}"
        return NetAddress(services=1, ip=resolved, port=int(parsed.port)), None

    host = address
    port: int | None = None
    if "://" in address:
        parsed = urlparse(address)
        host = parsed.hostname or ""
        port = parsed.port
    elif ":" in address:
        host, port_str = address.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return None, f"invalid port in address: {address}"

    if not host or port is None:
        return None, f"missing host or port in address: {address}"

    resolved = _resolve_core_host(host)
    if resolved is None:
        return None, f"failed to resolve host {host}"
    return NetAddress(services=1, ip=resolved, port=port), None


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


def _core_peer_to_dict(peer: t.Any) -> dict[str, t.Any]:
    if peer is None:
        return {}
    addr = getattr(peer, "address", None)
    addr_str = ""
    if addr is not None:
        addr_str = getattr(addr, "key", lambda: "")()
        if not addr_str:
            ip = getattr(addr, "ip", "")
            port = getattr(addr, "port", "")
            if ip and port:
                addr_str = f"{ip}:{port}"
    direction = "inbound" if getattr(peer, "inbound", False) else "outbound"
    last_seen = max(
        getattr(peer, "last_recv", 0) or 0,
        getattr(peer, "last_send", 0) or 0,
    )
    peer_dict: dict[str, t.Any] = {
        "id": str(getattr(peer, "peer_id", "")),
        "addr": addr_str,
        "status": "connected",
        "direction": direction,
    }
    if last_seen:
        peer_dict["lastSeen"] = float(last_seen)
    if hasattr(peer, "connected_at"):
        peer_dict["connectedAt"] = float(getattr(peer, "connected_at"))
    if hasattr(peer, "start_height"):
        peer_dict["height"] = getattr(peer, "start_height")
    if hasattr(peer, "user_agent") and getattr(peer, "user_agent"):
        peer_dict["meta"] = {"userAgent": getattr(peer, "user_agent")}
    return peer_dict


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
            if callable(peers_dict):
                peers_dict = peers_dict()
            result = []
            iterable = peers_dict.values() if isinstance(peers_dict, dict) else (peers_dict or [])
            for peer_info in iterable:
                peer_dict = {
                    "id": peer_info.get("peer_id") or peer_info.get("id") or "unknown",
                    "addr": str(peer_info.get("remote") or peer_info.get("addr") or ""),
                    "status": "connected" if peer_info.get("connected", True) else "disconnected",
                    "direction": peer_info.get("direction"),
                }
                if "last_seen" in peer_info:
                    peer_dict["lastSeen"] = peer_info["last_seen"]
                if "last_seen" not in peer_info and "last_seen_s" in peer_info:
                    peer_dict["lastSeen"] = peer_info.get("last_seen_s")
                if "connected_at" in peer_info:
                    peer_dict["connectedAt"] = peer_info.get("connected_at")
                if "height" in peer_info:
                    peer_dict["height"] = peer_info["height"]
                if "info" in peer_info:
                    peer_dict["meta"] = peer_info["info"]
                if "meta" in peer_info and isinstance(peer_info.get("meta"), dict):
                    peer_dict["meta"] = peer_info.get("meta")
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

    core_svc = _get_core_p2p_service()
    if core_svc is not None and hasattr(core_svc, "connman"):
        try:
            peers = core_svc.connman.peers()
            if isinstance(peers, dict):
                return [_core_peer_to_dict(peer) for peer in peers.values()]
        except Exception as e:
            log.debug("Failed to list peers from core P2P service: %s", e)
    
    log.debug("P2P service not available, returning empty peer list")
    return []


@method("p2p.getStatus", desc="Return the live P2P status snapshot")
async def get_status() -> dict[str, t.Any]:
    startup_error = None
    try:
        from rpc import deps

        ctx = deps.get_ctx()
        startup_error = getattr(ctx, "p2p_start_error", None)
    except Exception:
        startup_error = None
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None:
        if hasattr(p2p_svc, "status_snapshot"):
            snap = p2p_svc.status_snapshot()
            if hasattr(snap, "to_dict"):
                result = snap.to_dict()
                peer_id = None
                if hasattr(p2p_svc, "peer_id"):
                    peer_id = getattr(p2p_svc, "peer_id", None)
                if peer_id is None and hasattr(p2p_svc, "_peer_id_bytes"):
                    peer_bytes = getattr(p2p_svc, "_peer_id_bytes", None)
                    if isinstance(peer_bytes, (bytes, bytearray)):
                        peer_id = bytes(peer_bytes).hex()
                if peer_id:
                    result.setdefault("peer_id", str(peer_id))
                if startup_error:
                    result.setdefault("startup_error", startup_error)
                return result
            if isinstance(snap, dict):
                peer_id = snap.get("peer_id")
                if peer_id is None and hasattr(p2p_svc, "_peer_id_bytes"):
                    peer_bytes = getattr(p2p_svc, "_peer_id_bytes", None)
                    if isinstance(peer_bytes, (bytes, bytearray)):
                        snap["peer_id"] = bytes(peer_bytes).hex()
                if startup_error:
                    snap.setdefault("startup_error", startup_error)
                return snap
        if hasattr(p2p_svc, "status"):
            try:
                status = p2p_svc.status()
                if isinstance(status, dict):
                    status.setdefault("p2p_running", True)
                    if startup_error:
                        status.setdefault("startup_error", startup_error)
                    return status
            except Exception:
                pass

    core_svc = _get_core_p2p_service()
    if core_svc is not None:
        inbound = 0
        outbound = 0
        peers_total = 0
        try:
            peers = core_svc.connman.peers() if hasattr(core_svc, "connman") else {}
            if isinstance(peers, dict):
                for peer in peers.values():
                    peers_total += 1
                    if getattr(peer, "inbound", False):
                        inbound += 1
                    else:
                        outbound += 1
        except Exception:
            pass
        return {
            "p2p_running": True,
            "listen_addrs": [],
            "peers_total": peers_total,
            "peers_inbound": inbound,
            "peers_outbound": outbound,
            "bootstrap_attempts_last_5m": 0,
            "last_peer_connect_at": None,
            "last_peer_disconnect_at": None,
            "seed_sources": {},
            "dial_queue_depth": 0,
            "addrman_size": None,
            "startup_error": startup_error,
        }

    return {
        "p2p_running": False,
        "listen_addrs": [],
        "peers_total": 0,
        "peers_inbound": 0,
        "peers_outbound": 0,
        "bootstrap_attempts_last_5m": 0,
        "last_peer_connect_at": None,
        "last_peer_disconnect_at": None,
        "seed_sources": {},
        "dial_queue_depth": 0,
        "addrman_size": None,
        "startup_error": startup_error,
    }


@method("p2p.syncDebug", desc="Return P2P sync debug details")
async def sync_debug() -> dict[str, t.Any]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None and hasattr(p2p_svc, "sync_debug_snapshot"):
        try:
            return t.cast(dict[str, t.Any], p2p_svc.sync_debug_snapshot())
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": str(exc)}
    return {"error": P2P_UNAVAILABLE_ERROR}


@method("p2p.debugStatus", desc="Return P2P tx relay debug status")
async def debug_status() -> dict[str, t.Any]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None and hasattr(p2p_svc, "debug_status"):
        try:
            return t.cast(dict[str, t.Any], await p2p_svc.debug_status())
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": str(exc)}
    return {"error": P2P_UNAVAILABLE_ERROR}


@method("p2p.getPeerStats", desc="Return detailed peer stats for the P2P service")
async def get_peer_stats() -> list[dict[str, t.Any]]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None and hasattr(p2p_svc, "peer_stats_snapshot"):
        try:
            return list(p2p_svc.peer_stats_snapshot())
        except Exception as exc:
            log.debug("Failed to read peer stats: %s", exc)
    return []


@method("p2p.getBans", desc="Return the current P2P ban list")
async def get_bans() -> list[dict[str, t.Any]]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is not None and hasattr(p2p_svc, "banlist_snapshot"):
        try:
            return list(p2p_svc.banlist_snapshot())
        except Exception as exc:
            log.debug("Failed to read ban list: %s", exc)
    return []


@method("p2p.banPeer", desc="Ban a peer or address for a duration (seconds)")
async def ban_peer(key: str, ttl_s: float, reason: str | None = None) -> dict[str, t.Any]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is None or not hasattr(p2p_svc, "ban_peer"):
        return {"success": False, "error": P2P_UNAVAILABLE_ERROR}
    try:
        p2p_svc.ban_peer(key, ttl_s=float(ttl_s), reason=reason or "manual")
        return {"success": True, "key": key, "ttl_s": float(ttl_s)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@method("p2p.unbanPeer", desc="Remove a peer or address from the ban list")
async def unban_peer(key: str) -> dict[str, t.Any]:
    p2p_svc = _get_p2p_service()
    if p2p_svc is None or not hasattr(p2p_svc, "unban_peer"):
        return {"success": False, "error": P2P_UNAVAILABLE_ERROR}
    try:
        p2p_svc.unban_peer(key)
        return {"success": True, "key": key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


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
    if cm is not None:
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

    core_svc = _get_core_p2p_service()
    if core_svc is not None and hasattr(core_svc, "connman"):
        net_addr, error = _parse_core_address(address)
        if net_addr is None:
            return {"success": False, "error": error or "invalid address"}
        try:
            await core_svc.connman.dial(net_addr)
            return {"success": True, "message": f"Dialing {address}"}
        except Exception as e:
            log.error("Failed to dial core peer %s: %s", address, e, exc_info=True)
            return {"success": False, "error": str(e)}

    return {
        "success": False,
        "error": P2P_UNAVAILABLE_ERROR,
    }


@method("p2p.importPeers", desc="Persist and dial a list of peers")
async def import_peers(addresses: list[str]) -> dict[str, t.Any]:
    svc = _get_p2p_service()
    if svc is None:
        core_svc = _get_core_p2p_service()
        if core_svc is None or not hasattr(core_svc, "addrman"):
            peer_counts = _peer_counts_snapshot()
            return {
                "success": False,
                "added": 0,
                "skipped": 0,
                "dial_attempted": 0,
                "dial_success": 0,
                "seeds_added": 0,
                "seeds_skipped": 0,
                "dial_attempts_started": 0,
                **peer_counts,
                "errors": [P2P_UNAVAILABLE_ERROR],
            }
        added = 0
        skipped = 0
        dial_attempted = 0
        dial_success = 0
        errors: list[str] = []
        for addr in addresses:
            net_addr, err = _parse_core_address(addr)
            if net_addr is None:
                skipped += 1
                errors.append(err or f"invalid address {addr}")
                continue
            try:
                core_svc.addrman.add([net_addr])
                added += 1
                if hasattr(core_svc, "connman"):
                    dial_attempted += 1
                    try:
                        await core_svc.connman.dial(net_addr)
                        dial_success += 1
                    except Exception as exc:
                        errors.append(str(exc))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(str(exc))
        peer_counts = _peer_counts_snapshot()
        return {
            "success": bool(added or dial_attempted),
            "added": added,
            "skipped": skipped,
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "seeds_added": added,
            "seeds_skipped": skipped,
            "dial_attempts_started": dial_attempted,
            **peer_counts,
            "errors": errors,
        }

    if hasattr(svc, "import_peers"):
        try:
            result = await svc.import_peers(addresses)
            result.setdefault("success", True)
            result.setdefault("seeds_added", result.get("added", 0))
            result.setdefault("seeds_skipped", result.get("skipped", 0))
            result.setdefault("dial_attempts_started", result.get("dial_attempted", 0))
            result.update(_peer_counts_snapshot())
            return result
        except Exception as e:  # pragma: no cover - defensive
            log.error("import_peers failed", exc_info=True)
            peer_counts = _peer_counts_snapshot()
            return {
                "success": False,
                "added": 0,
                "skipped": 0,
                "dial_attempted": 0,
                "dial_success": 0,
                "seeds_added": 0,
                "seeds_skipped": 0,
                "dial_attempts_started": 0,
                **peer_counts,
                "errors": [str(e)],
            }

    # Fallback: seed peerstore directly if available
    added = 0
    skipped = 0
    dial_attempted = 0
    dial_success = 0
    try:
        peerstore = getattr(svc, "peerstore", None)
        if peerstore is None:
            peer_counts = _peer_counts_snapshot()
            return {
                "success": False,
                "added": 0,
                "skipped": 0,
                "dial_attempted": 0,
                "dial_success": 0,
                "seeds_added": 0,
                "seeds_skipped": 0,
                "dial_attempts_started": 0,
                **peer_counts,
                "errors": ["Peerstore unavailable"],
            }
        for addr in addresses:
            peer_id = addr
            try:
                peerstore.add(peer_id=peer_id, addrs=[addr], direction="outbound")
                added += 1
            except Exception:
                skipped += 1
                continue
        peer_counts = _peer_counts_snapshot()
        return {
            "success": bool(added),
            "added": added,
            "skipped": skipped,
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "seeds_added": added,
            "seeds_skipped": skipped,
            "dial_attempts_started": dial_attempted,
            **peer_counts,
            "errors": [],
        }
    except Exception as e:  # pragma: no cover - defensive
        peer_counts = _peer_counts_snapshot()
        return {
            "success": False,
            "added": 0,
            "skipped": 0,
            "dial_attempted": 0,
            "dial_success": 0,
            "seeds_added": 0,
            "seeds_skipped": 0,
            "dial_attempts_started": 0,
            **peer_counts,
            "errors": [str(e)],
        }


@method("p2p.addPeers", desc="Add multiple peers by address")
async def add_peers(addresses: list[str]) -> dict[str, t.Any]:
    """
    Add multiple peers by address and attempt to connect.

    Args:
        addresses: List of peer addresses (multiaddr or host:port format)

    Returns:
        Success status and counts of added/dialed peers.
    """
    return await import_peers(addresses)


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
    if cm is not None:
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

    core_svc = _get_core_p2p_service()
    if core_svc is not None and hasattr(core_svc, "connman"):
        try:
            await core_svc.connman._drop(peer_id, reason="rpc_remove")
            return {"success": True}
        except Exception as e:
            log.error("Failed to remove core peer %s: %s", peer_id, e, exc_info=True)
            return {"success": False, "error": str(e)}

    return {
        "success": False,
        "error": P2P_UNAVAILABLE_ERROR,
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
    if cm is not None:
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

    core_svc = _get_core_p2p_service()
    if core_svc is not None and hasattr(core_svc, "connman"):
        try:
            peers = core_svc.connman.peers()
            peer = peers.get(peer_id)
            if peer is None:
                return None
            return _core_peer_to_dict(peer)
        except Exception as e:
            log.error("Failed to get core peer info for %s: %s", peer_id, e, exc_info=True)
            return None
    return None


# Export for RPC method discovery
__all__ = [
    "list_peers",
    "get_status",
    "sync_debug",
    "debug_status",
    "add_peer",
    "remove_peer",
    "get_peer_info",
    "import_peers",
    "add_peers",
]
