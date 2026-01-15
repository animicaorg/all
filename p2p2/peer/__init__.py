"""
Peer state machine and scoring for P2P2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..protocol import Message, ServiceFlags


class PeerState(str, Enum):
    """Peer connection state."""
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


@dataclass
class PeerInfo:
    """Information about a peer from handshake."""
    node_id: str
    network_id: str
    chain_id: int
    genesis_hash: str
    protocol_version: int
    services: int
    listen_addrs: list[str]
    best_height: int
    best_hash: str


@dataclass
class PeerScore:
    """Peer quality scoring."""
    score: float = 10.0  # Base score
    headers_delivered: int = 0
    blocks_delivered: int = 0
    txs_delivered: int = 0
    invalid_messages: int = 0
    timeouts: int = 0
    last_seen: float = field(default_factory=time.time)
    
    def add_good_behavior(self, points: float = 0.1):
        """Increase score for good behavior."""
        self.score = min(100.0, self.score + points)
        self.last_seen = time.time()
    
    def add_bad_behavior(self, penalty: float = 1.0):
        """Decrease score for bad behavior."""
        self.score = max(-100.0, self.score - penalty)
        self.last_seen = time.time()
    
    def is_banned(self) -> bool:
        """Check if peer should be banned."""
        return self.score < -10.0


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    rate: float  # Tokens per second
    capacity: float  # Max tokens
    tokens: float = field(init=False)
    last_refill: float = field(default_factory=time.time)
    
    def __post_init__(self):
        self.tokens = self.capacity
    
    def try_consume(self, count: float = 1.0) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.time()
        elapsed = now - self.last_refill
        
        # Refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now
        
        # Try consume
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False


class Peer:
    """
    Peer state machine.
    
    Manages connection state, scoring, and rate limiting for a single peer.
    """
    
    def __init__(
        self,
        peer_id: str,
        addr: str,
        direction: str,  # "inbound" or "outbound"
    ):
        self.peer_id = peer_id
        self.addr = addr
        self.direction = direction
        
        self.state = PeerState.CONNECTING
        self.info: Optional[PeerInfo] = None
        self.score = PeerScore()
        
        # Rate limiting
        self.inv_bucket = TokenBucket(rate=10.0, capacity=50.0)  # 10 inv/sec
        self.getdata_bucket = TokenBucket(rate=20.0, capacity=100.0)  # 20 getdata/sec
        self.msg_bucket = TokenBucket(rate=50.0, capacity=200.0)  # 50 msgs/sec
        
        # RTT tracking
        self.rtt_ms: Optional[float] = None
        self.last_ping_time: Optional[float] = None
        
        # Activity tracking
        self.connected_at = time.time()
        self.last_recv = time.time()
        self.last_send = time.time()
        
        # Inflight tracking
        self.inflight_headers: set[str] = set()  # Request IDs
        self.inflight_blocks: set[str] = set()  # Block hashes
        self.inflight_txs: set[str] = set()  # Tx hashes
    
    def complete_handshake(self, info: PeerInfo):
        """Complete handshake and move to CONNECTED state."""
        self.info = info
        self.state = PeerState.CONNECTED
    
    def update_rtt(self, rtt_ms: float):
        """Update RTT measurement."""
        if self.rtt_ms is None:
            self.rtt_ms = rtt_ms
        else:
            # Exponential moving average
            self.rtt_ms = 0.8 * self.rtt_ms + 0.2 * rtt_ms
    
    def record_recv(self, msg: Message):
        """Record message received."""
        self.last_recv = time.time()
    
    def record_send(self, msg: Message):
        """Record message sent."""
        self.last_send = time.time()
    
    def check_rate_limit(self, msg_type: str) -> bool:
        """Check if message is rate-limited. Returns True if allowed."""
        # General message rate limit
        if not self.msg_bucket.try_consume(1.0):
            return False
        
        # Specific limits
        if msg_type == "inv":
            return self.inv_bucket.try_consume(1.0)
        elif msg_type == "getdata":
            return self.getdata_bucket.try_consume(1.0)
        
        return True
    
    def is_stale(self, timeout: float = 120.0) -> bool:
        """Check if peer is stale (no activity)."""
        return time.time() - self.last_recv > timeout
    
    def ban(self):
        """Ban this peer."""
        self.state = PeerState.BANNED
        self.score.score = -100.0
    
    def disconnect(self):
        """Disconnect this peer."""
        self.state = PeerState.DISCONNECTED
