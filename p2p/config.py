"""
p2p.config
==========

Configuration loader for the Animica P2P stack. Reads environment variables,
applies sane defaults, and exposes a single `load_config()` entrypoint that
returns a validated `P2PConfig` dataclass.

Env prefix: ANIMICA_P2P_

Key env vars (examples):
- ANIMICA_P2P_ENABLE_TCP=true
- ANIMICA_P2P_ENABLE_QUIC=true
- ANIMICA_P2P_ENABLE_WS=true
- ANIMICA_P2P_LISTEN_TCP=0.0.0.0:30333
- ANIMICA_P2P_LISTEN_QUIC=0.0.0.0:30334
- ANIMICA_P2P_LISTEN_WS=0.0.0.0:30335
- ANIMICA_P2P_ADVERTISED_ADDRS=/ip4/203.0.113.5/tcp/30333,/dns4/node.example.com/quic/30334
- ANIMICA_P2P_SEEDS=/dnsaddr/bootstrap.animica.dev,/dns4/seed1.animica.dev/tcp/30333
- ANIMICA_P2P_MAX_PEERS=64
- ANIMICA_P2P_MAX_OUTBOUND=16
- ANIMICA_P2P_WS_CORS= https://studio.animica.dev, https://localhost:5173
- ANIMICA_P2P_WS_ALLOW_CREDENTIALS=false
- ANIMICA_P2P_WS_COMPRESSION=true
- ANIMICA_P2P_NAT_UPNP=true
- ANIMICA_P2P_NAT_PMP=false
- ANIMICA_P2P_STUN= stun.l.google.com:19302, stun1.l.google.com:19302
- ANIMICA_P2P_EXTERNAL_IP=198.51.100.7
- ANIMICA_P2P_PRIVATE_NETWORK=false
- ANIMICA_P2P_NODE_KEY_PATH=~/.animica/node_key.json
- ANIMICA_P2P_NODE_CERT_PATH=~/.animica/node_cert.pem  (for optional QUIC ALPN cert)
"""
from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass, field, asdict
from typing import Final, Iterable, Optional, Tuple, List

from .constants import (
    DEFAULT_TCP_PORT,
    DEFAULT_QUIC_PORT,
    DEFAULT_WS_PORT,
    MAX_PEERS as CONST_MAX_PEERS,
    MAX_OUTBOUND_PEERS as CONST_MAX_OUTBOUND,
    MAX_INBOUND_PEERS as CONST_MAX_INBOUND,
    PROTOCOL_ID,
)


# ---------- parsing helpers ----------------------------------------------------

