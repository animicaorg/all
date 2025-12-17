from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class JobConfig:
    """
    Immutable snapshot of a Stratum job tailored for the OpenCL worker thread.

    Attributes:
        job_id: Unique identifier provided by the Stratum server.
        header_bytes: Canonical header sign-bytes (without nonce) as raw bytes.
        mix_seed: Canonical mix seed bytes (32 bytes).
        theta_micro: Current chain theta in micro-nats.
        share_target: Fractional share difficulty relative to theta (0-1].
        height: Block height for telemetry/logging.
        hints: Optional metadata forwarded from the server.
    """

    job_id: str
    header_bytes: bytes
    mix_seed: bytes
    theta_micro: int
    share_target: float
    height: int = 0
    hints: Dict[str, Any] = field(default_factory=dict)
    target_hex: Optional[str] = None

    @property
    def theta_share_micro(self) -> int:
        """
        Effective theta threshold for share acceptance.
        """
        base = max(self.theta_micro, 1)
        ratio = self.share_target if self.share_target > 0 else 1.0
        # Clamp to a positive integer to avoid passing zero to the GPU backend.
        return max(1, int(base * ratio))
