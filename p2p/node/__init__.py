from __future__ import annotations

"""
Animica P2P — node orchestration package.

This subpackage groups the high-level glue that binds together:
  • transports (TCP/QUIC/WebSocket)
  • crypto (handshake, AEAD, identities)
  • wire framing/encoding
  • peer lifecycle (peerstore, connection manager, ratelimiting)
  • discovery (seeds, mDNS, Kademlia)
  • gossip (topics, mesh, validators)
  • sync (headers, blocks, txs, shares)
into a runnable peer-to-peer node.

Modules expected to live alongside this __init__:
  - node.py / service.py (Node bootstrap, event loop wiring)
  - runtime.py (task group & graceful shutdown helpers)
  - config.py (node-level config wrapper around p2p.config)
Nothing here imports those directly to avoid import cycles.
"""

from ..version import __version__ as __version__

__all__ = ["__version__"]
