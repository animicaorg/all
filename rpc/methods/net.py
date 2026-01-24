from __future__ import annotations

"""Network metadata RPC methods."""

import asyncio
import typing as t

from rpc import deps
from rpc.config import resolve_chain_id


def _normalize_seed_address(addr: str) -> str:
    """Normalize peer addresses for seed responses.

    Ensures host:port style endpoints are tagged with a scheme so they can be
    consumed directly by dialers that expect URL-like seeds.
    """

    if not addr:
        return addr

    # Already normalized (multiaddr-style or URL-style)
    if addr.startswith("/") or "://" in addr:
        return addr

    # Best-effort normalize host:port → tcp://host:port
    if ":" in addr:
        return f"tcp://{addr}"

    return addr


def _collect_live_peer_seeds() -> tuple[list[str], list[str]]:
    """Collect inbound/outbound peer addresses from the running P2P service.

    Returns a tuple of (inbound, outbound) addresses, already normalized for
    seed consumption. All failures are swallowed so bootstrap continues to work
    when P2P is unavailable.
    """

    inbound: list[str] = []
    outbound: list[str] = []

    try:
        import p2p  # type: ignore

        svc = None
        if hasattr(p2p, "get_service"):
            try:
                svc = p2p.get_service()
            except Exception:
                svc = None

        # If we have a lightweight P2PService, use its peers view.
        if svc is not None and hasattr(svc, "peers"):
            try:
                peer_map = getattr(svc, "peers")
                if callable(peer_map):
                    peer_map = peer_map()
                for info in (peer_map or {}).values():
                    addr = _normalize_seed_address(info.get("remote") or info.get("addr"))
                    if not addr:
                        continue
                    direction = (info.get("direction") or "").lower()
                    if direction == "inbound":
                        inbound.append(addr)
                    else:
                        outbound.append(addr)
            except Exception:
                pass

        # If a ConnectionManager is available (full node service), include its peers too.
        cm = None
        if hasattr(p2p, "get_connection_manager"):
            try:
                cm = p2p.get_connection_manager()
            except Exception:
                cm = None

        if cm is not None:
            try:
                peers = cm.list_peers()  # May be sync or async depending on impl
                if asyncio.iscoroutine(peers):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        peers = asyncio.run(peers)
                    else:
                        # Running inside an event loop; avoid blocking it.
                        peers = []
                for peer in peers or []:
                    direction = getattr(peer, "direction", "") or ""
                    addr = _normalize_seed_address(
                        getattr(peer, "address", None)
                        or getattr(peer, "remote_addr", None)
                        or getattr(peer, "addr", None)
                        or ""
                    )
                    if not addr:
                        continue
                    if str(direction).lower() == "inbound":
                        inbound.append(addr)
                    else:
                        outbound.append(addr)
            except Exception:
                pass

    except Exception:
        # No P2P available (or not importable) – fall back to static seeds only.
        pass

    try:
        ctx = deps.get_ctx()
        core_svc = getattr(ctx, "core_p2p_service", None)
        if core_svc is not None and hasattr(core_svc, "connman"):
            peers = core_svc.connman.peers()
            for peer in peers.values():
                addr = getattr(peer, "address", None)
                addr_str = ""
                if addr is not None:
                    addr_str = getattr(addr, "key", lambda: "")()
                    if not addr_str:
                        ip = getattr(addr, "ip", "")
                        port = getattr(addr, "port", "")
                        if ip and port:
                            addr_str = f"{ip}:{port}"
                if not addr_str:
                    continue
                addr_str = _normalize_seed_address(addr_str)
                if getattr(peer, "inbound", False):
                    inbound.append(addr_str)
                else:
                    outbound.append(addr_str)
    except Exception:
        pass

    # Preserve insertion order while deduplicating
    def _dedupe(items: list[str]) -> list[str]:
        seen = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return _dedupe(inbound), _dedupe(outbound)


