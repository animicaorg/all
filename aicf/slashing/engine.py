from __future__ import annotations
from typing import Any, Optional

class Engine:
    """
    Minimal slashing engine for tests.

    Behavior:
      - While jailed: once cooldown has elapsed (>=) AND the window is "good", unjail.
      - On bad windows when not jailed: increment violations and soft-slash stake.
      - Jail when violations reach the configured threshold.
    """

    # ---- discovery helpers (some fixtures probe these) ----
    name: str = "AnimicaTestEngine"
    id: str = "animica"

    @classmethod
    def create(cls, thresholds: Any, penalties: Any) -> "Engine":
        return cls(thresholds, penalties)

    @staticmethod
    def available() -> bool:
        return True

    def __init__(self, thresholds: Any, penalties: Any) -> None:
        self.traps_min = float(getattr(thresholds, "traps_min", 0.98))
        self.qos_min   = float(getattr(thresholds, "qos_min", 0.90))
        self.cooldown_blocks = int(getattr(penalties, "cooldown_blocks", 5))
        self.penalty_per_violation = int(getattr(penalties, "penalty_per_violation", 0))
        self.jail_after_violations = int(getattr(penalties, "jail_after_violations", 2))

    def _good(self, stats: dict) -> bool:
        total = max(1, int(stats.get("total", 0)))
        traps_ok = int(stats.get("traps_ok", 0))
        qos_ok = int(stats.get("qos_ok", 0))
        return (traps_ok / total) >= self.traps_min and (qos_ok / total) >= self.qos_min

    def _slash_soft(self, provider: Any) -> None:
        try:
            provider.stake = max(0, int(getattr(provider, "stake", 0)) - self.penalty_per_violation)
        except Exception:
            pass

    def process_window(self, provider: Any, height: int, stats: dict) -> Optional[dict]:
        # If jailed: a GOOD window at or after cooldown end unjails
        if getattr(provider, "jailed", False):
            until = int(getattr(provider, "jail_until_height", 0))
            if height >= until and self._good(stats):
                provider.jailed = False
                provider.violations = 0
                return {"event": "unjail", "height": height}
            return None

        # Not jailed: GOOD does nothing
        if self._good(stats):
            return None

        # BAD: increment violations and soft-slash
        provider.violations = int(getattr(provider, "violations", 0)) + 1
        self._slash_soft(provider)

        if provider.violations >= self.jail_after_violations:
            provider.jailed = True
            provider.jail_until_height = height + self.cooldown_blocks
            return {"event": "jail", "height": height, "until": provider.jail_until_height}

        return {"event": "warn", "height": height, "violations": provider.violations}
