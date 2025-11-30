from __future__ import annotations

"""
Multiaddr-like parsing and formatting utilities for Animica P2P.

Supported components (subset + pragmatic extensions):

  /ip4/<addr>           e.g. /ip4/127.0.0.1
  /ip6/<addr>           e.g. /ip6/::1
  /dns4/<host>          e.g. /dns4/node.example.com
  /dns6/<host>          e.g. /dns6/node.example.com
  /tcp/<port>           e.g. /tcp/9031
  /udp/<port>           e.g. /udp/9000
  /quic                 (no value)
  /ws                   (no value; optional "?k=v&..." suffix → /ws?k=v)
  /wss                  (no value; optional "?k=v&..." suffix)
  /p2p/<peer-id>        optional peer id

Examples:
  /ip4/127.0.0.1/tcp/9031/ws
  /dns4/a.example/tcp/443/wss
  /ip6/::1/udp/9000/quic
  /dns4/a.example/tcp/443/wss/p2p/12D3KooW...    (peer hint)

Extensions:
  - We accept query parameters only on the terminal {ws,wss} component, e.g.:
        /dns4/a.example/tcp/443/wss?psk=c0ffee
    These become URL query params when converting to ws/wss URL.
  - We tolerate IPv6 with or without brackets in input; output is normalized.

This is intentionally small and dependency-free. If you need full multiaddr,
you can later swap this with a proper library; the public API here is minimal.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode

__all__ = [
    "Multiaddr",
    "parse_multiaddr",
    "format_multiaddr",
    "to_url",
    "is_multiaddr",
    "normalize_multiaddr",
    "MultiaddrParseError",
]

# ------------------------------- Errors -------------------------------------


class MultiaddrParseError(ValueError):
    pass


# ------------------------------- Helpers ------------------------------------


def _is_ipv4(s: str) -> bool:
    try:
        parts = s.split(".")
        return (
            len(parts) == 4
            and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
            and not s.endswith(".")
            and not s.startswith(".")
        )
    except Exception:
        return False


def _is_ipv6(s: str) -> bool:
    # minimal acceptance; we normalize by removing brackets for storage
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return ":" in s and all(len(chunk) <= 4 for chunk in s.split(":") if chunk)


def _bracket_ipv6(host: str) -> str:
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


def _split_components(ma: str) -> List[str]:
    if not ma or not ma.startswith("/"):
        raise MultiaddrParseError("multiaddr must start with '/'")
    # Keep query on same token if present at the end (e.g., /wss?psk=abc)
    comps = [c for c in ma.split("/") if c != ""]
    if not comps:
        raise MultiaddrParseError("empty multiaddr")
    return comps


def _parse_ws_query(token: str) -> Tuple[str, Dict[str, str]]:
    """
    Accept 'ws', 'wss', 'ws?x=y&z=1', 'wss?k=v'
    Returns (proto, query)
    """
    if "?" not in token:
        return token, {}
    proto, qs = token.split("?", 1)
    q = dict(parse_qsl(qs, keep_blank_values=True))
    return proto, q


# ------------------------------- Model --------------------------------------


@dataclass(frozen=True)
class Multiaddr:
    """
    Parsed view over a multiaddr-like string.

    Derived fields:
      - host: str
      - port: Optional[int]
      - transport: 'tcp' | 'udp'
      - is_quic: bool
      - ws_mode: Optional['ws'|'wss']
      - peer_id: Optional[str]
      - query: Dict[str,str]      (only for ws/wss)
      - parts: List[Tuple[str, Optional[str]]]  (raw components)
    """

    host: str
    port: Optional[int]
    transport: str
    is_quic: bool
    ws_mode: Optional[str]
    peer_id: Optional[str]
    query: Dict[str, str]
    parts: List[Tuple[str, Optional[str]]]

    # ---- convenience --------------------------------------------------------

    def with_query(self, updates: Dict[str, str]) -> "Multiaddr":
        q = {**self.query, **updates}
        return Multiaddr(
            host=self.host,
            port=self.port,
            transport=self.transport,
            is_quic=self.is_quic,
            ws_mode=self.ws_mode,
            peer_id=self.peer_id,
            query=q,
            parts=self.parts,
        )

    def ensure_port(self, default: int) -> "Multiaddr":
        if self.port is not None:
            return self
        return Multiaddr(
            host=self.host,
            port=default,
            transport=self.transport,
            is_quic=self.is_quic,
            ws_mode=self.ws_mode,
            peer_id=self.peer_id,
            query=self.query,
            parts=self.parts,
        )


# ------------------------------- Parser -------------------------------------


def parse_multiaddr(ma: str) -> Multiaddr:
    comps = _split_components(ma)

    host: Optional[str] = None
    port: Optional[int] = None
    transport: Optional[str] = None  # 'tcp' or 'udp'
    is_quic = False
    ws_mode: Optional[str] = None  # 'ws' | 'wss'
    query: Dict[str, str] = {}
    peer_id: Optional[str] = None
    parts: List[Tuple[str, Optional[str]]] = []

    i = 0
    while i < len(comps):
        proto = comps[i]
        val: Optional[str] = None

        # valueless protos (may include inline query for ws/wss)
        if proto.startswith("ws"):
            p, q = _parse_ws_query(proto)
            if p not in ("ws", "wss"):
                raise MultiaddrParseError(f"unexpected token {proto!r}")
            ws_mode = p
            if q:
                query.update(q)
            parts.append((p, None))
            i += 1
            continue

        if proto == "wss" or proto == "ws":  # already handled above; defensive
            ws_mode = proto
            parts.append((proto, None))
            i += 1
            continue

        if proto in ("quic",):
            is_quic = True
            parts.append((proto, None))
            i += 1
            continue

        # value-bearing protos
        if i + 1 >= len(comps):
            raise MultiaddrParseError(f"missing value for component '{proto}'")
        val = comps[i + 1]

        if proto == "ip4":
            if not _is_ipv4(val):
                raise MultiaddrParseError(f"invalid ip4 address: {val!r}")
            host = val
        elif proto == "ip6":
            if not _is_ipv6(val):
                raise MultiaddrParseError(f"invalid ip6 address: {val!r}")
            # store without brackets
            host = val[1:-1] if val.startswith("[") and val.endswith("]") else val
        elif proto == "dns4" or proto == "dns6" or proto == "dns":
            host = val
        elif proto == "tcp":
            try:
                p = int(val)
            except ValueError:
                raise MultiaddrParseError(f"tcp port must be integer: {val!r}")
            if not (0 < p < 65536):
                raise MultiaddrParseError(f"tcp port out of range: {p}")
            port = p
            transport = "tcp"
        elif proto == "udp":
            try:
                p = int(val)
            except ValueError:
                raise MultiaddrParseError(f"udp port must be integer: {val!r}")
            if not (0 < p < 65536):
                raise MultiaddrParseError(f"udp port out of range: {p}")
            port = p
            transport = "udp"
        elif proto == "p2p":
            peer_id = val
        else:
            raise MultiaddrParseError(f"unsupported component: {proto}")

        parts.append((proto, val))
        i += 2

    if host is None:
        raise MultiaddrParseError("host component (ip4/ip6/dns*) is required")
    if transport is None and not is_quic and ws_mode is None:
        # Bare address not useful; demand transport unless ws/quic implies
        raise MultiaddrParseError("transport (tcp/udp) or quic/ws is required")

    # If ws/wss is present but no transport set, assume tcp with default port
    if ws_mode and transport is None and not is_quic:
        transport = "tcp"

    # Normalize: if ws_mode present and no port, leave None; callers may apply default.
    return Multiaddr(
        host=host,
        port=port,
        transport=transport or ("udp" if transport == "udp" else "tcp"),
        is_quic=is_quic,
        ws_mode=ws_mode,
        peer_id=peer_id,
        query=query,
        parts=parts,
    )


# ------------------------------- Formatting ---------------------------------


def format_multiaddr(ma: Multiaddr) -> str:
    out: List[str] = []
    # host
    if _is_ipv4(ma.host):
        out += ["ip4", ma.host]
    elif _is_ipv6(ma.host):
        out += ["ip6", ma.host]
    else:
        # heuristic; you may choose dns4/dns6 explicitly later
        out += ["dns", ma.host]

    # transport/port
    if ma.transport == "tcp" and ma.port:
        out += ["tcp", str(ma.port)]
    elif ma.transport == "udp" and ma.port:
        out += ["udp", str(ma.port)]
    elif ma.transport in ("tcp", "udp") and ma.port is None:
        # keep proto but omit port if unknown
        out += [ma.transport, "0"]

    # quic?
    if ma.is_quic:
        out += ["quic"]

    # ws/wss + query
    if ma.ws_mode:
        if ma.query:
            out.append(f"{ma.ws_mode}?{urlencode(ma.query)}")
        else:
            out.append(ma.ws_mode)

    # peer-id
    if ma.peer_id:
        out += ["p2p", ma.peer_id]

    return "/" + "/".join(out)


def to_url(ma: Multiaddr, default_path: str = "/p2p") -> str:
    """
    Convert to a usable dial URL when ws/wss is present. For non-websocket transports,
    raises unless QUIC/TCP direct URLs are later supported by callers.

    Examples:
      /ip4/127.0.0.1/tcp/9031/ws                -> ws://127.0.0.1:9031/p2p
      /dns4/x.example/tcp/443/wss?psk=abc       -> wss://x.example:443/p2p?psk=abc
      /ip6/::1/tcp/9031/ws                      -> ws://[::1]:9031/p2p
    """
    if not ma.ws_mode:
        raise MultiaddrParseError("to_url() requires ws or wss component")
    if ma.port is None:
        # Apply conventional defaults if omitted
        port = 443 if ma.ws_mode == "wss" else 80
    else:
        port = ma.port
    host = _bracket_ipv6(ma.host)
    qs = f"?{urlencode(ma.query)}" if ma.query else ""
    path = default_path if default_path.startswith("/") else f"/{default_path}"
    return f"{ma.ws_mode}://{host}:{port}{path}{qs}"


def is_multiaddr(s: str) -> bool:
    try:
        parse_multiaddr(s)
        return True
    except Exception:
        return False


def normalize_multiaddr(s: str) -> str:
    """
    Parse and re-emit a canonical string form.
    """
    ma = parse_multiaddr(s)
    return format_multiaddr(ma)


# ------------------------------- CLI (dev) ----------------------------------


if __name__ == "__main__":
    # Tiny manual test:
    samples = [
        "/ip4/127.0.0.1/tcp/9031/ws",
        "/dns4/node.example.com/tcp/443/wss?psk=c0ffee",
        "/ip6/::1/tcp/9031/ws",
        "/ip6/[::1]/udp/9000/quic",
        "/dns/a.example/tcp/443/wss/p2p/12D3KooWXYZ",
    ]
    for s in samples:
        try:
            ma = parse_multiaddr(s)
            print("IN :", s)
            print("URL:", to_url(ma) if ma.ws_mode else "(no URL)")
            print("CAN:", format_multiaddr(ma))
            print("---")
        except Exception as e:
            print("ERR:", s, "->", e)
