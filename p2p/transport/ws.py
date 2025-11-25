from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import ssl
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Tuple, Dict
from urllib.parse import urlparse, parse_qs, urlunparse

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

# Optional deps
try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    from websockets.legacy.client import WebSocketClientProtocol
except Exception as _e:  # pragma: no cover
    websockets = None  # type: ignore
    WebSocketServerProtocol = object  # type: ignore
    WebSocketClientProtocol = object  # type: ignore
    _WEBSOCKETS_IMPORT_ERROR = _e
else:
    _WEBSOCKETS_IMPORT_ERROR = None

# AEAD (we implement a tiny local wrapper; prefers cryptography)
try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except Exception as _e:  # pragma: no cover
    ChaCha20Poly1305 = None  # type: ignore
    _CRYPTO_IMPORT_ERROR = _e
else:
    _CRYPTO_IMPORT_ERROR = None

# HKDF-SHA3-256 from our PQ utils (used for PSK-derived session keys)
try:
    from pq.py.utils.hkdf import hkdf_sha3_256  # type: ignore
except Exception:
    # Minimal local HKDF-SHA3-256 fallback
    def hkdf_sha3_256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
        """
        Very small HKDF (extract+expand) using SHA3-256. For dev only.
        """
        def hmac_sha3(key: bytes, data: bytes) -> bytes:
            import hmac
            return hmac.new(key, data, hashlib.sha3_256).digest()

        prk = hmac_sha3(salt or b"\x00" * hashlib.sha3_256().digest_size, ikm)
        out = b""
        t = b""
        counter = 1
        while len(out) < length:
            t = hmac_sha3(prk, t + info + bytes([counter]))
            out += t
            counter += 1
        return out[:length]


WS_ALPN = "animica/ws/1"
AAD_TX = b"animica/ws/aead/tx/v1"
AAD_RX = b"animica/ws/aead/rx/v1"

NONCE_PREFIX = b"\x00\x00\x00\x00"  # 4 bytes; + 8B seq = 12B nonce for ChaCha20-Poly1305
SEQ_BYTES = 8


class _AeadBox:
    """
    Simple AEAD box with monotonic-nonce schedule (uint64 BE counter embedded in frame).
    """
    __slots__ = ("_aead", "_aad", "_send_seq")

    def __init__(self, key: bytes, aad: bytes) -> None:
        if ChaCha20Poly1305 is None:  # pragma: no cover - rare path in CI
            raise RuntimeError(
                "cryptography is required for AEAD (ChaCha20-Poly1305). "
                f"Install it or disable AEAD. Cause: {_CRYPTO_IMPORT_ERROR!r}"
            )
        if len(key) not in (32,):
            raise ValueError("ChaCha20-Poly1305 key must be 32 bytes")
        self._aead = ChaCha20Poly1305(key)
        self._aad = aad
        self._send_seq = 0

    @staticmethod
    def _nonce_from_seq(seq: int) -> bytes:
        return NONCE_PREFIX + int(seq).to_bytes(SEQ_BYTES, "big")

    def seal(self, plaintext: bytes) -> bytes:
        nonce = self._nonce_from_seq(self._send_seq)
        self._send_seq = (self._send_seq + 1) & ((1 << 64) - 1)
        ct = self._aead.encrypt(nonce, plaintext, self._aad)
        # Prepend 8B sequence so receiver knows which nonce to use
        return nonce[-SEQ_BYTES:] + ct

    def open(self, frame: bytes, expected_seq_opt: Optional[int] = None) -> bytes:
        if len(frame) < SEQ_BYTES + 16:
            raise ValueError("truncated AEAD frame")
        seq = int.from_bytes(frame[:SEQ_BYTES], "big")
        if expected_seq_opt is not None and seq != expected_seq_opt:
            # We allow out-of-order if None; otherwise enforce exactly-once
            raise ValueError("unexpected AEAD sequence")
        nonce = self._nonce_from_seq(seq)
        ct = frame[SEQ_BYTES:]
        return self._aead.decrypt(nonce, ct, self._aad)


