from __future__ import annotations

"""
Animica P2P transports
======================

This subpackage hosts interchangeable, authenticated+encrypted transports used by
the P2P layer:

- TCP  : length-prefixed streams over TLS 1.3 (AEAD at the frame layer).
- QUIC : UDP-based streams with TLS 1.3, ALPN "animica/1".
- WS   : WebSocket (binary), for browser/edge compatibility.

Each transport provides the same abstract interface defined in
``p2p.transport.base``: ``Transport``, ``Conn``, and ``Stream``.

Importing this package does *not* import heavy optional deps (e.g. aioquic) so
module import stays fast. Use ``available_transports()`` to probe what's usable.
"""

from typing import Dict

try:  # Prefer the real ABCs when base.py is present
    from .base import Transport, Conn, Stream  # type: ignore
except Exception:  # pragma: no cover - fallback Protocols for early-import contexts
    from typing import Protocol, runtime_checkable, Any

    @runtime_checkable
    class Stream(Protocol):  # minimal surface for type-checkers
        async def send(self, data: bytes) -> None: ...
        async def recv(self, max_bytes: int = ...) -> bytes: ...
        async def close(self) -> None: ...

    @runtime_checkable
    class Conn(Protocol):
        async def open_stream(self) -> Stream: ...
        async def close(self) -> None: ...

    @runtime_checkable
    class Transport(Protocol):
        name: str
        async def listen(self, addr: str) -> None: ...
        async def dial(self, addr: str) -> Conn: ...
        async def close(self) -> None: ...

__all__ = ["Transport", "Conn", "Stream", "available_transports", "has_tcp", "has_quic", "has_ws"]


def has_tcp() -> bool:
    try:
        from . import tcp  # noqa: F401
        return True
    except Exception:
        return False


def has_quic() -> bool:
    try:
        from . import quic  # noqa: F401
        return True
    except Exception:
        return False


def has_ws() -> bool:
    try:
        from . import ws  # noqa: F401
        return True
    except Exception:
        return False


def available_transports() -> Dict[str, bool]:
    """
    Return a small capability map without importing heavy deps.

    Example:
        {"tcp": True, "quic": False, "ws": True}
    """
    return {"tcp": has_tcp(), "quic": has_quic(), "ws": has_ws()}
