from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum, IntFlag
from typing import Dict, NewType, Optional

ProviderId = NewType("ProviderId", str)

class Capability(IntFlag):
    NONE = 0
    AI = 1
    QUANTUM = 2

class ProviderStatus(str, Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    PAUSED = "paused"
    JAILED = "jailed"
    INACTIVE = "inactive"
    RETIRED = "retired"

@dataclass(frozen=True)
class Provider:
    id: ProviderId
    capabilities: Capability
    endpoints: Dict[str, str]
    stake: int
    status: ProviderStatus
    created_at: datetime
    updated_at: datetime
    last_heartbeat: Optional[datetime] = None
    region: Optional[str] = None
    meta: Dict[str, str] = field(default_factory=dict)

    def supports(self, kind: str) -> bool:
        k = kind.lower()
        if k == "ai":
            return bool(self.capabilities & Capability.AI)
        if k == "quantum":
            return bool(self.capabilities & Capability.QUANTUM)
        return False

    def with_status(self, status: ProviderStatus) -> "Provider":
        return replace(self, status=status, updated_at=_now())

    def heartbeat(self) -> "Provider":
        now = _now()
        return replace(self, last_heartbeat=now, updated_at=now)

def _now() -> datetime:
    return datetime.now(timezone.utc)

__all__ = ["ProviderId", "Capability", "ProviderStatus", "Provider"]
