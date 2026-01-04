"""PTL metrics and monitoring."""

from __future__ import annotations

import time
from typing import Dict, Any
from dataclasses import dataclass, field


@dataclass
class PtlMetrics:
    """Metrics for PTL monitoring."""

    # Transaction counts by status
    status_counts: Dict[str, int] = field(default_factory=dict)

    # Replication metrics
    total_receipts: int = 0
    ack_receipts: int = 0
    reject_receipts: int = 0
    timeout_receipts: int = 0

    # Performance metrics
    avg_replication_time: float = 0.0
    avg_inclusion_time: float = 0.0

    # Storage metrics
    total_size_bytes: int = 0
    pending_count: int = 0
    terminal_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status_counts": self.status_counts,
            "replication": {
                "total_receipts": self.total_receipts,
                "ack_receipts": self.ack_receipts,
                "reject_receipts": self.reject_receipts,
                "timeout_receipts": self.timeout_receipts,
            },
            "performance": {
                "avg_replication_time_ms": self.avg_replication_time * 1000,
                "avg_inclusion_time_ms": self.avg_inclusion_time * 1000,
            },
            "storage": {
                "total_size_bytes": self.total_size_bytes,
                "pending_count": self.pending_count,
                "terminal_count": self.terminal_count,
            },
        }


__all__ = ["PtlMetrics"]
