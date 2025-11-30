from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Tuple

from .base import (
    Transport,
    Conn,
    Stream,
    ListenConfig,
    ConnInfo,
    CloseCode,
    MAX_FRAME_DEFAULT,
    HandshakeError,
    TransportError,
    StreamClosed,
)

# Cryptographic handshake → AEAD keys
# Expected API (from p2p.crypto.handshake):
#   perform_handshake_tcp(reader, writer, is_outbound: bool, *,
#                         prologue: bytes = b"animica/tcp/1",
#                         timeout: Optional[float] = None) ->
#       Tuple[TxAead, RxAead, ConnInfo]
# where TxAead/RxAead expose:
#   seal(plaintext: bytes, aad: bytes, nonce: int) -> bytes
#   open(ciphertext: bytes, aad: bytes, nonce: int) -> bytes
from p2p.crypto.handshake import perform_handshake_tcp  # type: ignore[attr-defined]

# Frame header: 4 bytes ciphertext length (network order)
LEN_HDR = struct.Struct("!I")


@dataclass(slots=True)
class _StreamState:
    send_seq: int = 0
    recv_seq: int = 0
    send_closed: bool = False
    recv_closed: bool = False


class TcpStream(Stream):
    """
    Single logical bidirectional stream over a TCP connection.

    Framing:
      - Each application frame is AEAD-sealed with AAD = b"S0" || seq (u64 BE).
      - Wire format: len(4B) || ciphertext
      - Nonce schedule: per-direction strictly increasing u64 derived from seq.

    Notes:
      - Because TCP is single-stream, id is always 0.
    """

    __slots__ = (
        "_conn",
        "_state",
        "_write_lock",
        "_read_lock",
        "_max_frame",
    )

    def __init__(self, conn: "TcpConn", stream_id: int = 0):
        super().__init__(stream_id)
        self._conn = conn
        self._state = _StreamState()
        self._write_lock = asyncio.Lock()
        self._read_lock = asyncio.Lock()
        self._max_frame = conn._max_frame

    def _aad(self, prefix: bytes, seq: int) -> bytes:
        # AAD = prefix(2 bytes) || seq (u64 big-endian)
        return prefix + seq.to_bytes(8, "big")

    async def send(self, data: bytes) -> None:
        if self._state.send_closed or self._conn.closed:
            raise StreamClosed("send() on closed stream")

        if len(data) > self._max_frame:
            raise ValueError(f"frame too large ({len(data)} > {self._max_frame})")

        async with self._write_lock:
            seq = self._state.send_seq
            aad = self._aad(b"S0", seq)
            ct = self._conn._tx_aead.seal(data, aad=aad, nonce=seq)  # type: ignore[attr-defined]
            frame_len = LEN_HDR.pack(len(ct))
            try:
                self._conn._writer.write(frame_len)
                self._conn._writer.write(ct)
                await self._conn._writer.drain()
            except (ConnectionError, asyncio.CancelledError) as e:
                await self._conn.close(CloseCode.INTERNAL_ERROR)
                raise StreamClosed(f"send failed: {e}") from e
            self._state.send_seq += 1
            self._bytes_sent += len(data)

    async def recv(self, max_bytes: Optional[int] = None) -> bytes:
        if self._state.recv_closed or self._conn.closed:
            raise StreamClosed("recv() on closed stream")

        async with self._read_lock:
            try:
                hdr = await self._conn._reader.readexactly(LEN_HDR.size)
            except asyncio.IncompleteReadError:
                # Peer performed orderly shutdown.
                self._state.recv_closed = True
                return b""

            (n,) = LEN_HDR.unpack(hdr)
            if n > self._max_frame + 1024 * 1024:  # small sanity slack for AEAD tag
                await self._conn.close(CloseCode.MESSAGE_TOO_BIG)
                raise StreamClosed(f"ciphertext length too large: {n}")

            try:
                ct = await self._conn._reader.readexactly(n)
            except asyncio.IncompleteReadError as e:
                await self._conn.close(CloseCode.INTERNAL_ERROR)
                raise StreamClosed("unexpected EOF while reading frame") from e

            seq = self._state.recv_seq
            aad = self._aad(b"S0", seq)
            try:
                pt = self._conn._rx_aead.open(ct, aad=aad, nonce=seq)  # type: ignore[attr-defined]
            except Exception as e:  # AEAD fail
                await self._conn.close(CloseCode.PROTOCOL_ERROR)
                raise StreamClosed(f"AEAD open failed: {e}") from e

            self._state.recv_seq += 1
            self._bytes_recv += len(pt)

            if max_bytes is not None and len(pt) > max_bytes:
                # If requested, bound the returned payload (caller can loop).
                return pt[:max_bytes]
            return pt

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        # Half-close send side. We don't have half-close signaling on our
        # framing, so we only mark local half closed. Conn.close() tears down
        # the TCP socket.
        self._state.send_closed = True

    async def reset(self, code: CloseCode = CloseCode.PROTOCOL_ERROR) -> None:
        await self._conn.close(code)
        self._state.send_closed = True
        self._state.recv_closed = True


