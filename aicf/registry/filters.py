from __future__ import annotations
from aicf.queue.jobkind import JobKind

"""
AICF Provider Eligibility & Selection

This module filters and ranks providers for job assignment based on:
- Stake (min threshold and scoring)
- Health (live score via heartbeat monitor) and status gating
- Region allow/deny and region preference boosts
- Capability flags (AI / QUANTUM)
- Algorithm/model support (provider-declared features vs required sets)

Design goals
------------
- Storage-agnostic (works with any registry). Accepts an iterable of provider
  records and a health lookup function.
- Duck-typed providers: we read common attribute names with robust fallbacks.
- Clear reasons when a provider is filtered out (for observability).
- Simple composite score to break ties fairly: primarily health, then stake,
  with small regional preference boost. Deterministic ordering.

Typical usage
-------------
    from aicf.registry.heartbeat import HeartbeatMonitor
    from aicf.aitypes.job import JobKind

    hb = HeartbeatMonitor()
    criteria = FilterCriteria(
        require_caps={JobKind.AI},
        min_stake_total=10_000,
        allowed_regions={"us", "eu"},
        min_health=0.5,
        prefer_regions={"us"},
        require_status_any={"HEALTHY", "DEGRADED"},
        require_alg_superset={"ai_models": {"llama-3.1-8b"}}
    )

    winners = eligible_providers(
        providers, criteria, health_fn=hb.current_status, limit=8
    )
    # winners is a list[Ranked] with .provider, .score, .health, .stake_normalized

Notes
-----
- Health is sourced via `health_fn(provider_id) -> (status, score, last_seen_ts)`.
- Algorithm support uses a dict[str, set[str]] on the provider, e.g.:
    provider.alg_support = {"ai_models": {"llama-3.1-8b", "mistral-7b"}}
  You can map other keys (e.g., "quantum_backends", "quantum_gates").
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Protocol

# Optional import of JobKind (AI/QUANTUM). We keep things flexible if not present.
try:  # pragma: no cover - optional import for ergonomics
    from aicf.aitypes.job import JobKind
except Exception:  # pragma: no cover
    class JobKind:  # type: ignore[no-redef]
        AI = "AI"
        QUANTUM = "QUANTUM"


# ---- Duck-typed provider protocol -------------------------------------------

class ProviderLike(Protocol):
    provider_id: str  # unique id
    # Stake attributes
    stake_total: float  # preferred
    # fallbacks we may look for: stake, stake_locked

    # Capabilities (set of JobKind or strings like "AI", "QUANTUM")
    capabilities: Set[Any]

    # Regions: set of ISO-ish region codes or a single region string
    regions: Set[str]  # preferred
    # fallback: region: str

    # Optional: algorithm/model support
    alg_support: Dict[str, Set[str]]  # e.g., {"ai_models": {"llama-3.1-8b"}}

    # Optional: freeform metadata / endpoints
    # endpoints: Dict[str, str]


# ---- Criteria & results ------------------------------------------------------

@dataclass(frozen=True)
class FilterCriteria:
    require_caps: Set[Any] = frozenset()
    min_stake_total: float = 0.0

    # Region gating
    allowed_regions: Optional[Set[str]] = None  # if set, provider must overlap
    denied_regions: Optional[Set[str]] = None   # if set, provider must NOT overlap
    prefer_regions: Set[str] = frozenset()      # small tie-breaker / boost

    # Health & status gating
    min_health: float = 0.0
    require_status_any: Set[str] = frozenset({"HEALTHY", "DEGRADED"})

    # Algorithm/model support: provider.alg_support[key] must be a superset
    # of the required set (for each key present here).
    require_alg_superset: Dict[str, Set[str]] = None  # type: ignore[assignment]

    # Scoring weights
    weight_health: float = 0.7
    weight_stake: float = 0.3
    region_bonus: float = 0.05  # additive if provider is in a preferred region

    def __post_init__(self) -> None:
        # ensure mutables are sane
        if self.require_alg_superset is None:  # noqa: WPS504
            object.__setattr__(self, "require_alg_superset", {})  # type: ignore[misc]


@dataclass
class Ranked:
    provider: Any
    score: float
    health: float
    status: str
    stake_normalized: float
    region_hit: bool


@dataclass
class FilteredOut:
    provider: Any
    reasons: List[str]


HealthFn = Callable[[str], Tuple[str, float, float]]  # returns (status, score, last_seen_ts)


# ---- Public API --------------------------------------------------------------

def eligible_providers(
    providers: Iterable[Any],
    criteria: FilterCriteria,
    *,
    health_fn: Optional[HealthFn] = None,
    limit: Optional[int] = None,
    collect_filtered: Optional[List[FilteredOut]] = None,
) -> List[Ranked]:
    """
    Filter and rank providers according to `criteria`.

    Parameters
    ----------
    providers : iterable of provider-like objects
    criteria : FilterCriteria
    health_fn : callable(provider_id) -> (status, score, last_seen_ts)
        If not provided, health is assumed unknown (0.0) with status "UNKNOWN".
    limit : int | None
        If provided, truncate the ranked list to at most this many entries.
    collect_filtered : list | None
        If provided, append FilteredOut entries with reasons for ineligibility.

    Returns
    -------
    List[Ranked] sorted by descending `score`, then provider_id for determinism.
    """
    health_fn = health_fn or (lambda _pid: ("UNKNOWN", 0.0, 0.0))

    # Precompute max stake for normalization to [0,1]
    stakes: List[float] = [float(_read_stake(p)) for p in providers]
    # Need to iterate again; convert providers to a list
    providers_list = list(providers)
    if not stakes:
        return []
    max_stake = max(stakes) if max(stakes) > 0 else 1.0

    ranked: List[Ranked] = []
    for p in providers_list:
        reasons: List[str] = []
        pid = _read_provider_id(p)

        # --- Capabilities
        if criteria.require_caps:
            if not _has_all_caps(p, criteria.require_caps):
                reasons.append("missing required capabilities")
        # --- Stake
        stake = float(_read_stake(p))
        if stake < criteria.min_stake_total:
            reasons.append(f"stake below minimum ({stake:.0f} < {criteria.min_stake_total:.0f})")
        stake_norm = min(1.0, stake / max_stake) if max_stake > 0 else 0.0

        # --- Regions
        prov_regions = _read_regions(p)
        if criteria.allowed_regions is not None:
            if not prov_regions.intersection(criteria.allowed_regions):
                reasons.append("no overlap with allowed regions")
        if criteria.denied_regions is not None:
            if prov_regions.intersection(criteria.denied_regions):
                reasons.append("matches denied regions")
        region_hit = bool(criteria.prefer_regions and prov_regions.intersection(criteria.prefer_regions))

        # --- Algorithm/model support
        if criteria.require_alg_superset:
            alg_support = _read_alg_support(p)
            for key, required_set in criteria.require_alg_superset.items():
                req = set(required_set)
                have = set(alg_support.get(key, set()))
                if not req.issubset(have):
                    reasons.append(f"alg_support[{key}] missing: need {sorted(req - have)}")

        # --- Health & status
        status, health, _last = health_fn(pid)
        if criteria.require_status_any and status not in criteria.require_status_any:
            reasons.append(f"status not allowed ({status})")
        if health < criteria.min_health:
            reasons.append(f"health below minimum ({health:.2f} < {criteria.min_health:.2f})")

        if reasons:
            if collect_filtered is not None:
                collect_filtered.append(FilteredOut(provider=p, reasons=reasons))
            continue

        # --- Scoring: health primary, then stake, then small region bump.
        score = (
            criteria.weight_health * health
            + criteria.weight_stake * stake_norm
            + (criteria.region_bonus if region_hit else 0.0)
        )
        ranked.append(
            Ranked(
                provider=p,
                score=score,
                health=health,
                status=status,
                stake_normalized=stake_norm,
                region_hit=region_hit,
            )
        )

    # Deterministic sort: by score desc, then provider_id asc
    ranked.sort(key=lambda r: (-r.score, _read_provider_id(r.provider)))
    if limit is not None and limit >= 0:
        ranked = ranked[:limit]
    return ranked


def explain_ineligible(
    providers: Iterable[Any],
    criteria: FilterCriteria,
    *,
    health_fn: Optional[HealthFn] = None,
) -> List[FilteredOut]:
    """
    Convenience helper to get only the filtered-out providers with reasons.
    """
    out: List[FilteredOut] = []
    _ = eligible_providers(
        providers, criteria, health_fn=health_fn, limit=None, collect_filtered=out
    )
    return out


# ---- Internal helpers --------------------------------------------------------

def _read_provider_id(p: Any) -> str:
    return getattr(p, "provider_id", getattr(p, "id", str(p)))


def _read_stake(p: Any) -> float:
    for attr in ("stake_total", "stake", "stake_locked", "stake_tokens"):
        v = getattr(p, attr, None)
        if v is not None:
            try:
                return float(v)
            except Exception:
                return 0.0
    return 0.0


def _read_regions(p: Any) -> Set[str]:
    # Preferred attr: 'regions' as set/list; fallback to 'region' as string
    v = getattr(p, "regions", None)
    if v is None:
        s = getattr(p, "region", None)
        if isinstance(s, str) and s:
            return {s.lower()}
        return set()
    if isinstance(v, (list, tuple, set, frozenset)):
        return {str(x).lower() for x in v}
    if isinstance(v, str) and v:
        # allow comma-separated strings
        return {s.strip().lower() for s in v.split(",")}
    return set()


def _has_all_caps(p: Any, required_caps: Set[Any]) -> bool:
    caps = getattr(p, "capabilities", None)
    if not caps:
        return False
    # Normalize to a set of strings for comparison
    def _norm(x: Any) -> str:
        return getattr(x, "name", None) or getattr(x, "value", None) or str(x)
    have = {_norm(c).upper() for c in caps}
    need = {_norm(c).upper() for c in required_caps}
    return need.issubset(have)


def _read_alg_support(p: Any) -> Dict[str, Set[str]]:
    d = getattr(p, "alg_support", None)
    if not isinstance(d, dict):
        return {}
    out: Dict[str, Set[str]] = {}
    for k, v in d.items():
        if isinstance(v, (list, tuple, set, frozenset)):
            out[str(k)] = {str(x) for x in v}
        elif isinstance(v, str):
            out[str(k)] = {v}
    return out


__all__ = [
    "FilterCriteria",
    "Ranked",
    "FilteredOut",
    "eligible_providers",
    "explain_ineligible",
    "ProviderLike",
]
