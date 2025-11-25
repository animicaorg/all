"""
Animica P2P — Gossip subsystem
==============================

Public surface for the gossip layer. This package implements a lightweight,
GossipSub-like pub/sub with topic IDs, validator hooks, and a mesh manager.

Modules
-------
- topics.py      : canonical topics and helpers
- validator.py   : per-topic validators (fast sanity before decode)
- mesh.py        : mesh maintenance (fanout / graft / prune)
- engine.py      : high-level publish/subscribe engine

This __init__ file re-exports the primary types for convenience.
"""

from __future__ import annotations

from ..version import __version__ as _p2p_version

# Re-exports (these modules are provided by this package)
try:  # Imports are resolved at runtime once files are present.
    from .topics import Topics, topic_id, is_valid_topic
except Exception:  # pragma: no cover
    # Deferred imports allow tooling to import package before files are generated.
    Topics = None          # type: ignore
    topic_id = None        # type: ignore
    is_valid_topic = None  # type: ignore

try:
    from .validator import MessageValidator
except Exception:  # pragma: no cover
    MessageValidator = None  # type: ignore

try:
    from .mesh import GossipMesh
except Exception:  # pragma: no cover
    GossipMesh = None  # type: ignore

try:
    from .engine import GossipEngine
except Exception:  # pragma: no cover
    GossipEngine = None  # type: ignore


__version__ = _p2p_version

__all__ = [
    "__version__",
    "Topics",
    "topic_id",
    "is_valid_topic",
    "MessageValidator",
    "GossipMesh",
    "GossipEngine",
]
