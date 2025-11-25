from __future__ import annotations

import asyncio
import contextlib
import os
import ssl
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Optional, Tuple

from .base import (
    Transport,
    Conn,
    Stream,
    ListenConfig,
    ConnInfo,
    CloseCode,
    MAX_FRAME_DEFAULT,
    TransportError,
    StreamClosed,
)

ALPN = "animica/1"

# Optional dependency: aioquic
try:
    from aioquic.asyncio import QuicConnectionProtocol
    from aioquic.asyncio.client import connect as quic_connect
    from aioquic.asyncio.server import serve as quic_serve
    from aioquic.quic.configuration import QuicConfiguration
except Exception as _e:  # pragma: no cover - import error path
    QuicConnectionProtocol = None  # type: ignore[assignment]
    quic_connect = None  # type: ignore[assignment]
    quic_serve = None  # type: ignore[assignment]
    QuicConfiguration = None  # type: ignore[assignment]
    _AIOQUIC_IMPORT_ERROR = _e
else:
    _AIOQUIC_IMPORT_ERROR = None


# --- certificate helper (prefers repo's self-signed node cert) ----------------

def _ensure_quic_cert() -> Tuple[str, str]:
    """
    Try to locate/generate a self-signed certificate for QUIC dev usage.

    Prefers p2p.crypto.cert.ensure_node_cert(); falls back to an ephemeral cert
    in ~/.animica/quic-dev/ using the 'cryptography' package.
    """
    # 1) Preferred: repo helper
    with contextlib.suppress(Exception):
        from p2p.crypto.cert import ensure_node_cert  # type: ignore
        cert, key = ensure_node_cert()
        if os.path.exists(cert) and os.path.exists(key):
            return cert, key

    # 2) Fallback: generate ephemeral self-signed (dev only)
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime as _dt
    except Exception as e:  # pragma: no cover - rare path
        raise RuntimeError(
            "QUIC requires a certificate. "
            "Install 'cryptography' or provide p2p.crypto.cert.ensure_node_cert()."
        ) from e

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"animica-quic-dev")])
    now = _dt.datetime.utcnow()
    cert_obj = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(priv.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(u"localhost")]), critical=False)
        .sign(priv, hashes.SHA256())
    )

    out_dir = os.path.expanduser("~/.animica/quic-dev")
    os.makedirs(out_dir, exist_ok=True)
    cert_path = os.path.join(out_dir, "dev-cert.pem")
    key_path = os.path.join(out_dir, "dev-key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert_obj.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(
            priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    return cert_path, key_path


# --- Simple stream framing (length-prefixed) over QUIC stream ------------------

LEN_HDR_SIZE = 4  # uint32 BE


def _pack_len(n: int) -> bytes:
    return n.to_bytes(4, "big")


def _unpack_len(b: bytes) -> int:
    return int.from_bytes(b, "big")


@dataclass(slots=True)
class _StreamQueues:
    rx_queue: "asyncio.Queue[bytes]"
    rx_buf: bytearray
    send_lock: asyncio.Lock


class _SingleStreamQuicProto(QuicConnectionProtocol):
    """
    A small QUIC protocol that exposes a single bidirectional logical stream with
    our usual 4B length-prefixed framing. This keeps the API identical to TCP.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._connected = asyncio.Event()
        self._closed = asyncio.Event()
        self._stream_id: Optional[int] = None
        self._streams: Dict[int, _StreamQueues] = {}
        self._accept_q: "asyncio.Queue[int]" = asyncio.Queue()

    # ---- lifecycle ----

    def connection_made(self, transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        # Connected (handshake completes shortly)
        # We'll set the event in handshake_completed().

    def handshake_completed(self, event) -> None:  # type: ignore[override]
        # Server: will get streams initiated by peer.
        # Client: we'll create one proactively in open_or_get_stream().
        self._connected.set()

    def connection_lost(self, exc: Optional[Exception]) -> None:  # type: ignore[override]
        super().connection_lost(exc)
        self._closed.set()

    # ---- stream plumbing ----

    def _ensure_stream_state(self, stream_id: int) -> _StreamQueues:
        st = self._streams.get(stream_id)
        if st is None:
            st = _StreamQueues(rx_queue=asyncio.Queue(), rx_buf=bytearray(), send_lock=asyncio.Lock())
            self._streams[stream_id] = st
        return st

    def stream_created(self, stream_id: int) -> None:  # type: ignore[override]
        self._ensure_stream_state(stream_id)
        # Let acceptor know a stream exists (server path).
        self._accept_q.put_nowait(stream_id)

    def stream_data_received(self, stream_id: int, data: bytes, end_stream: bool) -> None:  # type: ignore[override]
        st = self._ensure_stream_state(stream_id)
        st.rx_buf.extend(data)

        # Parse length-prefixed frames
        buf = st.rx_buf
        while True:
            if len(buf) < LEN_HDR_SIZE:
                break
            n = _unpack_len(buf[0:4])
            if n < 0:
                # invalid, close
                self._quic.close(error_code=0x01, reason_phrase="invalid frame length")
                return
            if len(buf) < LEN_HDR_SIZE + n:
                break
            payload = bytes(buf[4 : 4 + n])
            del buf[: 4 + n]
            st.rx_queue.put_nowait(payload)

        if end_stream:
            # No special handling; app will see empty reads if desired
            pass

        self.transmit()

    # ---- app API ----

    async def wait_connected(self, timeout: Optional[float] = None) -> None:
        if timeout and timeout > 0:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        else:
            await self._connected.wait()

    async def open_or_get_stream(self) -> int:
        """
        Client path: create a bidi stream on first use.
        Server path: wait for a stream from peer (stream_created).
        """
        if self._stream_id is not None:
            return self._stream_id

        # Prefer an already-created stream (server)
        with contextlib.suppress(asyncio.TimeoutError):
            sid = await asyncio.wait_for(self._accept_q.get(), timeout=0.0)
            self._stream_id = sid
            return sid

        # Otherwise create (client)
        sid = self._quic.get_next_available_stream_id(is_unidirectional=False)
        self._quic.send_stream_data(sid, b"", end_stream=False)
        self._stream_id = sid
        self.transmit()
        return sid

    async def send_frame(self, stream_id: int, payload: bytes) -> None:
        st = self._ensure_stream_state(stream_id)
        if self._closed.is_set():
            raise StreamClosed("QUIC connection closed")
        if not payload:
            # send empty frame as keepalive if needed
            data = _pack_len(0)
        else:
            data = _pack_len(len(payload)) + payload
        async with st.send_lock:
            self._quic.send_stream_data(stream_id, data, end_stream=False)
            self.transmit()

    async def recv_frame(self, stream_id: int) -> bytes:
        st = self._ensure_stream_state(stream_id)
        return await st.rx_queue.get()

    def close(self, code: int = 0) -> None:  # not async - aioquic API
        if not self._closed.is_set():
            self._quic.close(error_code=code, reason_phrase="app close")
            self.transmit()
            self._closed.set()


# --- Stream / Conn wrappers ----------------------------------------------------

class QuicStream(Stream):
    __slots__ = ("_conn", "_id")

    def __init__(self, conn: "QuicConn", stream_id: int):
        super().__init__(stream_id)
        self._conn = conn
        self._id = stream_id

    async def send(self, data: bytes) -> None:
        await self._conn._proto.send_frame(self._id, data)
        self._bytes_sent += len(data)

    async def recv(self, max_bytes: Optional[int] = None) -> bytes:
        data = await self._conn._proto.recv_frame(self._id)
        self._bytes_recv += len(data)
        if max_bytes is not None and len(data) > max_bytes:
            return data[:max_bytes]
        return data

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        # QUIC streams can be half-closed, but we keep it simple:
        self._conn._proto.close(int(code))

    async def reset(self, code: CloseCode = CloseCode.PROTOCOL_ERROR) -> None:
        self._conn._proto.close(int(code))


class QuicConn(Conn):
    __slots__ = ("_proto", "_stream", "_max_frame")

    def __init__(self, proto: _SingleStreamQuicProto, info: ConnInfo, max_frame: int):
        super().__init__(info)
        self._proto = proto
        self._stream: Optional[QuicStream] = None
        self._max_frame = max_frame

    async def open_stream(self) -> Stream:
        if self.closed:
            raise TransportError("connection closed")
        sid = await self._proto.open_or_get_stream()
        if self._stream is None:
            self._stream = QuicStream(self, sid)
        return self._stream

    async def accept_streams(self) -> AsyncIterator[Stream]:
        # Server side yields at most one logical stream.
        if self._stream is None:
            sid = await self._proto.open_or_get_stream()
            self._stream = QuicStream(self, sid)
        yield self._stream

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        if self._closed:
            return
        self._closed = True
        self._proto.close(int(code))

    async def wait_closed(self) -> None:
        # best-effort
        await asyncio.sleep(0)


# --- Transport ----------------------------------------------------------------

class QuicTransport(Transport):
    """
    QUIC transport using aioquic with ALPN "animica/1".

    Security:
      - Uses TLS 1.3 over QUIC. For dev/test we allow self-signed server certs.
      - For production, configure proper certificates and CA roots.

    Dev notes:
      - This transport already encrypts/authenticates the stream; do NOT re-wrap
        with the Kyber+HKDF AEAD used by the TCP transport.
      - We still use length-prefixed frames to keep a consistent Stream API.
    """

    name = "quic"

    def __init__(self):
        if _AIOQUIC_IMPORT_ERROR is not None:  # pragma: no cover
            raise RuntimeError(
                "aioquic is required for QuicTransport. "
                "pip install aioquic\nCaused by: %r" % (_AIOQUIC_IMPORT_ERROR,)
            )
        self._server = None
        self._incoming: "asyncio.Queue[QuicConn]" = asyncio.Queue()
        self._listen_cfg: Optional[ListenConfig] = None

    # --- helpers ---

    @staticmethod
    def _parse_addr(addr: str) -> Tuple[str, int]:
        # Accept "quic://host:port" or "host:port"
        if addr.startswith("quic://"):
            addr = addr[len("quic://") :]
        if ":" not in addr:
            raise ValueError(f"missing port in addr: {addr!r}")
        host, port_s = addr.rsplit(":", 1)
        return host or "0.0.0.0", int(port_s)

    # --- Transport API ---

    async def listen(self, config: ListenConfig) -> None:
        if self._server is not None:
            return
        host, port = self._parse_addr(config.addr)
        self._listen_cfg = config

        cert_path, key_path = _ensure_quic_cert()

        quic_cfg = QuicConfiguration(
            is_client=False,
            alpn_protocols=[ALPN],
        )
        # Load self-signed dev cert
        quic_cfg.load_cert_chain(cert_path, key_path)

        # Protocol factory to enqueue new connections
        transport_self = self

        class _ServerProto(_SingleStreamQuicProto):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def connection_made(self, t) -> None:  # type: ignore[override]
                super().connection_made(t)

            def connection_terminated(self, event) -> None:  # type: ignore[override]
                super().connection_terminated(event)

            # When handshake completes and first stream is created, the acceptor
            # on the transport will construct a QuicConn wrapper.

        async def _on_connected(proto: _SingleStreamQuicProto):
            # Wait until handshake done
            await proto.wait_connected()
            info = ConnInfo(
                local_addr=f"{host}:{port}",
                remote_addr=str(getattr(proto._transport, 'get_extra_info', lambda *_: None)('peername')),
                is_outbound=False,
            )
            conn = QuicConn(proto, info=info, max_frame=config.max_frame_bytes)
            await transport_self._incoming.put(conn)

        # server() returns asyncio.Server-like object
        self._server = await quic_serve(
            host,
            port,
            configuration=quic_cfg,
            create_protocol=lambda *a, **kw: _ServerProto(*a, **kw),
        )

        # Background task to wrap incoming protocols into QuicConn objects
        async def _accept_loop():
            # aioquic doesn't expose direct accept queue; we wrap on demand when a
            # client interacts. Here, we periodically scan transports' protocols.
            # Simpler: rely on open_stream/accept_streams in Conn after wait_connected.
            # Nothing to do here; connections are enqueued from within the protocol
            # after handshake (via _on_connected). We'll attach hooks below.
            pass

        # Monkey-patch server's protocol factory to schedule _on_connected
        # by wrapping connection_made; we do it via transport's 'serve' argument:
        # We can't easily intercept protocols from aioquic; instead, we register a task
        # on each new protocol by overriding QuicConnectionProtocol.connection_made within factory.
        orig_factory = self._server._protocol_factory  # type: ignore[attr-defined]

        def _factory(*a, **kw):
            proto = orig_factory(*a, **kw)
            # Schedule enqueue after handshake completes.
            asyncio.get_event_loop().create_task(_on_connected(proto))
            return proto

        # Replace factory (best effort; aioquic exposes attribute on server)
        with contextlib.suppress(Exception):
            self._server._protocol_factory = _factory  # type: ignore[attr-defined]

    async def accept(self) -> QuicConn:
        if self._server is None:
            raise TransportError("listen() must be called before accept()")
        return await self._incoming.get()

    async def dial(self, addr: str, timeout: Optional[float] = None) -> QuicConn:
        host, port = self._parse_addr(addr)

        cfg = QuicConfiguration(is_client=True, alpn_protocols=[ALPN])
        # Dev: skip verification (self-signed servers). For production, set proper roots.
        cfg.verify_mode = ssl.CERT_NONE

        class _ClientProto(_SingleStreamQuicProto):
            pass

        async def _dial() -> QuicConn:
            async with quic_connect(host, port, configuration=cfg, create_protocol=_ClientProto) as client:
                proto: _SingleStreamQuicProto = client  # type: ignore[assignment]
                await proto.wait_connected()
                info = ConnInfo(
                    local_addr=str(getattr(proto._transport, 'get_extra_info', lambda *_: None)('sockname')),
                    remote_addr=str(getattr(proto._transport, 'get_extra_info', lambda *_: None)('peername')),
                    is_outbound=True,
                )
                return QuicConn(proto, info=info, max_frame=(self._listen_cfg.max_frame_bytes if self._listen_cfg else MAX_FRAME_DEFAULT))

        try:
            if timeout and timeout > 0:
                return await asyncio.wait_for(_dial(), timeout=timeout)
            return await _dial()
        except asyncio.TimeoutError as e:
            raise TransportError(f"quic dial timeout to {addr}") from e
        except Exception as e:
            raise TransportError(f"quic dial failed to {addr}: {e}") from e

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    def is_listening(self) -> bool:
        return self._server is not None

    def addresses(self) -> list[str]:
        if not self._server:
            return []
        addrs: list[str] = []
        for sock in self._server.sockets or []:  # type: ignore[attr-defined]
            try:
                host, port = sock.getsockname()[:2]
                addrs.append(f"quic://{host}:{port}")
            except Exception:
                continue
        return addrs