class TcpConn(Conn):
    """
    A secure, AEAD-protected connection over TCP with a single logical stream (id=0).
    """

    __slots__ = ("_reader", "_writer", "_tx_aead", "_rx_aead", "_stream", "_max_frame")

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        tx_aead,
        rx_aead,
        info: ConnInfo,
        max_frame: int = MAX_FRAME_DEFAULT,
    ):
        super().__init__(info)
        self._reader = reader
        self._writer = writer
        self._tx_aead = tx_aead
        self._rx_aead = rx_aead
        self._stream = TcpStream(self, stream_id=0)
        self._max_frame = max_frame

    async def open_stream(self) -> Stream:
        if self.closed:
            raise TransportError("connection closed")
        return self._stream

    async def accept_streams(self) -> AsyncIterator[Stream]:
        # Single-stream transport: yield once and then return.
        yield self._stream

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
        finally:
            # Wait for the underlying transport to finish closing.
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

    async def wait_closed(self) -> None:
        while not self._closed:
            await asyncio.sleep(0)  # give control back to the loop


import contextlib
from urllib.parse import urlparse


class TcpTransport(Transport):
    """
    Asyncio TCP transport with AEAD framing.

    Address format:
      tcp://host:port
      tcp://0.0.0.0:31000
      tcp://:31000            (bind all)
    """

    name = "tcp"

    def __init__(self):
        self._server: Optional[asyncio.AbstractServer] = None
        self._incoming: "asyncio.Queue[TcpConn]" = asyncio.Queue()
        self._listen_cfg: Optional[ListenConfig] = None

    # ---------- helpers ----------

    @staticmethod
    def _parse_tcp_addr(addr: str) -> Tuple[str, int]:
        if addr.startswith("tcp://"):
            parsed = urlparse(addr)
            host = parsed.hostname or ""
            port = parsed.port
            if port is None:
                raise ValueError(f"missing port in addr: {addr}")
            return (host, port)
        # Fallback: host:port
        if ":" in addr:
            host, port_s = addr.rsplit(":", 1)
            return (host, int(port_s))
        raise ValueError(f"unsupported tcp addr format: {addr}")

    # ---------- Transport API ----------

    async def listen(self, config: ListenConfig) -> None:
        if self._server is not None:
            return  # idempotent

        host, port = self._parse_tcp_addr(config.addr)
        self._listen_cfg = config

        async def _on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            # Handshake as responder (inbound)
            try:
                tx_aead, rx_aead, info = await perform_handshake_tcp(
                    reader,
                    writer,
                    is_outbound=False,
                    prologue=b"animica/tcp/1",
                    timeout=15.0,
                )
                info.local_addr = f"{host}:{port}"
                try:
                    peer = writer.get_extra_info("peername")
                    if isinstance(peer, (tuple, list)) and len(peer) >= 2:
                        info.remote_addr = f"{peer[0]}:{peer[1]}"
                except Exception:
                    pass
                info.is_outbound = False
                conn = TcpConn(
                    reader,
                    writer,
                    tx_aead,
                    rx_aead,
                    info=info,
                    max_frame=config.max_frame_bytes,
                )
                await self._incoming.put(conn)
            except Exception as e:
                # On handshake failure, ensure socket is closed.
                with contextlib.suppress(Exception):
                    writer.close()
                # No await wait_closed to avoid hanging in case of malformed peers.
                return

        self._server = await asyncio.start_server(
            _on_client,
            host=host or None,
            port=port,
            backlog=config.backlog,
        )

    async def accept(self) -> TcpConn:
        if self._server is None:
            raise TransportError("listen() must be called before accept()")
        return await self._incoming.get()

    async def dial(self, addr: str, timeout: Optional[float] = None) -> TcpConn:
        host, port = self._parse_tcp_addr(addr)

        async def _dial():
            reader, writer = await asyncio.open_connection(host=host or None, port=port)
            tx_aead, rx_aead, info = await perform_handshake_tcp(
                reader,
                writer,
                is_outbound=True,
                prologue=b"animica/tcp/1",
                timeout=15.0,
            )
            try:
                local = writer.get_extra_info("sockname")
                if isinstance(local, (tuple, list)) and len(local) >= 2:
                    info.local_addr = f"{local[0]}:{local[1]}"
                peer = writer.get_extra_info("peername")
                if isinstance(peer, (tuple, list)) and len(peer) >= 2:
                    info.remote_addr = f"{peer[0]}:{peer[1]}"
            except Exception:
                pass
            info.is_outbound = True
            max_frame = self._listen_cfg.max_frame_bytes if self._listen_cfg else MAX_FRAME_DEFAULT
            return TcpConn(reader, writer, tx_aead, rx_aead, info=info, max_frame=max_frame)

        try:
            if timeout is not None and timeout > 0:
                return await asyncio.wait_for(_dial(), timeout=timeout)
            return await _dial()
        except asyncio.TimeoutError as e:
            raise TransportError(f"dial timeout to {addr}") from e
        except HandshakeError:
            raise
        except Exception as e:
            raise TransportError(f"dial failed to {addr}: {e}") from e

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    # ---------- misc ----------

    def is_listening(self) -> bool:
        return self._server is not None

    def addresses(self) -> list[str]:
        if not self._server:
            return []
        addrs: list[str] = []
        for sock in self._server.sockets or []:
            try:
                host, port = sock.getsockname()[:2]
                addrs.append(f"tcp://{host}:{port}")
            except Exception:
                continue
        return addrs


# Compatibility alias for callers expecting the older name
TCPTransport = TcpTransport
