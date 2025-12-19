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

import ipaddress
import os
from dataclasses import asdict, dataclass, field
from typing import Final, Iterable, List, Optional, Tuple

from .constants import DEFAULT_QUIC_PORT, DEFAULT_TCP_PORT, DEFAULT_WS_PORT
from .constants import MAX_INBOUND_PEERS as CONST_MAX_INBOUND
from .constants import MAX_OUTBOUND_PEERS as CONST_MAX_OUTBOUND
from .constants import MAX_PEERS as CONST_MAX_PEERS
from .constants import PROTOCOL_ID

# Default fallback seeds (mainnet) - prefer the public RPC host with IP fallback.
MAINNET_SEEDS: Final[tuple[str, ...]] = (
    # Primary: resolve the mainnet seed domain first.
    "/dns4/mainnet.animica.org/udp/443/quic-v1",
    "/dns4/mainnet.animica.org/tcp/30333",
    "/dns4/mainnet.animica.org/tcp/30333/ws",
    # Public RPC host for additional reachability.
    "/dns4/rpc.animica.org/udp/443/quic-v1",
    "/dns4/rpc.animica.org/tcp/30333",
    "/dns4/rpc.animica.org/tcp/30333/ws",
    # Fallback: direct IP in case DNS is unavailable.
    "/ip4/144.126.133.21/udp/443/quic-v1",
    "/ip4/144.126.133.21/tcp/30333",
)

TESTNET_SEEDS: Final[tuple[str, ...]] = (
    # Primary: dedicated testnet seed domain.
    "/dns4/testnet.animica.org/udp/443/quic-v1",
    "/dns4/testnet.animica.org/tcp/30333",
    "/dns4/testnet.animica.org/tcp/30333/ws",
    # Public RPC host for testnet.
    "/dns4/rpc.testnet.animica.org/udp/443/quic-v1",
    "/dns4/rpc.testnet.animica.org/tcp/30333",
    # Shared IP fallback.
    "/ip4/144.126.133.21/udp/443/quic-v1",
    "/ip4/144.126.133.21/tcp/30333",
)

DEVNET_SEEDS: Final[tuple[str, ...]] = (
    # Primary devnet domain.
    "/dns4/devnet.animica.org/udp/443/quic-v1",
    "/dns4/devnet.animica.org/tcp/30333",
    # Shared IP fallback.
    "/ip4/144.126.133.21/udp/443/quic-v1",
    "/ip4/144.126.133.21/tcp/30333",
)

# Legacy default: fall back to mainnet seeds.
DEFAULT_SEEDS: Final[tuple[str, ...]] = MAINNET_SEEDS

# Network-specific seeds.
DEFAULT_SEEDS_BY_NETWORK: Final[dict[int, tuple[str, ...]]] = {
    1: MAINNET_SEEDS,
    2: TESTNET_SEEDS,
    1337: DEVNET_SEEDS,
}

# Network name to chain_id mapping (for ANIMICA_P2P_NETWORK env var)
NETWORK_NAME_TO_CHAIN_ID: Final[dict[str, int]] = {
    "mainnet": 1,
    "testnet": 2,
    "devnet": 1337,
}


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


def _parse_chain_id(value: str | None) -> int | None:
    """
    Parse chain_id values in decimal or 0x-prefixed hex form.
    Returns None for missing/invalid inputs.
    """
    if value is None:
        return None
    try:
        return int(value, 0)
    except Exception:
        return None


def _load_seeds_from_env(chain_id: int | None = None) -> tuple[str, ...]:
    """
    Load seeds from environment variables or use network-specific defaults.

    Priority:
    1. ANIMICA_P2P_SEEDS (explicit seed list)
    2. ANIMICA_P2P_NETWORK (network name: mainnet/testnet/devnet)
    3. chain_id parameter (use network-specific seeds)
    4. DEFAULT_SEEDS (mainnet fallback)
    """
    # Check for explicit seed list
    raw = os.getenv("ANIMICA_P2P_SEEDS")
    if raw is not None:
        parsed = _csv(raw)
        return tuple(_validate_advertised_addrs(parsed))

    # Check for network name env var (P2P-specific first, then global fallback)
    network_name = os.getenv("ANIMICA_P2P_NETWORK") or os.getenv("ANIMICA_NETWORK")
    if network_name and chain_id is None:
        network_name = network_name.lower().strip()
        if network_name in NETWORK_NAME_TO_CHAIN_ID:
            chain_id = NETWORK_NAME_TO_CHAIN_ID[network_name]

    # Fall back to chain_id env vars if still unset (P2P-specific first)
    if chain_id is None:
        chain_id = _parse_chain_id(os.getenv("ANIMICA_P2P_CHAIN_ID"))
    if chain_id is None:
        chain_id = _parse_chain_id(os.getenv("ANIMICA_CHAIN_ID"))

    # Use network-specific seeds if chain_id is available
    if chain_id is not None and chain_id in DEFAULT_SEEDS_BY_NETWORK:
        return DEFAULT_SEEDS_BY_NETWORK[chain_id]

    # Legacy fallback
    return DEFAULT_SEEDS


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
    return (
        s.startswith("/ip4/")
        or s.startswith("/ip6/")
        or s.startswith("/dns")
        or s.startswith("/dnsaddr/")
    )


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
                out.append(
                    f"/ip4/{host}/tcp/{port}"
                    if ":" not in host
                    else f"/ip6/{host}/tcp/{port}"
                )
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
class DiscoveryConfig:
    """Configuration for P2P discovery mechanisms."""

    enable_kademlia: bool = False
    enable_mdns: bool = False


