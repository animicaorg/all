"""
Protocol-wide constants for the Animica P2P stack.

These values are intentionally centralized so transport, gossip, sync, and
adapters can share coherent defaults. Most values may be overridden via
environment variables (e.g., ANIMICA_P2P_MAX_PEERS=128).
"""
from __future__ import annotations

import os
from typing import Final, Iterable


__all__ = [
    # Identity / protocol versioning
    "WIRE_VERSION",
    "PROTOCOL_ID",
    "HANDSHAKE_ALPN",
    "SCHEMA_VERSION",
    # Crypto preferences
    "KEM_PREFERENCE",
    "HKDF_HASH",
    "AEAD_ORDER",
    # Default listen ports
    "DEFAULT_TCP_PORT",
    "DEFAULT_QUIC_PORT",
    "DEFAULT_WS_PORT",
    # Frame & message sizing
    "MAX_FRAME_BYTES",
    "MAX_MESSAGE_BYTES",
    "MAX_TX_BYTES",
    "MAX_BLOCK_BYTES",
    "MAX_SHARE_BYTES",
    # Peer & gossip limits
    "MAX_PEERS",
    "MAX_INBOUND_PEERS",
    "MAX_OUTBOUND_PEERS",
    "GOSSIP_D",
    "GOSSIP_D_LOW",
    "GOSSIP_D_HIGH",
    "GOSSIP_FANOUT",
    "GOSSIP_MAX_INFLIGHT",
    # Timeouts & intervals (seconds)
    "DIAL_TIMEOUT",
    "HANDSHAKE_TIMEOUT",
    "REQUEST_TIMEOUT",
    "READ_IDLE_TIMEOUT",
    "WRITE_IDLE_TIMEOUT",
    "PING_INTERVAL",
    "PING_TIMEOUT",
    "GOSSIP_HEARTBEAT",
    "RECONNECT_BACKOFF_MIN",
    "RECONNECT_BACKOFF_MAX",
    # Topics
    "TOPIC_BLOCKS",
    "TOPIC_HEADERS",
    "TOPIC_TXS",
    "TOPIC_SHARES",
    "TOPIC_BLOBS",
    "ALL_TOPICS",
]


# ---- helpers -----------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _env_choice(name: str, default: str, choices: Iterable[str]) -> str:
    v = (os.getenv(name) or "").strip().lower()
    return v if v in {c.lower() for c in choices} else default


# ---- identity & versioning ----------------------------------------------------

# Wire protocol major version. Backward-incompatible changes MUST bump this.
WIRE_VERSION: Final[int] = 1

# Application-layer protocol ID used by QUIC ALPN and WS subprotocol negotiation.
PROTOCOL_ID: Final[str] = f"animica/{WIRE_VERSION}"

# Alias for ALPN string during PQ handshake (QUIC) negotiation.
HANDSHAKE_ALPN: Final[str] = PROTOCOL_ID

# Schema version for P2P wire messages (msg ids, envelopes).
SCHEMA_VERSION: Final[int] = 1


# ---- crypto preferences -------------------------------------------------------

# Post-quantum KEM preference for the transport handshake.
KEM_PREFERENCE: Final[str] = _env_choice(
    "ANIMICA_P2P_KEM", "kyber768", choices=["kyber768", "kyber1024"]
)

# HKDF hash for key schedule (kept in sync with pq/utils/hkdf.py).
HKDF_HASH: Final[str] = _env_choice(
    "ANIMICA_P2P_HKDF_HASH", "sha3-256", choices=["sha3-256"]
)

# Preferred AEAD suites in order. Both peers must share at least one.
AEAD_ORDER: Final[tuple[str, ...]] = tuple(
    (os.getenv("ANIMICA_P2P_AEAD_ORDER") or "chacha20-poly1305,aes-256-gcm")
    .replace(" ", "")
    .lower()
    .split(",")
)


# ---- default listen ports -----------------------------------------------------

DEFAULT_TCP_PORT: Final[int] = _env_int("ANIMICA_P2P_TCP_PORT", 30333)
DEFAULT_QUIC_PORT: Final[int] = _env_int("ANIMICA_P2P_QUIC_PORT", 30334)
DEFAULT_WS_PORT: Final[int] = _env_int("ANIMICA_P2P_WS_PORT", 30335)


# ---- sizing limits (bytes) ----------------------------------------------------

