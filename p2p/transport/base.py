from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import AsyncIterator, Optional, Protocol, Union, runtime_checkable

__all__ = [
    "Transport",
    "Conn",
    "Stream",
    "TransportError",
    "ConnectionClosed",
    "StreamClosed",
    "HandshakeError",
    "ListenConfig",
    "ConnInfo",
    "StreamId",
    "MAX_FRAME_DEFAULT",
]


# --------------------------- #
# Errors & small base types   #
# --------------------------- #


class TransportError(Exception):
    """Base class for transport-level errors."""


class HandshakeError(TransportError):
    """Raised when cryptographic or protocol handshakes fail."""


class ConnectionClosed(TransportError):
    """Raised when using a connection after it is closed."""


class StreamClosed(TransportError):
    """Raised when using a stream after it is closed."""


StreamId = int
MAX_FRAME_DEFAULT = 1 << 20  # 1 MiB sane default


class CloseCode(IntEnum):
    """Reason codes for orderly shutdown; carried as hints across layers."""

    NORMAL = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    INTERNAL_ERROR = 1011
    POLICY_VIOLATION = 1008
    MESSAGE_TOO_BIG = 1009
    TRY_AGAIN_LATER = 1013


@dataclass(slots=True)
class ListenConfig:
    """
    Parameters for listen() across transports.

    addr:     transport-specific address (e.g., "tcp://0.0.0.0:31000",
              "quic://:31000?alpn=animica/1", "ws://0.0.0.0:31001/p2p").
    max_frame_bytes: maximum payload size per frame (unencrypted).
    backlog:  OS listen backlog (if applicable).
    """

    addr: str
    max_frame_bytes: int = MAX_FRAME_DEFAULT
    backlog: int = 128


@dataclass(slots=True)
class ConnInfo:
    """
    Metadata about a live connection after handshake.
    """

    peer_id: Optional[bytes] = None  # p2p.crypto.peer_id bytes (sha3-256(...))
    alpn: Optional[str] = None  # "animica/1" for QUIC/TLS-based transports
    local_addr: Optional[str] = None
    remote_addr: Optional[str] = None
    is_outbound: bool = False
    started_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    # Keying material digests for debugging/telemetry (never raw keys)
    tx_key_sha256: Optional[bytes] = None
    rx_key_sha256: Optional[bytes] = None


# --------------------------- #
# Abstract streaming API      #
# --------------------------- #


class Stream(abc.ABC):
    """
    Byte-stream over an authenticated, encrypted connection.

    Semantics:
      - Ordered, reliable delivery.
      - Back-pressure: send() awaits when buffers are full.
      - Graceful close: close() half-closes the stream for sending; recv() can
        still yield remaining data until EOF. Use reset() for abrupt cancel.
      - Async-context friendly.
    """

    __slots__ = ("_id", "_closed_send", "_closed_recv", "_bytes_sent", "_bytes_recv")

    def __init__(self, stream_id: StreamId):
        self._id: StreamId = stream_id
        self._closed_send: bool = False
        self._closed_recv: bool = False
        self._bytes_sent: int = 0
        self._bytes_recv: int = 0

    @property
    def id(self) -> StreamId:
        return self._id

    @property
    def closed(self) -> bool:
        return self._closed_send and self._closed_recv

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def bytes_recv(self) -> int:
        return self._bytes_recv

    async def __aenter__(self) -> "Stream":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # --- abstract ops ---

    @abc.abstractmethod
    async def send(self, data: bytes) -> None:
        """
        Write bytes to the stream. May suspend for back-pressure.

        Raises:
          StreamClosed if already half-closed for send or after reset().
        """
        ...

    @abc.abstractmethod
    async def recv(self, max_bytes: Optional[int] = None) -> bytes:
        """
        Read up to max_bytes (if provided) or a transport-chosen chunk.

        Returns b"" on EOF (remote half-closed). Raises StreamClosed if locally
        reset or fully closed.
        """
        ...

    @abc.abstractmethod
    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        """
        Half-close (no more sends). Idempotent.
        """
        ...

    @abc.abstractmethod
    async def reset(self, code: CloseCode = CloseCode.PROTOCOL_ERROR) -> None:
        """
        Abruptly terminate both directions. After reset(), send/recv raise StreamClosed.
        """
        ...

    # --- helpers ---

    async def recv_exactly(self, n: int) -> bytes:
        """
        Read exactly n bytes unless EOF occurs sooner.
        """
        if n < 0:
            raise ValueError("n must be >= 0")
        chunks: list[bytes] = []
        got = 0
        while got < n:
            chunk = await self.recv(n - got)
            if chunk == b"":
                break
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)


