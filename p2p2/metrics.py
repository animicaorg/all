"""
Metrics collection for P2P2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class P2PMetrics:
    """Overall P2P metrics."""
    started_at: float = field(default_factory=time.time)
    
    # Connection metrics
    total_connections: int = 0
    inbound_connections: int = 0
    outbound_connections: int = 0
    banned_peers: int = 0
    
    # Message metrics
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    
    # Sync metrics
    headers_synced: int = 0
    blocks_synced: int = 0
    orphans_resolved: int = 0
    
    def to_dict(self) -> Dict:
        """Export as dictionary."""
        return {
            "uptime_seconds": time.time() - self.started_at,
            "connections": {
                "total": self.total_connections,
                "inbound": self.inbound_connections,
                "outbound": self.outbound_connections,
                "banned": self.banned_peers,
            },
            "messages": {
                "sent": self.messages_sent,
                "received": self.messages_received,
                "bytes_sent": self.bytes_sent,
                "bytes_received": self.bytes_received,
            },
            "sync": {
                "headers": self.headers_synced,
                "blocks": self.blocks_synced,
                "orphans_resolved": self.orphans_resolved,
            },
        }
