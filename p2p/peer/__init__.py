"""
p2p.peer
========
Types and services for peer lifecycle management:

- Peer / PeerRole: runtime state for a connected peer (ids, caps, scores)
- PeerStore: persistence of discovered/seen peers and scores
- AddressBook: validated multiaddrs per peer, last-seen timestamps
- ConnectionManager: dialing/backoff/limits/keepalive
- IdentifyService: IDENTIFY exchange (versions, caps, head height)
- PingService: RTT measurement with moving window
- RateLimiter: token-bucket rate controls per peer/topic/global

These are imported here for convenient access as p2p.peer.*.
"""

from .peer import Peer, PeerRole
from .peerstore import PeerStore
from .address_book import AddressBook
from .connection_manager import ConnectionManager
from .identify import IdentifyService
from .ping import PingService
from .ratelimit import RateLimiter, RateBucket, PeerRateLimiter

__all__ = [
    "Peer",
    "PeerRole",
    "PeerStore",
    "AddressBook",
    "ConnectionManager",
    "IdentifyService",
    "PingService",
    "RateLimiter",
    "RateBucket",
    "PeerRateLimiter",
]
