"""
P2P2: Complete rewrite of Animica P2P networking stack.

This module provides a production-ready P2P implementation with:
- Reliable sync from genesis to tip
- Orphan pool with parent backfill
- Bitcoin-style inv/getdata gossip
- Peer scoring and banning
- Clean separation of concerns

Architecture:
- transport/: TCP connections, framing, reconnect
- protocol/: Handshake, message types, validation
- peer/: Peer state machine, scoring
- gossip/: Inv/getdata protocol
- sync/: Headers-first then blocks sync
- api/: RPC introspection
"""

__version__ = "2.0.0"