def _parse_ws_addr(addr: str) -> Tuple[str, str, int, str, Dict[str, str]]:
    """
    Returns (scheme, host, port, path, query_map[one-value])
    Accepts forms:
      - ws://host:port/path?psk=hex
      - wss://host:port
    """
    u = urlparse(addr)
    if u.scheme not in ("ws", "wss"):
        raise ValueError(f"address must start with ws:// or wss://, got {addr!r}")
    host = u.hostname or "0.0.0.0"
    port = u.port or (443 if u.scheme == "wss" else 80)
    path = u.path or "/p2p"
    q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
    return u.scheme, host, port, path, q


def _derive_session_keys(psk: bytes, role_is_client: bool, ctx: bytes) -> Tuple[bytes, bytes]:
    """
    Derive (tx_key, rx_key) from PSK and role using HKDF-SHA3-256.

    tx_key is the key *this* side uses to send; rx_key is what it expects from peer.
    """
    # A salt that binds to context and WS ALPN
    salt = hashlib.sha3_256(b"animica/ws/salt|" + ctx + b"|" + WS_ALPN.encode()).digest()
    if role_is_client:
        tx = hkdf_sha3_256(psk, salt, b"client->server|" + ctx, 32)
        rx = hkdf_sha3_256(psk, salt, b"server->client|" + ctx, 32)
    else:
        tx = hkdf_sha3_256(psk, salt, b"server->client|" + ctx, 32)
        rx = hkdf_sha3_256(psk, salt, b"client->server|" + ctx, 32)
    return tx, rx


@dataclass(slots=True)
class _WsCrypto:
    tx: Optional[_AeadBox]
    rx: Optional[_AeadBox]
    # When AEAD is disabled, both are None (plaintext over WS/TLS).


class WsStream(Stream):
    __slots__ = ("_conn", "_max_frame")

    def __init__(self, conn: "WsConn", max_frame: int):
        super().__init__(stream_id=0)
        self._conn = conn
        self._max_frame = max_frame

    async def send(self, data: bytes) -> None:
        if self._conn._closed:
            raise StreamClosed("websocket closed")
        if self._conn._crypto.tx:
            frame = self._conn._crypto.tx.seal(data)
        else:
            frame = data
        if len(frame) > self._max_frame:
            raise TransportError(f"frame too large ({len(frame)} > {self._max_frame})")
        await self._conn._ws.send(frame)
        self._bytes_sent += len(data)

    async def recv(self, max_bytes: Optional[int] = None) -> bytes:
        if self._conn._closed:
            raise StreamClosed("websocket closed")
        msg = await self._conn._ws.recv()
        if not isinstance(msg, (bytes, bytearray)):
            raise TransportError("expected binary websocket frame")
        data: bytes
        if self._conn._crypto.rx:
            data = self._conn._crypto.rx.open(bytes(msg))
        else:
            data = bytes(msg)
        self._bytes_recv += len(data)
        if max_bytes is not None and len(data) > max_bytes:
            return data[:max_bytes]
        return data

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        with contextlib.suppress(Exception):
            await self._conn._ws.close(code=int(code), reason="stream close")

    async def reset(self, code: CloseCode = CloseCode.PROTOCOL_ERROR) -> None:
        await self.close(code)


