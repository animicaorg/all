"""
Shared helpers for Animica P2P tests.

These utilities are imported by individual test modules, e.g.:

    from p2p.tests import (
        free_port, tcp_multiaddr, ws_multiaddr, quic_multiaddr,
        async_service, start_p2p_service, wait_for, hexdump,
    )

They intentionally avoid pytest-specific fixtures so they can be used from
both pytest and ad-hoc scripts.
"""
from __future__ import annotations

import asyncio as _asyncio
import contextlib as _contextlib
import importlib
import os
import socket
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Iterable, Optional

# Try to speed up asyncio if uvloop is present (harmless if not).
try:  # pragma: no cover
    import uvloop  # type: ignore
    uvloop.install()
except Exception:
    pass


# --------------------------------------------------------------------------------------
# Networking helpers
# --------------------------------------------------------------------------------------
def free_port() -> int:
    """Return an available TCP port on 127.0.0.1 (best-effort, race-free in practice)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(s.getsockname()[1])


def tcp_multiaddr(port: int) -> str:
    """Build a TCP multiaddr string for localhost."""
    return f"/ip4/127.0.0.1/tcp/{int(port)}"


def ws_multiaddr(port: int) -> str:
    """Build a WebSocket multiaddr string for localhost."""
    return f"/ip4/127.0.0.1/tcp/{int(port)}/ws"


def quic_multiaddr(port: int) -> str:
    """Build a QUIC multiaddr string for localhost (udp with alpn)."""
    return f"/ip4/127.0.0.1/udp/{int(port)}/quic"


# --------------------------------------------------------------------------------------
# Minimal boot config + service lifecycle used by tests
# --------------------------------------------------------------------------------------
@dataclass
class BootCfg:
    chain_id: int = 1337
    listen_addrs: list[str] = None  # type: ignore[assignment]
    seeds: list[str] = None         # type: ignore[assignment]
    enable_quic: bool = False
    enable_ws: bool = False
    nat: bool = False

    def __post_init__(self) -> None:
        if self.listen_addrs is None:
            self.listen_addrs = []
        if self.seeds is None:
            self.seeds = []


async def start_p2p_service(cfg: BootCfg):
    """
    Start a P2PService and return it. The caller is responsible for stopping it.

    Raises a helpful ImportError if the P2P module is not available.
    """
    try:
        P2PService = importlib.import_module("p2p.node.service").P2PService  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover - exercised only in partial installs
        raise ImportError(
            "p2p.node.service.P2PService not available. Ensure the p2p package is installed."
        ) from e

    service = P2PService(
        listen_addrs=cfg.listen_addrs,
        seeds=cfg.seeds,
        chain_id=cfg.chain_id,
        enable_quic=cfg.enable_quic,
        enable_ws=cfg.enable_ws,
        nat=cfg.nat,
        deps=None,  # tests generally don't need full deps wiring
    )
    await service.start()
    return service


@_contextlib.asynccontextmanager
async def async_service(cfg: BootCfg) -> AsyncIterator[object]:
    """
    Async context manager to run a P2P service for the scope:

        async with async_service(BootCfg(...)) as s:
            ... use s ...
    """
    svc = await start_p2p_service(cfg)
    try:
        yield svc
    finally:
        try:
            await getattr(svc, "stop")()
        except Exception:  # pragma: no cover
            pass


# --------------------------------------------------------------------------------------
# Small async/test utilities
# --------------------------------------------------------------------------------------
async def wait_for(
    predicate: Callable[[], Awaitable[bool]] | Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.05,
) -> bool:
    """
    Poll `predicate` until it returns True or timeout elapses.
    Works with sync or async predicates. Returns True on success, False on timeout.
    """
    deadline = _asyncio.get_event_loop().time() + max(0.0, float(timeout))
    while True:
        try:
            ok = predicate()
            if _asyncio.iscoroutine(ok):  # type: ignore[arg-type]
                ok = await ok  # type: ignore[assignment]
        except Exception:
            ok = False
        if ok:
            return True
        if _asyncio.get_event_loop().time() >= deadline:
            return False
        await _asyncio.sleep(max(0.0, float(interval)))


def hexdump(b: bytes, width: int = 16) -> str:
    """Pretty hexdump string for small payloads (used in assertion diffs)."""
    out_lines = []
    for i in range(0, len(b), width):
        chunk = b[i : i + width]
        hexpart = " ".join(f"{x:02x}" for x in chunk)
        asciip = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        out_lines.append(f"{i:04x}  {hexpart:<{width*3}}  {asciip}")
    return "\n".join(out_lines)


# --------------------------------------------------------------------------------------
# Env toggles used by tests (documented, not required)
# --------------------------------------------------------------------------------------
def env_enabled(name: str, default: bool = False) -> bool:
    """
    Read a boolean toggle from environment variables:
      - '1', 'true', 'yes', 'on' => True
      - '0', 'false', 'no', 'off' => False
    """
    val = os.getenv(name)
    if val is None:
        return bool(default)
    v = val.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    return bool(default)


__all__ = [
    # ports & addrs
    "free_port",
    "tcp_multiaddr",
    "ws_multiaddr",
    "quic_multiaddr",
    # booting
    "BootCfg",
    "start_p2p_service",
    "async_service",
    # utils
    "wait_for",
    "hexdump",
    "env_enabled",
]
