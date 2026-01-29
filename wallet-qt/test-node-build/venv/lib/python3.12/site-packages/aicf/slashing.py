from __future__ import annotations


class Engine:
    def __init__(
        self,
        traps_min: float = 0.98,
        qos_min: float = 0.90,
        jail_after_violations: int = 2,
        cooldown_blocks: int = 5,
    ):
        self.traps_min = traps_min
        self.qos_min = qos_min
        self.jail_after_violations = jail_after_violations
        self.cooldown_blocks = cooldown_blocks

    def process_window(self, provider, height: int, stats: dict):
        total = max(1, int(stats.get("total", 0)))
        traps_ok = int(stats.get("traps_ok", 0))
        qos_ok = int(stats.get("qos_ok", 0))
        traps_ratio = traps_ok / total
        qos_ratio = qos_ok / total

        # If jailed and cooldown elapsed, allow good window to unjail
        if getattr(provider, "jailed", False) and height >= getattr(
            provider, "jail_until_height", 0
        ):
            if traps_ratio >= self.traps_min and qos_ratio >= self.qos_min:
                provider.jailed = False
                provider.jail_until_height = 0
                provider.violations = 0
                return {"event": "unjail", "height": height}
            return None

        # Otherwise, on poor performance increase violations and possibly jail
        if traps_ratio < self.traps_min or qos_ratio < self.qos_min:
            provider.violations = getattr(provider, "violations", 0) + 1
            if provider.violations >= self.jail_after_violations:
                provider.jailed = True
                provider.jail_until_height = height + self.cooldown_blocks
                return {"event": "jail", "height": height}
        return None


# Optional helper used by some harnesses
def get_engine() -> Engine:
    return Engine()
