"""
animica.stratum_pool.version_gate
=================================

Pool-level miner-version enforcement. When enabled, the pool rejects shares and
AICF (AI) jobs from miners running software older than a required minimum, so the
operator can require everyone to update to the unified mining+AI build. This is a
**pool policy only** — it never touches chain consensus, so it cannot fork or
stall the network, and it is reversible by clearing the config flag.

Miners advertise their version in the ``mining.subscribe`` features dict, under
any of ``version`` / ``agent_version`` / ``aicf.version``. A miner that reports
no version (every pre-update build) is treated as too old when enforcement is on
— which is exactly the "previous mining versions are rejected" behaviour.
"""

from __future__ import annotations

from typing import Any, Optional

# Baseline the unified mining+AI build advertises. Operators set the *enforced*
# minimum via config (ANIMICA_POOL_MIN_MINER_VERSION); this constant is just the
# version the bundled miner reports so a same-build fleet passes by default.
# The unified mining + AI-training + AI-serving build ships as 1.0.0.
UNIFIED_MINER_VERSION = "1.0.0"

_REJECT_REASON = "miner version too old — update to the unified animica miner"


def parse_version(value: Any) -> Optional[tuple[int, ...]]:
    """Parse a dotted version (``"0.5.0"``, ``"v0.5"``, ``"0.5.0-rc1"``) to a tuple.

    Returns ``None`` for anything unparseable (treated as "no version").
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s.startswith("v"):
        s = s[1:]
    # drop a pre-release/build suffix: "1.0.0-rc1" / "1.0.0_rc1" / "1.0.0+abc"
    for sep in ("-", "_", "+", " "):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts = s.split(".")
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None
        out.append(int(p))
    return tuple(out) if out else None


def version_ge(reported: Any, minimum: Any) -> bool:
    """True iff ``reported`` is a parseable version >= ``minimum``."""
    rv = parse_version(reported)
    mv = parse_version(minimum)
    if mv is None:        # no/garbage minimum ⇒ nothing to enforce
        return True
    if rv is None:        # miner reported no usable version ⇒ too old
        return False
    # pad to equal length so (0,5) == (0,5,0)
    n = max(len(rv), len(mv))
    rv = rv + (0,) * (n - len(rv))
    mv = mv + (0,) * (n - len(mv))
    return rv >= mv


def extract_version(features: Optional[dict[str, Any]]) -> str:
    """Pull the miner-reported version out of mining.subscribe features."""
    f = features or {}
    if not isinstance(f, dict):
        return ""
    for key in ("version", "agent_version", "miner_version", "client_version"):
        v = f.get(key)
        if v:
            return str(v)
    aicf = f.get("aicf")
    if isinstance(aicf, dict) and aicf.get("version"):
        return str(aicf["version"])
    return ""


def evaluate(features: Optional[dict[str, Any]], minimum: Any,
             *, enforce: bool = True) -> dict[str, Any]:
    """Decide whether a subscribing miner is allowed.

    Returns ``{"ok", "reported", "minimum", "enforced", "reason"}``. When
    ``enforce`` is False or no minimum is set, ``ok`` is always True (the gate is
    inert) but the reported version is still surfaced for logging/metrics.
    """
    reported = extract_version(features)
    minimum_s = str(minimum or "").strip()
    enforced = bool(enforce and parse_version(minimum_s) is not None)
    if not enforced:
        return {"ok": True, "reported": reported, "minimum": minimum_s,
                "enforced": False, "reason": ""}
    ok = version_ge(reported, minimum_s)
    return {"ok": ok, "reported": reported, "minimum": minimum_s,
            "enforced": True, "reason": "" if ok else _REJECT_REASON}
