import asyncio
import os
import socket
import sys
from contextlib import closing

import pytest

# Make repo root importable
sys.path.insert(0, os.path.expanduser("~/animica"))

#
# This test aims for a black-box, end-to-end sanity:
#  - start two P2P nodes bound to localhost on ephemeral TCP ports
#  - connect node B -> node A
#  - wait for HELLO/handshake to complete
#  - verify both see each other (peer count >= 1)
#  - if a header-sync API is exposed, trigger it and assert tips match
#
# It is intentionally defensive: it introspects for the best-effort APIs
# your implementation exposes and skips gracefully when a feature is absent.
#

# -----------------------------
# Utilities
# -----------------------------


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def eventually(predicate, timeout=8.0, interval=0.05) -> bool:
    """
    Poll predicate() until it returns truthy or timeout elapses.
    predicate can be sync or async.
    """
    end = asyncio.get_event_loop().time() + timeout
    while True:
        if asyncio.iscoroutinefunction(predicate):
            ok = await predicate()
        else:
            ok = predicate()
        if ok:
            return True
        if asyncio.get_event_loop().time() >= end:
            return False
        await asyncio.sleep(interval)


def get_peer_count(service) -> int:
    # Try a few likely places where peer count may be exposed.
    # 1) service.metrics.peer_count
    m = getattr(service, "metrics", None)
    if m is not None:
        pc = getattr(m, "peer_count", None)
        if isinstance(pc, int):
            return pc
        # Some metrics expose a callable
        if callable(pc):
            try:
                return int(pc())
            except Exception:
                pass
    # 2) service.router.num_peers()
    router = getattr(service, "router", None)
    if router is not None:
        for name in ("num_peers", "peer_count"):
            f = getattr(router, name, None)
            if callable(f):
                try:
                    return int(f())
                except Exception:
                    pass
    # 3) service.peerstore / peers
    for attr in ("peerstore", "peers", "peermap"):
        ps = getattr(service, attr, None)
        if ps is not None:
            # Map-like
            try:
                return len(ps)
            except Exception:
                # Maybe has .count()
                c = getattr(ps, "count", None)
                if callable(c):
                    try:
                        return int(c())
                    except Exception:
                        pass
    return 0


def get_head_height(service) -> int | None:
    """
    Best-effort read of a node's current head height from any exposed adapter/state.
    """
    for attr in ("consensus_view", "state", "sync", "deps", "core"):
        obj = getattr(service, attr, None)
        if obj is None:
            continue
        for n in ("head_height", "get_head_height"):
            v = getattr(obj, n, None)
            if isinstance(v, int):
                return v
            if callable(v):
                try:
                    return int(v())
                except Exception:
                    pass
    # Sometimes exposed directly
    for n in ("head_height", "get_head_height"):
        v = getattr(service, n, None)
        if isinstance(v, int):
            return v
        if callable(v):
            try:
                return int(v())
            except Exception:
                pass
    return None


async def dial(service, addr: str) -> None:
    """
    Try several likely methods for dialing another peer.
    """
    # Preferred: service.connect(...) or service.dial(...)
    for name in ("connect", "dial", "dial_peer", "add_outbound"):
        f = getattr(service, name, None)
        if callable(f):
            r = f(addr)
            if asyncio.iscoroutine(r):
                await r
            return
    # Else: try connection_manager if present
    cm = getattr(service, "connection_manager", None)
    if cm:
        for name in ("connect", "dial"):
            f = getattr(cm, name, None)
            if callable(f):
                r = f(addr)
                if asyncio.iscoroutine(r):
                    await r
                return
    raise RuntimeError("No dialing method found on service")


# -----------------------------
# Imports (lazy so the test can skip gracefully if P2P is not built)
# -----------------------------

try:
    from p2p.node.service import P2PService as _Service
except Exception:
    _Service = None

try:
    from p2p.config import P2PConfig
except Exception:
    P2PConfig = None


@pytest.mark.asyncio
async def test_end_to_end_two_nodes_connect_and_sync():
    if _Service is None or P2PConfig is None:
        pytest.skip("P2P service/config not available in this build")

    # Make two minimal configs on ephemeral ports.
    port_a = find_free_port()
    port_b = find_free_port()

    cfg_a = P2PConfig(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_a}"],
        seeds=[],
        enable_tcp=True,
        enable_quic=False,
        enable_ws=False,
        max_peers=16,
        gossip=True,
        sync_headers=True,
        sync_blocks=False,
    )
    cfg_b = P2PConfig(
        listen_addrs=[f"/ip4/127.0.0.1/tcp/{port_b}"],
        seeds=[],
        enable_tcp=True,
        enable_quic=False,
        enable_ws=False,
        max_peers=16,
        gossip=True,
        sync_headers=True,
        sync_blocks=False,
    )

    # Instantiate services
    node_a = _Service(config=cfg_a)
    node_b = _Service(config=cfg_b)

    # Some implementations require explicit .setup() before .start()
    for svc in (node_a, node_b):
        setup = getattr(svc, "setup", None)
        if callable(setup):
            r = setup()
            if asyncio.iscoroutine(r):
                await r

    # Start both nodes
    start_a = node_a.start()
    if asyncio.iscoroutine(start_a):
        await start_a
    start_b = node_b.start()
    if asyncio.iscoroutine(start_b):
        await start_b

    try:
        # Compose a dialable address to A from B (prefer multiaddr string if service exposes it)
        listen_addrs = getattr(node_a, "listen_addrs", None) or getattr(
            node_a, "listening", None
        )
        if listen_addrs:
            # Already in multiaddr-like form
            addr_a = listen_addrs[0]
        else:
            addr_a = f"/ip4/127.0.0.1/tcp/{port_a}"

        # B dials A
        await dial(node_b, addr_a)

        # Wait until both peers see each other
        ok = await eventually(
            lambda: get_peer_count(node_a) >= 1 and get_peer_count(node_b) >= 1,
            timeout=10.0,
        )
        assert (
            ok
        ), f"Peer connection not established: A={get_peer_count(node_a)} B={get_peer_count(node_b)}"

        # Give the protocol some time to run HELLO/IDENTIFY and (optional) header-sync kickoff
        await asyncio.sleep(0.25)

        # If both nodes expose head heights, assert they match (no-op sync from genesis is fine)
        h_a = get_head_height(node_a)
        h_b = get_head_height(node_b)
        if h_a is not None and h_b is not None:
            # Allow a brief convergence window
            converged = await eventually(
                lambda: get_head_height(node_b) == get_head_height(node_a), timeout=5.0
            )
            assert converged, f"Head heights diverged: A={h_a} B={h_b}"

        # If an explicit sync method exists, try it and re-check.
        for name in ("request_header_sync", "kick_header_sync", "sync_now"):
            f = getattr(node_b, name, None)
            if callable(f):
                r = f()
                if asyncio.iscoroutine(r):
                    await r
                await asyncio.sleep(0.1)
                h_a2 = get_head_height(node_a)
                h_b2 = get_head_height(node_b)
                if h_a2 is not None and h_b2 is not None:
                    assert h_a2 == h_b2, f"Header sync failed: A={h_a2} B={h_b2}"
                break

    finally:
        # Stop both nodes (best-effort)
        for svc in (node_b, node_a):
            stop = getattr(svc, "stop", None)
            if callable(stop):
                try:
                    r = stop()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
