"""
Animica P2P protocol facade.

This module defines:
  • Wire/protocol version constants and ALPN string
  • A tiny, transport-agnostic handler registry keyed by message-id
  • Helpers to build/validate the HELLO capabilities structure used during handshake

It intentionally avoids importing heavy transport or peer objects to prevent cycles.
Higher layers (peer/session managers) should:
  1) call build_hello_caps() to construct the local capability blob,
  2) validate a remote blob with validate_hello_caps(remote),
  3) route incoming frames by msg_id via resolve_handler(msg_id).
"""

from __future__ import annotations

import os
import platform
from typing import Any, Awaitable, Callable, Dict, Optional, TypedDict

# Keep strictly self-contained to avoid init-order import cycles.
# If you change these, also update p2p/wire/message_ids.py and IDENTIFY/HELLO logic.
PROTOCOL_FAMILY = "animica"
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
WIRE_SCHEMA_VERSION = 1  # bump when wire payload schemas change incompatibly
ALPN = f"{PROTOCOL_FAMILY}/{PROTOCOL_MAJOR}"

# -----------------------------
# Handler registry (msg_id → fn)
# -----------------------------

Handler = Callable[[str, bytes], Awaitable[None]]
_registry: Dict[int, Handler] = {}


def register_handler(msg_id: int, handler: Handler) -> None:
    """
    Register an async handler for a message id.
    Handler signature: (peer_id: str, payload_bytes: bytes) -> Awaitable[None]
    """
    if not isinstance(msg_id, int) or msg_id < 0:
        raise ValueError("msg_id must be a non-negative int")
    _registry[msg_id] = handler


def unregister_handler(msg_id: int) -> None:
    _registry.pop(msg_id, None)


def resolve_handler(msg_id: int) -> Optional[Handler]:
    return _registry.get(msg_id)


def clear_handlers() -> None:
    _registry.clear()


# -----------------------------
# HELLO / IDENTIFY capabilities
# -----------------------------


class HelloCaps(TypedDict, total=False):
    # Version & identity
    family: str
    alpn: str
    major: int
    minor: int
    wire: int
    node_version: str
    impl: str  # python/<version> on <platform>
    # Chain & role hints
    chain_id: int
    roles: list[str]  # e.g. ["full"], ["full","miner"], ["observer"]
    # Feature toggles (transport & protocol)
    transports: list[str]  # subset of ["tcp","quic","ws"]
    compression: list[str]  # subset of ["zstd","snappy"]
    rpc: bool  # node exposes RPC (useful for peers to discover local RPC bridge)
    # Optional product info
    agent: str  # free-form "animicad/0.1 (+https://…)"


class ProtocolError(Exception):
    pass


def _safe_import_version(
    module: str, attr: str = "__version__", fallback: str = "unknown"
) -> str:
    try:
        mod = __import__(module, fromlist=[attr])
        return str(getattr(mod, attr))
    except Exception:
        return fallback


def _node_version() -> str:
    # Prefer core/rpc/p2p versions if available
    pv = _safe_import_version("p2p.version")
    cv = _safe_import_version("core.version")
    rv = _safe_import_version("rpc.version")
    # Compose a compact string
    parts = [
        f"p2p/{pv}" if pv != "unknown" else "p2p",
        f"core/{cv}" if cv != "unknown" else "core",
    ]
    if rv != "unknown":
        parts.append(f"rpc/{rv}")
    return ";".join(parts)


def _impl_string() -> str:
    return f"python/{platform.python_version()} on {platform.system()}-{platform.machine()}"


def _roles_from_env() -> list[str]:
    # ANIMICA_NODE_ROLE can be "full", "miner", "observer", or comma-separated combos
    env = os.getenv("ANIMICA_NODE_ROLE", "full")
    roles = [r.strip() for r in env.split(",") if r.strip()]
    # Normalize & dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for r in roles:
        r = r.lower()
        if r in {"full", "miner", "observer"} and r not in seen:
            out.append(r)
            seen.add(r)
    return out or ["full"]


def build_hello_caps(
    *,
    chain_id: int,
    transports: Optional[list[str]] = None,
    compression: Optional[list[str]] = None,
    rpc_exposed: bool = False,
    agent: Optional[str] = None,
) -> HelloCaps:
    """
    Construct a capabilities structure suitable for the HELLO exchange.
    This object is JSON/CBOR friendly and intentionally conservative.
    """
    caps: HelloCaps = {
        "family": PROTOCOL_FAMILY,
        "alpn": ALPN,
        "major": PROTOCOL_MAJOR,
        "minor": PROTOCOL_MINOR,
        "wire": WIRE_SCHEMA_VERSION,
        "node_version": _node_version(),
        "impl": _impl_string(),
        "chain_id": int(chain_id),
        "roles": _roles_from_env(),
        "transports": transports or ["tcp", "ws"],  # QUIC optional
        "compression": compression or ["zstd"],  # snappy optional
        "rpc": bool(rpc_exposed),
        "agent": agent or os.getenv("ANIMICA_AGENT", "animicad/0.1"),
    }
    return caps


def validate_hello_caps(remote: Dict[str, Any]) -> None:
    """
    Validate a remote HELLO caps dict. Raises ProtocolError if incompatible.
    """
    family = remote.get("family")
    alpn = remote.get("alpn")
    major = remote.get("major")
    wire = remote.get("wire")

    if family != PROTOCOL_FAMILY:
        raise ProtocolError(
            f"incompatible family: remote={family!r} local={PROTOCOL_FAMILY!r}"
        )

    if not isinstance(alpn, str) or not alpn.startswith(f"{PROTOCOL_FAMILY}/"):
        raise ProtocolError(f"bad ALPN: {alpn!r}")

    try:
        r_major = int(alpn.split("/", 1)[1])
    except Exception as e:
        raise ProtocolError(f"unparsable ALPN {alpn!r}") from e

    if r_major != PROTOCOL_MAJOR:
        raise ProtocolError(
            f"protocol major mismatch: remote={r_major} local={PROTOCOL_MAJOR}"
        )

    if not isinstance(major, int) or major != PROTOCOL_MAJOR:
        # Redundant but helpful to detect inconsistent peers
        raise ProtocolError(
            f"HELLO.major mismatch: remote={major} local={PROTOCOL_MAJOR}"
        )

    if not isinstance(wire, int) or wire != WIRE_SCHEMA_VERSION:
        # For now require exact wire schema match; relax to range if/when we version payloads loosely.
        raise ProtocolError(
            f"wire schema mismatch: remote={wire} local={WIRE_SCHEMA_VERSION}"
        )

    # Optional sanity checks
    roles = remote.get("roles", [])
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise ProtocolError("malformed roles list")

    transports = remote.get("transports", [])
    if not isinstance(transports, list) or not all(
        isinstance(t, str) for t in transports
    ):
        raise ProtocolError("malformed transports list")


__all__ = [
    # Versions & constants
    "PROTOCOL_FAMILY",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "WIRE_SCHEMA_VERSION",
    "ALPN",
    # Registry
    "Handler",
    "register_handler",
    "unregister_handler",
    "resolve_handler",
    "clear_handlers",
    # Hello/caps
    "HelloCaps",
    "build_hello_caps",
    "validate_hello_caps",
    # Errors
    "ProtocolError",
]
