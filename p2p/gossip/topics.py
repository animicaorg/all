from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Set

try:
    # Prefer project constants if available.
    from ..constants import PROTOCOL_NAME, PROTOCOL_VERSION
except Exception:  # pragma: no cover
    PROTOCOL_NAME = "animica"
    PROTOCOL_VERSION = "1"

_CHAIN_RE = re.compile(
    r"^(?:\d+|[a-z0-9_-]+:\d+)$"
)  # e.g., 1 or animica:1 (CAIP-2-ish)

# Canonical leaf names for topics. Keep this list stable across releases.
_CANONICAL_LEAVES: Set[str] = {
    "headers",  # header announcements / requests
    "blocks",  # compact/full block announcements
    "txs",  # transaction relay
    "shares/hash",  # useful-work hash shares
    "shares/ai",  # AI proof shares
    "shares/quantum",  # Quantum proof shares
    "blobs",  # DA blob commitments / retrieval notices
}


@dataclass(frozen=True)
class Topic:
    """A fully-qualified P2P topic with a stable numeric id."""

    path: str
    id64: int

    def __str__(self) -> str:  # pragma: no cover
        return self.path


def _version_str() -> str:
    # Accept semver-like values from constants; otherwise treat as plain string.
    return str(PROTOCOL_VERSION)


def _prefix(chain_id: str | int) -> str:
    c = str(chain_id)
    if not _CHAIN_RE.match(c):
        raise ValueError(
            f"invalid chain_id '{chain_id}' (expected integer or 'ns:integer')"
        )
    return f"{PROTOCOL_NAME}/gossip/v{_version_str()}/{c}"


def _topic_path(leaf: str, chain_id: str | int) -> str:
    if leaf not in _CANONICAL_LEAVES:
        raise ValueError(f"unknown topic leaf '{leaf}'")
    return f"{_prefix(chain_id)}/{leaf}"


def topic_id(path: str) -> int:
    """
    Stable 64-bit topic id derived from the UTF-8 path using SHA3-256.
    (Use 64-bit to keep frame overhead small; collision risk is negligible.)
    """
    h = hashlib.sha3_256(path.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def topic(leaf: str, chain_id: str | int) -> Topic:
    """Build a fully-qualified topic (path + 64-bit id) for a canonical leaf."""
    p = _topic_path(leaf, chain_id)
    return Topic(path=p, id64=topic_id(p))


# Convenience builders for the standard topics -------------------------------


def headers(chain_id: str | int) -> Topic:
    return topic("headers", chain_id)


def blocks(chain_id: str | int) -> Topic:
    return topic("blocks", chain_id)


def txs(chain_id: str | int) -> Topic:
    return topic("txs", chain_id)


def shares_hash(chain_id: str | int) -> Topic:
    return topic("shares/hash", chain_id)


def shares_ai(chain_id: str | int) -> Topic:
    return topic("shares/ai", chain_id)


def shares_quantum(chain_id: str | int) -> Topic:
    return topic("shares/quantum", chain_id)


def blobs(chain_id: str | int) -> Topic:
    return topic("blobs", chain_id)


# Validation & utilities -----------------------------------------------------


def is_valid_topic(path: str, allowed_leaves: Optional[Iterable[str]] = None) -> bool:
    """
    Check that a given path is a valid animica gossip topic and (optionally)
    that its leaf is in an allowlist.
    """
    parts = path.split("/")
    if len(parts) < 5:
        return False
    proto, group, ver, chain, *leaf_parts = parts
    if proto != PROTOCOL_NAME or group != "gossip":
        return False
    if not ver.startswith("v"):
        return False
    if not _CHAIN_RE.match(chain):
        return False
    leaf = "/".join(leaf_parts)
    if leaf not in _CANONICAL_LEAVES:
        return False
    if allowed_leaves is not None and leaf not in set(allowed_leaves):
        return False
    return True


def topic_id_from_path(path: str) -> int:
    """Compute the 64-bit topic id from a path (validity is the caller's responsibility)."""
    return topic_id(path)


# Introspection --------------------------------------------------------------


class Topics:
    """
    Convenience namespace with constructors for all canonical topics.
    Prefer these over hard-coding strings in callers.
    """

    @staticmethod
    def headers(chain_id: str | int) -> Topic:
        return headers(chain_id)

    @staticmethod
    def blocks(chain_id: str | int) -> Topic:
        return blocks(chain_id)

    @staticmethod
    def txs(chain_id: str | int) -> Topic:
        return txs(chain_id)

    @staticmethod
    def shares_hash(chain_id: str | int) -> Topic:
        return shares_hash(chain_id)

    @staticmethod
    def shares_ai(chain_id: str | int) -> Topic:
        return shares_ai(chain_id)

    @staticmethod
    def shares_quantum(chain_id: str | int) -> Topic:
        return shares_quantum(chain_id)

    @staticmethod
    def blobs(chain_id: str | int) -> Topic:
        return blobs(chain_id)

    @staticmethod
    def all_paths(chain_id: str | int) -> list[str]:
        """List all canonical topic paths for a chain."""
        return [_topic_path(leaf, chain_id) for leaf in sorted(_CANONICAL_LEAVES)]

    @staticmethod
    def all(chain_id: str | int) -> list[Topic]:
        return [topic(leaf, chain_id) for leaf in sorted(_CANONICAL_LEAVES)]


__all__ = [
    "Topic",
    "topic",
    "topic_id",
    "topic_id_from_path",
    "is_valid_topic",
    "headers",
    "blocks",
    "txs",
    "shares_hash",
    "shares_ai",
    "shares_quantum",
    "blobs",
    "Topics",
]
