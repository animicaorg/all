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
                    return list(registry.snapshot())
            except Exception:
                pass
        if svc is not None and hasattr(svc, "peers"):
            peers = svc.peers() if callable(getattr(svc, "peers")) else svc.peers
            if isinstance(peers, dict):
                return list(peers.values())
            if peers is None:
                return []
            return list(peers)
    except Exception:
        return []
    return []
from rpc.methods import method
from rpc import errors as rpc_errors


def _fallback_seeds_from_network(chain_id: int | None) -> list[str]:
    network_name = {1: "mainnet", 2: "testnet", 1337: "devnet"}.get(chain_id, "mainnet")
    try:
        from animica.seeds import get_seed_nodes

        return list(get_seed_nodes(network_name))
    except Exception:
        return []


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
        if snapshot:
            # Deduplicate by peer_id when present
            seen = set()
            for peer in snapshot:
                pid = str(peer.get("peer_id") or peer.get("id") or "")
                if pid:
                    seen.add(pid)
            unknown = sum(1 for peer in snapshot if not (peer.get("peer_id") or peer.get("id")))
            return len(seen) + unknown
        return 0
    except Exception as exc:
        raise rpc_errors.InternalError(f"peer count unavailable: {exc}")


@method("net.peers", desc="Return a snapshot of connected peers", aliases=["p2p.netPeers"])
async def net_peers() -> list[dict[str, object]]:
    try:
        return _active_peer_snapshot()
    except Exception as exc:
        raise rpc_errors.InternalError(f"peer list unavailable: {exc}")


__all__ = ["net_get_bootstrap_seeds", "net_peer_count", "net_peers"]