class Conn(abc.ABC):
    """
    Secure connection carrying one or more Streams.

    Notes:
      - TCP transports typically expose a single logical stream (id=0).
      - QUIC transports support multiple concurrent streams (client- or server-initiated).
      - WS transports expose a single message stream (id=0) with internal framing.

    The connection itself is an async context manager.
    """

    __slots__ = ("_info", "_closed")

    def __init__(self, info: Optional[ConnInfo] = None):
        self._info: ConnInfo = info or ConnInfo()
        self._closed: bool = False

    @property
    def info(self) -> ConnInfo:
        return self._info

    @property
    def closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> "Conn":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # --- abstract ops ---

    @abc.abstractmethod
    async def open_stream(self) -> Stream:
        """
        Open a new bidirectional stream (QUIC), or return the single stream (TCP/WS).
        """
        ...

    @abc.abstractmethod
    async def accept_streams(self) -> AsyncIterator[Stream]:
        """
        Iterate incoming streams until the connection is closed.

        For single-stream transports, yields the single stream once and then
        returns when closed.
        """
        ...

    @abc.abstractmethod
    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        """
        Graceful shutdown of the connection and all streams.
        """
        ...

    @abc.abstractmethod
    async def wait_closed(self) -> None:
        """
        Wait until the transport is fully closed (all resources released).
        """
        ...


class Transport(abc.ABC):
    """
    Transport provides authenticated, encrypted connections and implements:

      - listen(config): bind & start accepting inbound connections.
      - accept(): await next inbound connection (after listen()).
      - dial(addr): connect outbound to a listening peer.
      - close(): stop listening and close all pending accept tasks.

    Each concrete transport should:
      * Use Kyber768 + HKDF-SHA3 for the P2P key schedule (see p2p.crypto.handshake)
        and ChaCha20-Poly1305/AES-GCM as AEAD (see p2p.crypto.aead).
      * Set Conn.info.peer_id and alpn (when applicable).
      * Enforce config.max_frame_bytes at the frame layer.
    """

    name: str = "base"

    @abc.abstractmethod
    async def listen(self, config: ListenConfig) -> None:
        """
        Start listening for inbound connections. Idempotent when called with the
        same address/config.
        """
        ...

    @abc.abstractmethod
    async def accept(self) -> Conn:
        """
        Wait for and return the next inbound connection.

        Raises:
          asyncio.CancelledError if the listener is closed while waiting.
        """
        ...

    @abc.abstractmethod
    async def dial(self, addr: str, timeout: Optional[float] = None) -> Conn:
        """
        Establish an outbound connection.

        Args:
          addr: transport-specific address, e.g. "tcp://host:port".
          timeout: optional overall timeout seconds.

        Raises:
          HandshakeError on cryptographic failures.
          TransportError on network errors.
        """
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """
        Stop listening (if active) and release resources. Idempotent.
        """
        ...

    # --- optional helpers / capabilities ---

    def is_listening(self) -> bool:
        """Lightweight hint for readiness checks; override if you can provide it."""
        return False

    def addresses(self) -> list[str]:
        """Return bound listen addresses (if any)."""
        return []

    # Utility for implementations
    async def _with_timeout(self, coro, timeout: Optional[float]):
        if timeout is None or timeout <= 0:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout)