@dataclass(frozen=True, slots=True)
class GossipConfig:
    """Configuration for gossip subsystem."""

    # Add gossip-specific configuration as needed
    pass


@dataclass(frozen=True, slots=True)
class FlowControlConfig:
    """Configuration for flow control."""

    # Add flow control configuration as needed
    pass


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
    node_key_path: Optional[str] = (
        None  # Dilithium3/SPHINCS+ identity, managed elsewhere
    )
    node_cert_path: Optional[str] = None  # used by p2p.crypto.cert for QUIC

    # WebSocket CORS
    ws_cors_allowed_origins: Tuple[str, ...] = field(
        default_factory=lambda: ("https://studio.animica.dev",)
    )
    ws_allow_credentials: bool = False
    ws_compression: bool = True  # permessage-deflate hint for servers/clients

    # Protocol id (ALPN / subprotocol)
    protocol_id: str = PROTOCOL_ID

    # Data directory for persistent storage (peer store, keys, etc.)
    # Defaults to ~/.animica/p2p/ when loaded via load_config()
    data_dir: str = field(default_factory=lambda: os.path.expanduser("~/.animica/p2p"))

    # Nested configuration objects
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    gossip: GossipConfig = field(default_factory=GossipConfig)
    flow_control: FlowControlConfig = field(default_factory=FlowControlConfig)

    # Additional fields referenced by NodeService
    alg_policy_root: Optional[bytes] = None
    keys_path: Optional[str] = None
    identity_alg: str = "dilithium3"
    handshake_hkdf_salt: bytes = field(default_factory=lambda: b"animica-p2p-v1")
    dial_timeout: float = 5.0
    quic_alpn: str = "animica/1"
    ws_cors: bool = True
    listen_multiaddrs: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert bytes to hex strings for JSON serialization
        if d.get("alg_policy_root") is not None:
            d["alg_policy_root"] = d["alg_policy_root"].hex()
        if d.get("handshake_hkdf_salt") is not None:
            d["handshake_hkdf_salt"] = d["handshake_hkdf_salt"].hex()
        return d


# ---------- loader -------------------------------------------------------------


def load_config() -> P2PConfig:
    """Load configuration from environment variables and return a validated P2PConfig."""
    enable_tcp = _getenv_bool("ANIMICA_P2P_ENABLE_TCP", True)
    enable_quic = _getenv_bool("ANIMICA_P2P_ENABLE_QUIC", True)
    enable_ws = _getenv_bool("ANIMICA_P2P_ENABLE_WS", True)

    listen_tcp = _parse_host_port(_getenv("ANIMICA_P2P_LISTEN_TCP"), DEFAULT_TCP_PORT)
    listen_quic = _parse_host_port(
        _getenv("ANIMICA_P2P_LISTEN_QUIC"), DEFAULT_QUIC_PORT
    )
    listen_ws = _parse_host_port(_getenv("ANIMICA_P2P_LISTEN_WS"), DEFAULT_WS_PORT)

    advertised_addrs = tuple(
        _validate_advertised_addrs(_csv(_getenv("ANIMICA_P2P_ADVERTISED_ADDRS")))
    )
    # Try to get chain_id for network-specific seeds (best effort)
    chain_id = _parse_chain_id(_getenv("ANIMICA_P2P_CHAIN_ID"))
    seeds = _load_seeds_from_env(chain_id)

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

    # Data directory for persistent storage (defaults to ~/.animica/p2p/)
    data_dir = _expanduser(_getenv("ANIMICA_P2P_DATA_DIR"))
    if data_dir is None:
        # Default to ~/.animica/p2p/ for peer store and other persistent data
        data_dir = os.path.expanduser("~/.animica/p2p")

    # Discovery configuration
    enable_kademlia = _getenv_bool("ANIMICA_P2P_ENABLE_KADEMLIA", False)
    enable_mdns = _getenv_bool("ANIMICA_P2P_ENABLE_MDNS", False)
    discovery_config = DiscoveryConfig(
        enable_kademlia=enable_kademlia,
        enable_mdns=enable_mdns,
    )

    # Gossip and flow control configs (use defaults for now)
    gossip_config = GossipConfig()
    flow_control_config = FlowControlConfig()

    # Additional NodeService fields
    keys_path = _expanduser(_getenv("ANIMICA_P2P_KEYS_PATH"))
    identity_alg = _getenv("ANIMICA_P2P_IDENTITY_ALG") or "dilithium3"
    dial_timeout = float(_getenv("ANIMICA_P2P_DIAL_TIMEOUT") or "5.0")

    # Build listen_multiaddrs from individual listen addresses
    listen_multiaddrs = []
    if enable_tcp:
        host, port = listen_tcp
        listen_multiaddrs.append(f"/ip4/{host}/tcp/{port}")
    if enable_quic:
        host, port = listen_quic
        listen_multiaddrs.append(f"/ip4/{host}/udp/{port}/quic-v1")
    if enable_ws:
        host, port = listen_ws
        listen_multiaddrs.append(f"/ip4/{host}/tcp/{port}/ws")

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
        data_dir=data_dir,
        discovery=discovery_config,
        gossip=gossip_config,
        flow_control=flow_control_config,
        keys_path=keys_path,
        identity_alg=identity_alg,
        dial_timeout=dial_timeout,
        listen_multiaddrs=tuple(listen_multiaddrs),
    )


# ---------- quick dump for debugging ------------------------------------------

if __name__ == "__main__":
    import json

    cfg = load_config()
    print(json.dumps(cfg.to_dict(), indent=2))