def _getenv(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip()


def _getenv_bool(name: str, default: bool) -> bool:
    v = _getenv(name)
    if v is None:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


def _getenv_int(name: str, default: int) -> int:
    v = _getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    # split on commas, also accept whitespace around
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def _expanduser(path: str | None) -> str | None:
    return os.path.expanduser(path) if path else None


def _parse_host_port(v: str | None, default_port: int) -> tuple[str, int]:
    """
    Parse a "host:port" string. If only host is present, default_port is used.
    """
    host = "0.0.0.0"
    port = default_port
    if v:
        if ":" in v:
            h, p = v.rsplit(":", 1)
            host = h or host
            try:
                port = int(p)
            except Exception:
                port = default_port
        else:
            host = v
    return host, port


def _looks_like_multiaddr(s: str) -> bool:
    # A permissive check to avoid pulling the full parser here; the real parser
    # lives in p2p/transport/multiaddr.py. This only filters obvious junk.
    return s.startswith("/ip4/") or s.startswith("/ip6/") or s.startswith("/dns") or s.startswith("/dnsaddr/")


def _validate_advertised_addrs(addrs: Iterable[str]) -> list[str]:
    out: list[str] = []
    for a in addrs:
        a = a.strip()
        if not a:
            continue
        if _looks_like_multiaddr(a):
            out.append(a)
        else:
            # Allow raw host:port for convenience; convert to a canonical multiaddr-like string
            host, port = _parse_host_port(a, 0)
            try:
                ipaddress.ip_address(host)
                out.append(f"/ip4/{host}/tcp/{port}" if ":" not in host else f"/ip6/{host}/tcp/{port}")
            except ValueError:
                # assume DNS
                out.append(f"/dns4/{host}/tcp/{port}")
    return out


def _validate_ws_cors(origins: Iterable[str]) -> list[str]:
    out: list[str] = []
    for o in origins:
        o = o.strip()
        if not o:
            continue
        # allow "*" explicitly, otherwise require a scheme
        if o == "*":
            out = ["*"]
            break
        if "://" not in o:
            # be lenient, assume https scheme-less domain
            o = "https://" + o
        out.append(o)
    return out


def _normalize_stun(servers: Iterable[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for s in servers:
        s = s.strip()
        if not s:
            continue
        host, port = _parse_host_port(s, 3478)
        out.append((host, port))
    return out


# ---------- dataclass ----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class P2PConfig:
    # Transports
    enable_tcp: bool = True
    enable_quic: bool = True
    enable_ws: bool = True

    listen_tcp: Tuple[str, int] = ("0.0.0.0", DEFAULT_TCP_PORT)
    listen_quic: Tuple[str, int] = ("0.0.0.0", DEFAULT_QUIC_PORT)
    listen_ws: Tuple[str, int] = ("0.0.0.0", DEFAULT_WS_PORT)

    # Optional advertised multiaddrs for NATed setups (used in HELLO & peerstore)
    advertised_addrs: Tuple[str, ...] = field(default_factory=tuple)

    # Bootstrapping seeds (DNS or multiaddr-like entries)
    seeds: Tuple[str, ...] = field(default_factory=tuple)

    # Peer limits
    max_peers: int = CONST_MAX_PEERS
    max_outbound: int = CONST_MAX_OUTBOUND
    max_inbound: int = CONST_MAX_INBOUND

    # NAT & external reachability
    nat_upnp: bool = False
    nat_pmp: bool = False
    nat_hairpinning_fix: bool = True
    stun_servers: Tuple[Tuple[str, int], ...] = field(default_factory=tuple)
    external_ip: Optional[str] = None
    private_network: bool = False  # if true, skip public bootstraps & harden gossip

    # Identity / TLS (optional self-signed for QUIC ALPN)
    node_key_path: Optional[str] = None  # Dilithium3/SPHINCS+ identity, managed elsewhere
    node_cert_path: Optional[str] = None  # used by p2p.crypto.cert for QUIC

    # WebSocket CORS
    ws_cors_allowed_origins: Tuple[str, ...] = field(default_factory=lambda: ("https://studio.animica.dev",))
    ws_allow_credentials: bool = False
    ws_compression: bool = True  # permessage-deflate hint for servers/clients

    # Protocol id (ALPN / subprotocol)
    protocol_id: str = PROTOCOL_ID

    def to_dict(self) -> dict:
        d = asdict(self)
        # dataclasses with tuples serialize as lists fine; return d directly
        return d


# ---------- loader -------------------------------------------------------------

def load_config() -> P2PConfig:
    """Load configuration from environment variables and return a validated P2PConfig."""
    enable_tcp = _getenv_bool("ANIMICA_P2P_ENABLE_TCP", True)
    enable_quic = _getenv_bool("ANIMICA_P2P_ENABLE_QUIC", True)
    enable_ws = _getenv_bool("ANIMICA_P2P_ENABLE_WS", True)

    listen_tcp = _parse_host_port(_getenv("ANIMICA_P2P_LISTEN_TCP"), DEFAULT_TCP_PORT)
    listen_quic = _parse_host_port(_getenv("ANIMICA_P2P_LISTEN_QUIC"), DEFAULT_QUIC_PORT)
    listen_ws = _parse_host_port(_getenv("ANIMICA_P2P_LISTEN_WS"), DEFAULT_WS_PORT)

    advertised_addrs = tuple(_validate_advertised_addrs(_csv(_getenv("ANIMICA_P2P_ADVERTISED_ADDRS"))))
    seeds = tuple(_validate_advertised_addrs(_csv(_getenv("ANIMICA_P2P_SEEDS"))))

    max_peers = _getenv_int("ANIMICA_P2P_MAX_PEERS", CONST_MAX_PEERS)
    max_outbound = _getenv_int("ANIMICA_P2P_MAX_OUTBOUND", CONST_MAX_OUTBOUND)
    max_inbound = _getenv_int("ANIMICA_P2P_MAX_INBOUND", max_peers - max_outbound)

    nat_upnp = _getenv_bool("ANIMICA_P2P_NAT_UPNP", False)
    nat_pmp = _getenv_bool("ANIMICA_P2P_NAT_PMP", False)
    nat_hairpinning_fix = _getenv_bool("ANIMICA_P2P_NAT_HAIRPIN_FIX", True)

    stun_servers = tuple(_normalize_stun(_csv(_getenv("ANIMICA_P2P_STUN"))))
    external_ip = _getenv("ANIMICA_P2P_EXTERNAL_IP")
    private_network = _getenv_bool("ANIMICA_P2P_PRIVATE_NETWORK", False)

    node_key_path = _expanduser(_getenv("ANIMICA_P2P_NODE_KEY_PATH"))
    node_cert_path = _expanduser(_getenv("ANIMICA_P2P_NODE_CERT_PATH"))

    ws_cors = tuple(_validate_ws_cors(_csv(_getenv("ANIMICA_P2P_WS_CORS")))) or (
        "https://studio.animica.dev",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    ws_allow_credentials = _getenv_bool("ANIMICA_P2P_WS_ALLOW_CREDENTIALS", False)
    ws_compression = _getenv_bool("ANIMICA_P2P_WS_COMPRESSION", True)

    # Basic sanity: enforce bounds & non-negative
    if max_outbound < 0:
        max_outbound = 0
    if max_inbound < 0:
        max_inbound = 0
    if max_outbound + max_inbound > max_peers:
        # clamp inbound to fit
        max_inbound = max(0, max_peers - max_outbound)

    return P2PConfig(
        enable_tcp=enable_tcp,
        enable_quic=enable_quic,
        enable_ws=enable_ws,
        listen_tcp=listen_tcp,
        listen_quic=listen_quic,
        listen_ws=listen_ws,
        advertised_addrs=advertised_addrs,
        seeds=seeds,
        max_peers=max_peers,
        max_outbound=max_outbound,
        max_inbound=max_inbound,
        nat_upnp=nat_upnp,
        nat_pmp=nat_pmp,
        nat_hairpinning_fix=nat_hairpinning_fix,
        stun_servers=stun_servers,
        external_ip=external_ip,
        private_network=private_network,
        node_key_path=node_key_path,
        node_cert_path=node_cert_path,
        ws_cors_allowed_origins=ws_cors,
        ws_allow_credentials=ws_allow_credentials,
        ws_compression=ws_compression,
    )


# ---------- quick dump for debugging ------------------------------------------

if __name__ == "__main__":
    import json
    cfg = load_config()
    print(json.dumps(cfg.to_dict(), indent=2))