def _normalize_peer_snapshot(peer: dict[str, object]) -> dict[str, object]:
    normalized = dict(peer)
    if "addr" not in normalized:
        addr = normalized.get("remote") or normalized.get("address")
        if addr:
            normalized["addr"] = addr
    if "lastSeen" not in normalized and "last_seen" in normalized:
        normalized["lastSeen"] = normalized.get("last_seen")
    if "connectedAt" not in normalized and "connected_at" in normalized:
        normalized["connectedAt"] = normalized.get("connected_at")
    return normalized


def _active_peer_snapshot() -> list[dict[str, object]]:
    """
    Return a list of active peer sessions from the running P2P service.
    """
    try:
        import p2p  # type: ignore

        svc = None
        if hasattr(p2p, "get_service"):
            svc = p2p.get_service()
        if svc is not None and hasattr(svc, "peer_registry"):
            try:
                registry = getattr(svc, "peer_registry")
                if registry is not None and hasattr(registry, "snapshot"):
                    return [
                        _normalize_peer_snapshot(p)
                        for p in list(registry.snapshot())
                        if isinstance(p, dict)
                    ]
            except Exception:
                pass
        if svc is not None and hasattr(svc, "peers"):
            peers = svc.peers() if callable(getattr(svc, "peers")) else svc.peers
            if isinstance(peers, dict):
                return [
                    _normalize_peer_snapshot(p)
                    for p in list(peers.values())
                    if isinstance(p, dict)
                ]
            if peers is None:
                return []
            return [
                _normalize_peer_snapshot(p) for p in list(peers) if isinstance(p, dict)
            ]
    except Exception:
        pass

    try:
        ctx = deps.get_ctx()
        core_svc = getattr(ctx, "core_p2p_service", None)
        if core_svc is not None and hasattr(core_svc, "connman"):
            peers = core_svc.connman.peers()
            snapshot = []
            for peer in peers.values():
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
                peer_dict: dict[str, object] = {
                    "peer_id": str(getattr(peer, "peer_id", "")),
                    "addr": addr_str,
                    "direction": direction,
                }
                if last_seen:
                    peer_dict["last_seen"] = float(last_seen)
                if hasattr(peer, "connected_at"):
                    peer_dict["connected_at"] = float(getattr(peer, "connected_at"))
                if hasattr(peer, "start_height"):
                    peer_dict["height"] = getattr(peer, "start_height")
                if hasattr(peer, "user_agent") and getattr(peer, "user_agent"):
                    peer_dict["info"] = {"userAgent": getattr(peer, "user_agent")}
                snapshot.append(peer_dict)
            return [_normalize_peer_snapshot(p) for p in snapshot]
    except Exception:
        pass
    return []
from rpc.methods import method
from rpc import errors as rpc_errors


def _fallback_seeds_from_network(chain_id: int | None) -> list[str]:
    network_name = {0: "mainnet", 2: "testnet", 1337: "devnet"}.get(chain_id, "mainnet")
    try:
        from animica.seeds import get_seed_nodes

        return list(get_seed_nodes(network_name))
    except Exception:
        return []


def _filter_tcp_seeds(seeds: list[str]) -> list[str]:
    """
    Filter seeds to only include TCP transports that are supported by CoreP2PService.
    
    QUIC/UDP addresses are not yet supported by the core P2P implementation and will
    be rejected as invalid during import. This filter prevents confusion by removing
    them from the seed list before they're returned to clients.
    
    Args:
        seeds: List of multiaddr or host:port seed addresses
        
    Returns:
        Filtered list containing only TCP-compatible seeds
    """
    filtered: list[str] = []
    for seed in seeds:
        # Skip QUIC/UDP addresses - they're not supported by CoreP2PService yet
        # The substring check "/quic" matches both "/quic" and "/quic-v1" variants
        if "/udp/" in seed or "/quic" in seed.lower():
            continue
        # Keep TCP addresses and host:port style addresses (which default to TCP)
        filtered.append(seed)
    return filtered


