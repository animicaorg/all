from __future__ import annotations
from dataclasses import dataclass, field
from typing import Set

@dataclass
class Allowlist:
    denied_ids: Set[str] = field(default_factory=set)
    denied_regions: Set[str] = field(default_factory=set)

    def is_denied(self, provider_id: str, region: str) -> bool:
        return (provider_id in self.denied_ids) or (region in self.denied_regions)

__all__ = ["Allowlist"]
