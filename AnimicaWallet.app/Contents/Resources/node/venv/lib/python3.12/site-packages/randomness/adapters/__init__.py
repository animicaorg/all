"""
Randomness adapters package.

This package hosts integration shims that connect the beacon / commit–reveal /
VDF components to the rest of the node (RPC, P2P, execution, storage).

Typical modules that may live here:
  - rpc_mount:   Mount read-only randomness endpoints into the main FastAPI app.
  - p2p_topics:  Canonical gossip topics and message keys for commits/reveals.
  - p2p_gossip:  Publish/subscribe wiring for commit/reveal propagation.
  - state_db:    Bridges to the node's persistent KV/SQL stores.
  - params:      Load/resolve chain parameters relevant to randomness.
  - execution:   Hooks for exposing beacon outputs to the execution layer.

Keeping this package separate allows the core randomness logic to remain
storage- and transport-agnostic while still providing clean integration points.

Nothing is imported here to avoid pulling optional dependencies unless a
specific adapter is used.
"""

__all__: list[str] = []
