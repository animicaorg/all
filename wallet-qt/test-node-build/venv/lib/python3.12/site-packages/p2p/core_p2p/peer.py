from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from .netaddress import NetAddress


@dataclass
class PeerState:
    peer_id: str
    address: NetAddress
    inbound: bool
    services: int = 0
    version: Optional[int] = None
    user_agent: str = ""
    start_height: int = 0
    relay: bool = True
    connected_at: float = field(default_factory=time.time)
    last_recv: float = field(default_factory=time.time)
    last_send: float = field(default_factory=time.time)
    ping_nonce: Optional[int] = None
    handshake_complete: bool = False
    version_received: bool = False
    version_sent: bool = False
    verack_received: bool = False
    verack_sent: bool = False
    known_inventory: Set[bytes] = field(default_factory=set)
    inflight_blocks: Dict[bytes, float] = field(default_factory=dict)
    misbehavior_score: int = 0
    best_header: Optional[bytes] = None

    def mark_recv(self) -> None:
        self.last_recv = time.time()

    def mark_send(self) -> None:
        self.last_send = time.time()
