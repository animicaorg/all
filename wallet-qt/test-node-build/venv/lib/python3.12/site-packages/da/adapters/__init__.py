from __future__ import annotations

"""
Animica • DA • Adapters

Integration adapters that connect the Data Availability (DA) subsystem with the
rest of the node:

- core_chain:    compute/validate DA (NMT) root when building/sealing blocks
- rpc_mount:     mount DA REST/JSON endpoints into the main FastAPI app
- p2p_topics:    canonical P2P gossip topics for commitments/shares
- p2p_gossip:    publish/subscribe DA messages on the P2P stack

This package's __init__ is intentionally side-effect free (no heavy imports) so
that importing `da.adapters` never pulls in optional frameworks unless needed.
"""

from typing import TYPE_CHECKING

try:
    # Re-export package version if available.
    from da.version import __version__  # noqa: F401
except Exception:  # pragma: no cover
    __version__ = "0.0.0"  # fallback for early bootstrapping

# Export the module names for convenience without importing them (keeps init light).
__all__ = [
    "core_chain",
    "rpc_mount",
    "p2p_topics",
    "p2p_gossip",
]

# Optional type-only imports for IDEs/type checkers (won't execute at runtime).
if TYPE_CHECKING:  # pragma: no cover
    from . import core_chain as core_chain  # noqa: F401
    from . import p2p_gossip as p2p_gossip  # noqa: F401
    from . import p2p_topics as p2p_topics  # noqa: F401
    from . import rpc_mount as rpc_mount  # noqa: F401