@method("net.getBootstrapSeeds", desc="Return canonical bootstrap seeds for the active network")
def net_get_bootstrap_seeds() -> dict[str, t.Any]:
    seeds: list[str] = []
    try:
        from p2p.config import load_config

        cfg = load_config()
        seeds = list(getattr(cfg, "seeds", []) or [])
    except Exception:
        seeds = []

    if not seeds:
        try:
            cid = resolve_chain_id()
        except Exception:
            try:
                cid = deps.get_ctx().cfg.chain_id  # type: ignore[attr-defined]
            except Exception:
                cid = None
        seeds = _fallback_seeds_from_network(cid)

    # Filter out QUIC/UDP seeds that aren't supported by CoreP2PService
    seeds = _filter_tcp_seeds(seeds)

    inbound_peers, outbound_peers = _collect_live_peer_seeds()

    merged_seeds: list[str] = []
    for source in (seeds, outbound_peers, inbound_peers):
        for addr in source:
            normalized = _normalize_seed_address(addr)
            if normalized not in merged_seeds:
                merged_seeds.append(normalized)

    ttl = 120
    return {
        "seeds": merged_seeds[:32],
        "ttl": ttl,
        "chainId": resolve_chain_id(),
        "discovered": {
            "outbound": outbound_peers[:32],
            "inbound": inbound_peers[:32],
        },
    }


@method("net.peerCount", desc="Return the number of connected peers", aliases=["p2p.peerCount"])
async def net_peer_count() -> int:
    try:
        snapshot = _active_peer_snapshot()
        bootstrap_bonus = 0
        try:
            import p2p  # type: ignore

            svc = None
            if hasattr(p2p, "get_service"):
                svc = p2p.get_service()
            if svc is not None and hasattr(svc, "bootstrap_peer_bonus"):
                bootstrap_bonus = int(getattr(svc, "bootstrap_peer_bonus")())
            if svc is not None and hasattr(svc, "status_snapshot"):
                try:
                    status = svc.status_snapshot()
                    if hasattr(status, "to_dict"):
                        status = status.to_dict()
                    if isinstance(status, dict) and "peers_connected" in status:
                        return int(status.get("peers_connected") or 0) + bootstrap_bonus
                except Exception:
                    pass
        except Exception:
            bootstrap_bonus = 0
        if snapshot:
            # Deduplicate by peer_id when present
            seen = set()
            for peer in snapshot:
                pid = str(peer.get("peer_id") or peer.get("id") or "")
                if pid:
                    seen.add(pid)
            unknown = sum(1 for peer in snapshot if not (peer.get("peer_id") or peer.get("id")))
            return len(seen) + unknown + bootstrap_bonus
        return bootstrap_bonus
    except Exception as exc:
        raise rpc_errors.InternalError(f"peer count unavailable: {exc}")


@method("net.peers", desc="Return a snapshot of connected peers", aliases=["p2p.netPeers"])
async def net_peers() -> list[dict[str, object]]:
    try:
        return _active_peer_snapshot()
    except Exception as exc:
        raise rpc_errors.InternalError(f"peer list unavailable: {exc}")


@method("net.getChainId", desc="Return the chain ID of the running node", aliases=["net.chainId", "eth_chainId"])
async def net_get_chain_id() -> int:
    """
    Return the chain ID that this node is operating on.
    
    This is the authoritative chain ID used by consensus, state, and P2P.
    Use this to verify the CLI/wallet are querying the correct network.
    
    Returns:
        Chain ID (0 for mainnet, 2 for testnet, 1337 for devnet)
    """
    try:
        # Try to get from RPC context first (most authoritative)
        ctx = deps.get_ctx()
        if hasattr(ctx, "cfg") and hasattr(ctx.cfg, "chain_id"):
            return int(ctx.cfg.chain_id)
        
        # Fallback to config resolution
        return int(resolve_chain_id())
    except Exception as exc:
        raise rpc_errors.InternalError(f"chain ID unavailable: {exc}")


