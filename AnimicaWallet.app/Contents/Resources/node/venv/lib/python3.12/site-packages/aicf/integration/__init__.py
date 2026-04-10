"""
AICF Integration package.

This namespace is reserved for integration adapters that connect the AICF
scheduler/registry/economics to other Animica subsystems and external services
(e.g., consensus, capabilities host, RPC surfaces, and observability stacks).

Modules that typically live here:
  - core_chain: hooks to emit proof-claim intents and consume settlements
  - capabilities_bridge: wiring to capabilities/jobs for enqueue/result flow
  - rpc_mount: read-only endpoints for operator tooling
  - p2p_topics: optional gossip helpers (if AICF signals ride over P2P)

This file intentionally exports nothing today; it serves as a package marker.
"""

__all__: list[str] = []
