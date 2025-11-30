from __future__ import annotations

from typing import Mapping, Optional


def process_window(
    provider, height: int, stats: Mapping[str, int], thresholds=None, penalties=None
):
    """
    Minimal engine:
      - Before cooldown end: do nothing (return None)
      - At/after cooldown end with good stats: unjail and return an event
    Tests feed stats like total=200, traps_ok=199, qos_ok=190 (very good).
    """
    if not getattr(provider, "jailed", False):
        return None
    jail_until = int(getattr(provider, "jail_until_height", 0))
    if height < jail_until:
        return None
    total = int(stats.get("total", 0))
    traps_ok = int(stats.get("traps_ok", 0))
    qos_ok = int(stats.get("qos_ok", 0))
    # simple goodness check
    good = total > 0 and traps_ok >= total - 1 and qos_ok >= int(0.75 * total)
    if good:
        provider.jailed = False
        return {"event": "unjail", "height": height}
    return None