@method("net.getGenesisHash", desc="Return the genesis block hash of the running node", aliases=["net.genesisHash", "chain.genesisHash"])
async def net_get_genesis_hash() -> str:
    """
    Return the genesis block hash that this node was initialized with.
    
    This is the pinned genesis hash used for network identity validation.
    Use this to verify the node is on the expected network/chain.
    
    Returns:
        Genesis block hash as 0x-prefixed hex string (32 bytes)
    """
    try:
        # Get genesis hash from context or block_db
        ctx = deps.get_ctx()
        
        # Try to get from block_db (genesis block at height 0)
        if hasattr(ctx, "block_db"):
            try:
                genesis_block = ctx.block_db.get_block_by_height(0)
                if genesis_block:
                    # Return block hash if available
                    block_hash = getattr(genesis_block, "hash", None)
                    # CRITICAL: Always call hash if it's a method/callable
                    # This prevents returning bound method objects like "<bound method Header.hash ...>"
                    if callable(block_hash):
                        block_hash = block_hash()
                    if block_hash:
                        if isinstance(block_hash, bytes):
                            return "0x" + block_hash.hex()
                        # Additional safety: check if result is still callable (shouldn't happen)
                        if callable(block_hash):
                            raise ValueError(f"block_hash is still callable after invocation: {type(block_hash)}")
                        # Ensure the result is a proper hex string
                        hash_str = str(block_hash)
                        if not hash_str.startswith("0x"):
                            hash_str = "0x" + hash_str
                        # Validate it's a proper hex string (64 chars + 0x prefix = 66 total)
                        if len(hash_str) == 66 and all(c in "0123456789abcdefABCDEF" for c in hash_str[2:]):
                            return hash_str.lower()
                        raise ValueError(f"Invalid hash format: {hash_str}")
                    
                    # Try header hash
                    header = getattr(genesis_block, "header", None)
                    if header:
                        header_hash = getattr(header, "hash", None) or getattr(header, "block_hash", None)
                        # CRITICAL: Always call hash if it's a method/callable
                        if callable(header_hash):
                            header_hash = header_hash()
                        if header_hash:
                            if isinstance(header_hash, bytes):
                                return "0x" + header_hash.hex()
                            # Additional safety: check if result is still callable (shouldn't happen)
                            if callable(header_hash):
                                raise ValueError(f"header_hash is still callable after invocation: {type(header_hash)}")
                            # Ensure the result is a proper hex string
                            hash_str = str(header_hash)
                            if not hash_str.startswith("0x"):
                                hash_str = "0x" + hash_str
                            # Validate it's a proper hex string (64 chars + 0x prefix = 66 total)
                            if len(hash_str) == 66 and all(c in "0123456789abcdefABCDEF" for c in hash_str[2:]):
                                return hash_str.lower()
                            raise ValueError(f"Invalid hash format: {hash_str}")
            except Exception:
                pass
        
        # Fallback: get pinned genesis hash from network params
        chain_id = resolve_chain_id()
        try:
            from core.network_params import get_pinned_genesis_hash
            
            pinned_hash = get_pinned_genesis_hash(chain_id=chain_id)
            if pinned_hash:
                return "0x" + pinned_hash.hex()
        except Exception:
            pass
        
        # Last resort: try to get from network manifest
        try:
            from core.network_manifest import get_manifest
            
            manifest = get_manifest(chain_id=chain_id)
            if manifest:
                return manifest.pinned_genesis_hash_hex
        except Exception:
            pass
        
        raise rpc_errors.InternalError("genesis hash not available from any source")
        
    except Exception as exc:
        if isinstance(exc, rpc_errors.InternalError):
            raise
        raise rpc_errors.InternalError(f"genesis hash unavailable: {exc}")


__all__ = ["net_get_bootstrap_seeds", "net_peer_count", "net_peers", "net_get_chain_id", "net_get_genesis_hash"]