class WsConn(Conn):
    __slots__ = ("_ws", "_crypto", "_stream", "_max_frame")

    def __init__(self, ws, info: ConnInfo, crypto: _WsCrypto, max_frame: int):
        super().__init__(info)
        self._ws: WebSocketClientProtocol | WebSocketServerProtocol = ws
        self._crypto = crypto
        self._stream: Optional[WsStream] = None
        self._max_frame = max_frame

    async def open_stream(self) -> Stream:
        if self._stream is None:
            self._stream = WsStream(self, self._max_frame)
        return self._stream

    async def accept_streams(self) -> AsyncIterator[Stream]:
        if self._stream is None:
            self._stream = WsStream(self, self._max_frame)
        yield self._stream

    async def close(self, code: CloseCode = CloseCode.NORMAL) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._ws.close(code=int(code), reason="conn close")

    async def wait_closed(self) -> None:
        try:
            await self._ws.wait_closed()
        except Exception:
            pass


class WebSocketTransport(Transport):
    """
    WebSocket transport for P2P: single logical stream per connection.

    Security:
      - Use 'wss://' to get TLS on the socket.
      - Optionally enable an *application-layer* AEAD (ChaCha20-Poly1305) using a PSK
        embedded in the URL query (?psk=HEX). Keys are derived per-direction via HKDF-SHA3-256.
        Example listen addr:  ws://0.0.0.0:9031/p2p?psk=c0ffee...deadbeef
        Example dial addr:    wss://example.org:443/p2p?psk=c0ffee...deadbeef

      If PSK is omitted, payloads are sent plaintext over the WS (still protected by TLS if wss://).
      For production, use QUIC (preferred) or wss:// + PSK AEAD.

    Notes:
      - We use *binary* WS frames only.
      - We do not fragment at the app layer; max_frame_bytes controls an upper bound.
    """

    name = "ws"

    def __init__(self):
        if _WEBSOCKETS_IMPORT_ERROR is not None:  # pragma: no cover
            raise RuntimeError(
                "websockets is required for WebSocketTransport. "
                "pip install websockets\nCaused by: %r" % (_WEBSOCKETS_IMPORT_ERROR,)
            )
        self._server = None
        self._incoming: "asyncio.Queue[WsConn]" = asyncio.Queue()
        self._listen_cfg: Optional[ListenConfig] = None
        self._listen_addr: Optional[Tuple[str, str, int, str, Dict[str, str]]] = None

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _mk_ssl_ctx(scheme: str) -> Optional[ssl.SSLContext]:
        if scheme == "wss":
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            # Prefer repo cert via p2p.crypto.cert.ensure_node_cert(); else auto
            with contextlib.suppress(Exception):
                from p2p.crypto.cert import ensure_node_cert  # type: ignore
                cert, key = ensure_node_cert()
                ctx.load_cert_chain(certfile=cert, keyfile=key)
                return ctx
            # Dev fallback: generate ephemeral self-signed if cert helper absent
            # (we reuse QUIC's helper if present)
            with contextlib.suppress(Exception):
                from p2p.transport.quic import _ensure_quic_cert  # type: ignore
                cert, key = _ensure_quic_cert()
                ctx.load_cert_chain(certfile=cert, keyfile=key)
                return ctx
            # As a last resort, return None — caller must provide SSL termination externally.
            return None
        return None

    @staticmethod
    def _mk_client_ssl_ctx(scheme: str) -> Optional[ssl.SSLContext]:
        if scheme == "wss":
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            # In dev, allow self-signed
            if os.getenv("ANIMICA_WS_INSECURE", "1") == "1":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return None

    # ---- Transport API -------------------------------------------------------

    async def listen(self, config: ListenConfig) -> None:
        if self._server is not None:
            return
        self._listen_cfg = config
        scheme, host, port, path, q = _parse_ws_addr(config.addr)
        self._listen_addr = (scheme, host, port, path, q)

        # Pre-derive PSK if present (server role)
        psk_hex = q.get("psk", "")
        psk = bytes.fromhex(psk_hex) if psk_hex else None
        ctx_bytes = hashlib.sha3_256(f"{host}:{port}{path}".encode()).digest()

        ssl_ctx = self._mk_ssl_ctx(scheme)

        async def _handler(ws: WebSocketServerProtocol):
            # Derive per-connection crypto (server receives -> tx=server->client)
            if psk:
                tx_key, rx_key = _derive_session_keys(psk, role_is_client=False, ctx=ctx_bytes)
                crypto = _WsCrypto(tx=_AeadBox(tx_key, AAD_TX), rx=_AeadBox(rx_key, AAD_RX))
            else:
                crypto = _WsCrypto(tx=None, rx=None)

            try:
                peer = ws.remote_address
                local = ws.local_address
            except Exception:
                peer = ("?", 0)
                local = (host, port)

            info = ConnInfo(
                local_addr=f"{local[0]}:{local[1]}",
                remote_addr=f"{peer[0]}:{peer[1]}",
                is_outbound=False,
            )
            conn = WsConn(ws, info=info, crypto=crypto, max_frame=config.max_frame_bytes)
            await self._incoming.put(conn)
            # Keep the handler alive until closed to avoid premature drop
            await ws.wait_closed()

        self._server = await websockets.serve(
            _handler,
            host=host,
            port=port,
            process_request=None,
            subprotocols=[WS_ALPN],
            ssl=ssl_ctx,
            ping_timeout=20,
            max_size=config.max_frame_bytes,
        )

    async def accept(self) -> WsConn:
        if self._server is None:
            raise TransportError("listen() must be called before accept()")
        return await self._incoming.get()

    async def dial(self, addr: str, timeout: Optional[float] = None) -> WsConn:
        scheme, host, port, path, q = _parse_ws_addr(addr)
        psk_hex = q.get("psk", "")
        psk = bytes.fromhex(psk_hex) if psk_hex else None
        ctx_bytes = hashlib.sha3_256(f"{host}:{port}{path}".encode()).digest()

        ssl_ctx = self._mk_client_ssl_ctx(scheme)
        # Rebuild URL without our custom query keys except psk (it's fine to leave it —
        # it's only used locally to derive keys; keeping it is harmless but we can strip)
        qs_pairs = [f"{k}={v}" for k, v in q.items()]
        url = urlunparse((scheme, f"{host}:{port}", path, "", "&".join(qs_pairs), ""))

        async def _do() -> WsConn:
            ws: WebSocketClientProtocol = await websockets.connect(
                url,
                subprotocols=[WS_ALPN],
                ssl=ssl_ctx,
                ping_timeout=20,
                max_size=(self._listen_cfg.max_frame_bytes if self._listen_cfg else MAX_FRAME_DEFAULT),
            )
            # Derive per-connection crypto (client role)
            if psk:
                tx_key, rx_key = _derive_session_keys(psk, role_is_client=True, ctx=ctx_bytes)
                crypto = _WsCrypto(tx=_AeadBox(tx_key, AAD_TX), rx=_AeadBox(rx_key, AAD_RX))
            else:
                crypto = _WsCrypto(tx=None, rx=None)

            try:
                peer = ws.remote_address
                local = ws.local_address
            except Exception:
                peer = (host, port)
                local = ("0.0.0.0", 0)

            info = ConnInfo(
                local_addr=f"{local[0]}:{local[1]}",
                remote_addr=f"{peer[0]}:{peer[1]}",
                is_outbound=True,
            )
            return WsConn(ws, info=info, crypto=crypto, max_frame=(self._listen_cfg.max_frame_bytes if self._listen_cfg else MAX_FRAME_DEFAULT))

        try:
            if timeout and timeout > 0:
                return await asyncio.wait_for(_do(), timeout=timeout)
            return await _do()
        except asyncio.TimeoutError as e:
            raise TransportError(f"ws dial timeout to {addr}") from e
        except Exception as e:
            raise TransportError(f"ws dial failed to {addr}: {e}") from e

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
        for sock in getattr(self._server, "sockets", []) or []:
            try:
                host, port = sock.getsockname()[:2]
                addrs.append(f"ws://{host}:{port}")
            except Exception:
                continue
        return addrs