# Max size of a single encrypted frame on the wire (payload + envelope).
# Keep well below common MTUs for QUIC datagrams; transport may fragment.
MAX_FRAME_BYTES: Final[int] = _env_int("ANIMICA_P2P_MAX_FRAME_BYTES", 1 << 20)  # 1 MiB

# Logical message size (after deframe/AEAD). Some messages (blocks) stream in parts.
MAX_MESSAGE_BYTES: Final[int] = _env_int("ANIMICA_P2P_MAX_MESSAGE_BYTES", 2 << 20)  # 2 MiB

# Soft ceilings for common payload classes used by validators:
MAX_TX_BYTES: Final[int] = _env_int("ANIMICA_P2P_MAX_TX_BYTES", 512 * 1024)     # 512 KiB
MAX_BLOCK_BYTES: Final[int] = _env_int("ANIMICA_P2P_MAX_BLOCK_BYTES", 8 << 20)  # 8 MiB
MAX_SHARE_BYTES: Final[int] = _env_int("ANIMICA_P2P_MAX_SHARE_BYTES", 256 * 1024)  # 256 KiB


# ---- peer & gossip limits -----------------------------------------------------

# Connection counts
MAX_PEERS: Final[int] = _env_int("ANIMICA_P2P_MAX_PEERS", 64)
MAX_OUTBOUND_PEERS: Final[int] = _env_int("ANIMICA_P2P_MAX_OUTBOUND", 16)
MAX_INBOUND_PEERS: Final[int] = _env_int("ANIMICA_P2P_MAX_INBOUND", MAX_PEERS - MAX_OUTBOUND_PEERS)

# Gossip mesh parameters (GossipSub-like)
GOSSIP_D: Final[int] = _env_int("ANIMICA_P2P_GOSSIP_D", 8)
GOSSIP_D_LOW: Final[int] = _env_int("ANIMICA_P2P_GOSSIP_D_LOW", 6)
GOSSIP_D_HIGH: Final[int] = _env_int("ANIMICA_P2P_GOSSIP_D_HIGH", 12)
GOSSIP_FANOUT: Final[int] = _env_int("ANIMICA_P2P_GOSSIP_FANOUT", 6)

# Max in-flight messages per peer/topic (flow control)
GOSSIP_MAX_INFLIGHT: Final[int] = _env_int("ANIMICA_P2P_GOSSIP_MAX_INFLIGHT", 32)


# ---- timeouts & intervals (seconds) ------------------------------------------

DIAL_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_DIAL_TIMEOUT", 5.0)
HANDSHAKE_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_HANDSHAKE_TIMEOUT", 8.0)
REQUEST_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_REQUEST_TIMEOUT", 10.0)

READ_IDLE_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_READ_IDLE_TIMEOUT", 60.0)
WRITE_IDLE_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_WRITE_IDLE_TIMEOUT", 60.0)

PING_INTERVAL: Final[float] = _env_float("ANIMICA_P2P_PING_INTERVAL", 15.0)
PING_TIMEOUT: Final[float] = _env_float("ANIMICA_P2P_PING_TIMEOUT", 10.0)

# Gossip heartbeat (mesh maintenance, IWANT/IHAVE)
GOSSIP_HEARTBEAT: Final[float] = _env_float("ANIMICA_P2P_GOSSIP_HEARTBEAT", 1.0)

# Reconnect/backoff bounds for failed dials
RECONNECT_BACKOFF_MIN: Final[float] = _env_float("ANIMICA_P2P_BACKOFF_MIN", 0.5)
RECONNECT_BACKOFF_MAX: Final[float] = _env_float("ANIMICA_P2P_BACKOFF_MAX", 60.0)


# ---- topics -------------------------------------------------------------------

TOPIC_BLOCKS: Final[str] = "animica/blocks/v1"
TOPIC_HEADERS: Final[str] = "animica/headers/v1"
TOPIC_TXS: Final[str] = "animica/txs/v1"
TOPIC_SHARES: Final[str] = "animica/shares/v1"   # useful-work shares (Hash/AI/Quantum/Storage/VDF)
TOPIC_BLOBS: Final[str] = "animica/blobs/v1"     # DA commitments & requests

ALL_TOPICS: Final[tuple[str, ...]] = (
    TOPIC_BLOCKS,
    TOPIC_HEADERS,
    TOPIC_TXS,
    TOPIC_SHARES,
    TOPIC_BLOBS,
)
