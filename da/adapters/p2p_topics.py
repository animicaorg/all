from __future__ import annotations

"""
Animica • DA • P2P Topics
=========================

Canonical gossip topic strings for Data Availability (DA) messages.

We keep topics human-readable, versioned, and chain-scoped:

    animica/da/<version>/chain/<chainId>/<kind>[/ns/<namespace>]

Where:
- <version>  : protocol topic version (e.g. "v1")
- <chainId>  : CAIP-2 chain id number for Animica networks (1=mainnet, 2=testnet, 1337=devnet)
- <kind>     : one of {"commitment", "shares", "samples"}
- /ns/<id>   : optional namespace qualifier for narrower subscriptions (uint32)

Kinds
-----
- "commitment": announce new blob commitments (per-blob NMT roots) becoming available.
- "shares"    : advertise share/range availability (rare; mostly internal use).
- "samples"   : publish DAS sample responses (indices + proof branches) to subscribers.

These names intentionally align with DA adapters and the DA wire protocol.
Changing them is a consensus-adjacent change for network tooling; bump VERSION.

Examples
--------
- commitment topic (devnet):  animica/da/v1/chain/1337/commitment
- samples for ns=24 mainnet:  animica/da/v1/chain/1/samples/ns/24
"""

from dataclasses import dataclass
from typing import Literal, Optional
import re

# -----------------------------------------------------------------------------
# Canonical constants
# -----------------------------------------------------------------------------

PREFIX = "animica/da"
VERSION = "v1"  # bump if you change shapes/namespacing rules

Kind = Literal["commitment", "shares", "samples"]

_NS_MIN = 0
_NS_MAX = (1 << 32) - 1

_TOPIC_RE = re.compile(
    r"^animica/da/(?P<version>v[0-9]+)/chain/(?P<chain>[0-9]+)/(?P<kind>commitment|shares|samples)"
    r"(?:/ns/(?P<ns>[0-9]+))?$"
)

# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicParts:
    version: str
    chain_id: int
    kind: Kind
    namespace: Optional[int] = None


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------

def _validate_chain_id(chain_id: int) -> int:
    if not isinstance(chain_id, int) or chain_id < 0:
        raise ValueError("chain_id must be a non-negative integer")
    return chain_id


def _validate_namespace(ns: Optional[int]) -> Optional[int]:
    if ns is None:
        return None
    if not isinstance(ns, int):
        raise ValueError("namespace must be an int")
    if ns < _NS_MIN or ns > _NS_MAX:
        raise ValueError(f"namespace out of range [{_NS_MIN}, {_NS_MAX}]")
    return ns


def build_topic(kind: Kind, *, chain_id: int, namespace: Optional[int] = None, version: str = VERSION) -> str:
    """
    Build a canonical DA gossip topic string.

    Args:
        kind:       "commitment", "shares", or "samples"
        chain_id:   CAIP-2 numeric chain id for Animica networks
        namespace:  optional uint32 namespace qualifier
        version:    topic version (default: VERSION)

    Returns:
        Topic string like: "animica/da/v1/chain/1/commitment" or ".../samples/ns/24"
    """
    _validate_chain_id(chain_id)
    _validate_namespace(namespace)
    if version[:1] != "v" or not version[1:].isdigit():
        raise ValueError("version must look like 'v1', 'v2', ...")
    base = f"{PREFIX}/{version}/chain/{chain_id}/{kind}"
    return f"{base}/ns/{namespace}" if namespace is not None else base


def commitment_topic(*, chain_id: int, version: str = VERSION) -> str:
    """Topic for announcing new blob commitments."""
    return build_topic("commitment", chain_id=chain_id, version=version)


def shares_topic(*, chain_id: int, namespace: Optional[int] = None, version: str = VERSION) -> str:
    """Topic for share/range availability announcements (optional/advanced)."""
    return build_topic("shares", chain_id=chain_id, namespace=namespace, version=version)


def samples_topic(*, chain_id: int, namespace: Optional[int] = None, version: str = VERSION) -> str:
    """Topic for DAS sample responses (indices + proof branches)."""
    return build_topic("samples", chain_id=chain_id, namespace=namespace, version=version)


# -----------------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------------

def parse_topic(topic: str) -> TopicParts:
    """
    Parse a topic string back into structured components.

    Raises ValueError for malformed topics.
    """
    m = _TOPIC_RE.match(topic)
    if not m:
        raise ValueError(f"invalid DA topic: {topic!r}")
    version = m.group("version")
    chain_id = int(m.group("chain"))
    kind = m.group("kind")  # type: ignore[assignment]
    ns_str = m.group("ns")
    ns = int(ns_str) if ns_str is not None else None
    _validate_chain_id(chain_id)
    _validate_namespace(ns)
    return TopicParts(version=version, chain_id=chain_id, kind=kind, namespace=ns)


__all__ = [
    "PREFIX",
    "VERSION",
    "Kind",
    "TopicParts",
    "build_topic",
    "commitment_topic",
    "shares_topic",
    "samples_topic",
    "parse_topic",
]
