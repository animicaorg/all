"""What this install is allowed to do.

The shape (decided 2026-08-08)
------------------------------
======================  ==================================  ==========================
                        free                                pro — $9.99/mo
======================  ==================================  ==========================
chat                    unlimited                           unlimited
agentic tasks           10 per day                          unlimited
agentic iterations      10 per task                         50 per task
tier                    standard                            flagship + long context
thread sync             local + your own DA namespace       hosted sync
======================  ==================================  ==========================

Pro is sold as a PayPal subscription on animica.dev/pricing, which issues a
licence key. The key lives in ``~/.animica/licence`` and is sent to the
entitlement API; the CLI never contains logic that decides someone is Pro on its
own say-so.

Two honest notes, because pretending otherwise would be worse
-------------------------------------------------------------
1. **The free caps are enforced locally and are trivially resettable.** The
   counter is a JSON file in the user's own home directory; anyone who wants to
   delete it can. That is deliberate — it is a friction gate on a free tier, not
   DRM, and building real client-side enforcement into an open-source CLI is not
   possible. What *is* enforced server-side is the thing that costs money: the
   hosted endpoint's own rate limit and tier selection. If the caps ever need to
   bite, they have to move there.
2. **Verification fails toward the tier you last proved, then toward free.** An
   unreachable entitlement API must not downgrade a paying user mid-session, so a
   successful check is cached and honoured for ``GRACE_DAYS``. It must also not
   hand Pro to everyone when the API is down, so the cache expires and the
   fallback is free rather than Pro.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

TIER_FREE = "free"
TIER_PRO = "pro"

DEFAULT_ENTITLEMENT_URL = "https://animica.dev/api/cli/entitlement"
PRICING_URL = "https://animica.dev/pricing"

# Free-tier limits.
FREE_AGENT_TASKS_PER_DAY = 10
FREE_AGENT_ITERATIONS = 10
PRO_AGENT_ITERATIONS = 50

# How long a verified Pro licence is honoured without re-checking.
GRACE_DAYS = 7

_UPGRADE_HINT = (
    f"free tier: {FREE_AGENT_TASKS_PER_DAY} agentic tasks/day, "
    f"{FREE_AGENT_ITERATIONS} iterations each. "
    f"$9.99/mo lifts both — {PRICING_URL}"
)


def _state_dir() -> Path:
    home = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


def licence_path() -> Path:
    return _state_dir() / "licence"


def _usage_path() -> Path:
    return _state_dir() / "cli_usage.json"


def read_licence() -> Optional[str]:
    """The licence key, or None. Whitespace-stripped; never logged anywhere."""
    env = os.environ.get("ANIMICA_LICENCE") or os.environ.get("ANIMICA_LICENSE")
    if env and env.strip():
        return env.strip()
    p = licence_path()
    try:
        key = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def write_licence(key: str) -> Path:
    """Store the key 0600 — it is a bearer credential."""
    p = licence_path()
    p.write_text(key.strip() + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def masked(key: Optional[str]) -> str:
    """For display. A licence key is a credential, so only its shape is shown."""
    if not key:
        return "(none)"
    k = key.strip()
    if len(k) <= 10:
        return "*" * len(k)
    return f"{k[:6]}…{k[-4:]}"


@dataclass
class Entitlements:
    tier: str = TIER_FREE
    agent_tasks_per_day: Optional[int] = FREE_AGENT_TASKS_PER_DAY   # None = unlimited
    agent_iterations: int = FREE_AGENT_ITERATIONS
    hosted_sync: bool = False
    long_context: bool = False
    source: str = "default"          # how we decided: default|licence|cache|env
    reason: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def is_pro(self) -> bool:
        return self.tier == TIER_PRO

    @classmethod
    def free(cls, reason: str = "", source: str = "default") -> "Entitlements":
        return cls(tier=TIER_FREE, reason=reason, source=source)

    @classmethod
    def pro(cls, reason: str = "", source: str = "licence") -> "Entitlements":
        return cls(
            tier=TIER_PRO,
            agent_tasks_per_day=None,
            agent_iterations=PRO_AGENT_ITERATIONS,
            hosted_sync=True,
            long_context=True,
            source=source,
            reason=reason,
        )

    def describe(self) -> str:
        if self.is_pro:
            return f"pro — unlimited agentic tasks, {self.agent_iterations} iterations"
        return (f"free — {self.agent_tasks_per_day} agentic tasks/day, "
                f"{self.agent_iterations} iterations")


def _cache_path() -> Path:
    return _state_dir() / "entitlement_cache.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(data: dict) -> None:
    try:
        p = _cache_path()
        p.write_text(json.dumps(data), encoding="utf-8")
        p.chmod(0o600)
    except OSError:
        pass


def resolve(*, offline_ok: bool = True, timeout: float = 6.0) -> Entitlements:
    """Work out what this install may do.

    No licence key means free, with no network call at all — the common case must
    not pay a round-trip to learn it is the default.
    """
    key = read_licence()
    if not key:
        return Entitlements.free(reason="no licence key")

    url = os.environ.get("ANIMICA_ENTITLEMENT_URL") or DEFAULT_ENTITLEMENT_URL
    try:
        body = json.dumps({"licence": key}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"content-type": "application/json",
                     "user-agent": "animica-cli/entitlement"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — any failure falls back
        cached = _load_cache()
        if cached.get("tier") == TIER_PRO and offline_ok:
            age_days = (time.time() - float(cached.get("checked_at") or 0)) / 86400.0
            if age_days <= GRACE_DAYS:
                return Entitlements.pro(
                    reason=f"cached (offline, {age_days:.1f}d of {GRACE_DAYS}d grace)",
                    source="cache",
                )
        # Fall back to FREE, never to pro: an unreachable API must not be a way
        # to get the paid tier.
        return Entitlements.free(reason=f"entitlement check failed: {_short(exc)}")

    active = bool(payload.get("active"))
    tier = str(payload.get("tier") or (TIER_PRO if active else TIER_FREE)).lower()
    if active and tier == TIER_PRO:
        _save_cache({"tier": TIER_PRO, "checked_at": time.time()})
        ent = Entitlements.pro(reason=str(payload.get("reason") or "licence active"))
        # The server may tighten or loosen the numbers without a CLI release.
        if isinstance(payload.get("agent_iterations"), int):
            ent.agent_iterations = int(payload["agent_iterations"])
        if payload.get("agent_tasks_per_day") is not None:
            ent.agent_tasks_per_day = payload["agent_tasks_per_day"]
        return ent

    _save_cache({"tier": TIER_FREE, "checked_at": time.time()})
    return Entitlements.free(
        reason=str(payload.get("reason") or "licence not active"), source="licence")


# --------------------------------------------------------------------------- #
# Free-tier daily counter                                                     #
# --------------------------------------------------------------------------- #

def _today() -> str:
    return date.today().isoformat()


def agent_tasks_used_today() -> int:
    data = _load_usage()
    return int(data.get("agent_tasks", {}).get(_today(), 0))


def _load_usage() -> dict:
    try:
        return json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_agent_task() -> int:
    """Count one agentic task against today. Returns the new total."""
    data = _load_usage()
    tasks = data.setdefault("agent_tasks", {})
    today = _today()
    tasks[today] = int(tasks.get(today, 0)) + 1
    # Keep the file small: a fortnight is plenty of history for a daily cap.
    for day in sorted(tasks)[:-14]:
        tasks.pop(day, None)
    try:
        _usage_path().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return tasks[today]


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    iterations: int = FREE_AGENT_ITERATIONS
    upgrade_hint: str = ""


def check_agent_task(ent: Entitlements, *, requested_iterations: Optional[int] = None) -> Verdict:
    """May this install start an agentic task, and with what iteration cap?

    The iteration cap is CLAMPED rather than rejected: someone who passes
    `--max-iterations 200` on the free tier gets 10 and is told so, which is more
    useful than an error telling them to try again with a different number.
    """
    cap = ent.agent_iterations
    iterations = cap if requested_iterations is None else min(int(requested_iterations), cap)

    if ent.agent_tasks_per_day is None:
        return Verdict(True, reason=f"{ent.tier} tier", iterations=iterations)

    used = agent_tasks_used_today()
    if used >= ent.agent_tasks_per_day:
        return Verdict(
            False,
            reason=(f"free tier allows {ent.agent_tasks_per_day} agentic tasks per day; "
                    f"{used} used today"),
            iterations=iterations,
            upgrade_hint=_UPGRADE_HINT,
        )
    remaining = ent.agent_tasks_per_day - used - 1
    return Verdict(
        True,
        reason=f"free tier — {remaining} more task(s) today after this one",
        iterations=iterations,
        upgrade_hint="" if remaining > 2 else _UPGRADE_HINT,
    )


def _short(exc: Exception, limit: int = 100) -> str:
    s = str(exc) or exc.__class__.__name__
    return s if len(s) <= limit else s[:limit] + "…"


__all__ = [
    "TIER_FREE", "TIER_PRO", "Entitlements", "Verdict", "resolve",
    "check_agent_task", "record_agent_task", "agent_tasks_used_today",
    "read_licence", "write_licence", "licence_path", "masked",
    "FREE_AGENT_TASKS_PER_DAY", "FREE_AGENT_ITERATIONS", "PRO_AGENT_ITERATIONS",
    "PRICING_URL", "GRACE_DAYS",
]
