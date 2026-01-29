from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlparse

from p2p.transport.multiaddr import MultiaddrParseError, parse_multiaddr


@dataclass(frozen=True)
class PeerAddr:
    raw: str
    scheme: str
    host: str
    port: int
    canonical: str


@dataclass(frozen=True)
class PeerAddrParseResult:
    addr: Optional[PeerAddr]
    reason: Optional[str] = None
    should_penalize: bool = False


_SUPPORTED_SCHEMES = {"tcp", "quic", "ws", "wss", "p2p"}


def _bracket_ipv6(host: str) -> str:
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(ip_obj, ipaddress.IPv6Address):
        return f"[{host}]"
    return host


def _build_canonical(scheme: str, host: str, port: int, query: str = "") -> str:
    host_disp = _bracket_ipv6(host)
    suffix = f"?{query}" if query else ""
    return f"{scheme}://{host_disp}:{port}{suffix}"


def normalize_peer_addr(
    raw: str,
    *,
    fallback_port: Optional[int] = None,
    allow_ws: bool = True,
    allow_quic: bool = True,
    allow_tcp: bool = True,
) -> PeerAddrParseResult:
    if raw is None:
        return PeerAddrParseResult(None, "empty", False)
    addr = raw.strip()
    if not addr:
        return PeerAddrParseResult(None, "empty", False)

    # Multiaddr
    if addr.startswith("/"):
        try:
            ma = parse_multiaddr(addr)
        except MultiaddrParseError as exc:
            return PeerAddrParseResult(None, f"invalid_multiaddr:{exc}", False)
        except Exception as exc:  # pragma: no cover - defensive
            return PeerAddrParseResult(None, f"invalid_multiaddr:{exc}", False)

        if ma.ws_mode:
            if not allow_ws:
                return PeerAddrParseResult(None, "unsupported_ws", False)
            scheme = ma.ws_mode
        elif ma.is_quic:
            if not allow_quic:
                return PeerAddrParseResult(None, "unsupported_quic", False)
            scheme = "quic"
        else:
            if ma.transport != "tcp":
                return PeerAddrParseResult(
                    None, f"unsupported_transport:{ma.transport}", False
                )
            if not allow_tcp:
                return PeerAddrParseResult(None, "unsupported_tcp", False)
            scheme = "tcp"

        host = ma.host
        port = int(ma.port) if ma.port else None
        if port is None:
            port = int(fallback_port) if fallback_port else None
        if port is None:
            return PeerAddrParseResult(None, "missing_port", False)

        query = urlencode(ma.query) if ma.ws_mode and ma.query else ""
        canonical = _build_canonical(scheme, host, port, query=query)
        return PeerAddrParseResult(
            PeerAddr(raw=addr, scheme=scheme, host=host, port=port, canonical=canonical)
        )

    # URL form
    if "://" in addr:
        parsed = urlparse(addr)
        scheme = (parsed.scheme or "").lower()
        if scheme not in _SUPPORTED_SCHEMES:
            return PeerAddrParseResult(None, f"unsupported_scheme:{scheme}", False)
        if scheme == "p2p":
            scheme = "tcp"
        if scheme in ("ws", "wss") and not allow_ws:
            return PeerAddrParseResult(None, "unsupported_ws", False)
        if scheme == "quic" and not allow_quic:
            return PeerAddrParseResult(None, "unsupported_quic", False)
        if scheme == "tcp" and not allow_tcp:
            return PeerAddrParseResult(None, "unsupported_tcp", False)

        host = parsed.hostname
        if not host:
            return PeerAddrParseResult(None, "missing_host", False)
        port = parsed.port or (int(fallback_port) if fallback_port else None)
        if port is None:
            return PeerAddrParseResult(None, "missing_port", False)
        canonical = _build_canonical(scheme, host, int(port))
        return PeerAddrParseResult(
            PeerAddr(
                raw=addr, scheme=scheme, host=host, port=int(port), canonical=canonical
            )
        )

    # host:port (or [v6]:port)
    host = addr
    port: Optional[int] = None
    if addr.startswith("[") and "]" in addr:
        host_part, _, port_part = addr.partition("]")
        host = host_part[1:]
        if port_part.startswith(":"):
            port_part = port_part[1:]
        port = int(port_part) if port_part else None
    elif ":" in addr:
        host_part, port_part = addr.rsplit(":", 1)
        host = host_part
        port = int(port_part) if port_part.isdigit() else None

    if port is None:
        port = int(fallback_port) if fallback_port else None
    if port is None:
        return PeerAddrParseResult(None, "missing_port", False)
    if not allow_tcp:
        return PeerAddrParseResult(None, "unsupported_tcp", False)

    canonical = _build_canonical("tcp", host, int(port))
    return PeerAddrParseResult(
        PeerAddr(raw=addr, scheme="tcp", host=host, port=int(port), canonical=canonical)
    )
