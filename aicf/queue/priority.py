from __future__ import annotations
from typing import Iterable, List
from aicf.aitypes.job import JobRecord

_TIER_ORDER = {"gold": 0, "premium": 1, "standard": 2}

def _tier_score(t: str | None) -> int:
    return _TIER_ORDER.get((t or "standard").lower(), 2)

def rank(jobs: Iterable[JobRecord], now: float | None = None, seed: int | None = None) -> List[JobRecord]:
    """
    Sort jobs by:
      1) fee (desc)
      2) age (older first -> created_at asc)
      3) size_bytes (smaller first)
      4) tier (gold < premium < standard)
      5) job_id (lexicographic tie-breaker; deterministic)
    """
    return sorted(
        list(jobs),
        key=lambda j: (
            -int(j.fee),
            float(j.created_at),
            int(j.size_bytes),
            _tier_score(getattr(j, "tier", "standard")),
            str(getattr(j, "job_id", "")),
        ),
    )
