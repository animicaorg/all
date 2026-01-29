# Compatible proof selector for tests.
# Accepts flexible signatures and applies caps + total gamma cap.
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _ptype(c: Dict[str, Any]) -> str:
    return (
        c.get("type")
        or c.get("kind")
        or c.get("category")
        or c.get("id", "").split(":")[0]
        or "hash"
    )


def _psi(c: Dict[str, Any]) -> float:
    if "psi" in c:
        try:
            return float(c["psi"])
        except:
            return 0.0
    return float(c.get("metrics", {}).get("psi", 0.0))


def select_proofs(
    candidates: List[Dict[str, Any]],
    policy: Optional[Dict[str, Any]] = None,
    *,
    caps: Optional[Dict[str, int]] = None,
    per_type_caps: Optional[Dict[str, int]] = None,
    gamma_cap: Optional[float] = None,
    Gamma_cap: Optional[float] = None,
    total_cap: Optional[float] = None,
    total_Gamma: Optional[float] = None,
    escort_q: Optional[float] = None,
    fairness_q: Optional[float] = None,
    diversity_q: Optional[float] = None,
    weights: Optional[Dict[str, float]] = None,
    max_count: Optional[int] = None,
    limit: Optional[int] = None,
    # ignored optional knobs some implementations require:
    theta_micro: Optional[float] = None,
    h_u_micro: Optional[float] = None,
    **_kw: Any,
) -> List[Dict[str, Any]]:
    policy = policy or {}
    caps = (
        caps
        or per_type_caps
        or policy.get("per_type_caps")
        or policy.get("per_type")
        or {}
    )
    # normalize dicts like {"hash":{"cap":2}} -> {"hash":2}
    norm_caps: Dict[str, int] = {}
    for t, v in caps.items():
        if isinstance(v, dict) and "cap" in v:
            try:
                norm_caps[t] = int(v["cap"])
            except:
                norm_caps[t] = 0
        else:
            try:
                norm_caps[t] = int(v)
            except:
                norm_caps[t] = 0
    caps = norm_caps

    if weights is None:
        weights = policy.get("weights", {}) or {}

    # pick a gamma cap from any of the known aliases
    gcap = (
        gamma_cap
        if gamma_cap is not None
        else (
            Gamma_cap
            if Gamma_cap is not None
            else (
                total_cap
                if total_cap is not None
                else total_Gamma if total_Gamma is not None else policy.get("gamma_cap")
            )
        )
    )

    # escort/fairness present?
    q = (
        escort_q
        if escort_q is not None
        else (
            fairness_q
            if fairness_q is not None
            else diversity_q if diversity_q is not None else policy.get("escort_q")
        )
    )

    # effective per-call limit
    lim = (
        limit
        if limit is not None
        else max_count if max_count is not None else len(candidates)
    )

    # compute weighted score
    def score(c: Dict[str, Any]) -> float:
        t = _ptype(c)
        w = float(weights.get(t, 1.0))
        return _psi(c) * w

    # Sort by score desc
    ordered = sorted(candidates, key=score, reverse=True)
    by_type_present = {_ptype(c) for c in ordered}
    # If only one type is present, fairness must not block selection.
    apply_fairness = q is not None and len(by_type_present) > 1 and q > 0.0

    counts: Dict[str, int] = {}
    chosen: List[Dict[str, Any]] = []
    gamma_total = 0.0

    def under_cap(t: str) -> bool:
        cap = caps.get(t)
        if cap is None:
            return True
        return counts.get(t, 0) < cap

    for c in ordered:
        t = _ptype(c)
        if not under_cap(t):
            continue
        add_psi = score(c)

        # total gamma cap truncation
        if gcap is not None and (gamma_total + add_psi) > (gcap + 1e-12):
            continue

        # very light fairness heuristic: if one type already dominates by > (q) fraction,
        # try to diversify when others remain available under caps.
        if apply_fairness and sum(counts.values()) > 0:
            tot = sum(counts.values())
            frac_t = counts.get(t, 0) / max(1, tot)
            if frac_t > q:
                # see if another type is still selectable; if yes, defer this pick
                alt_ok = False
                for other in ordered:
                    ot = _ptype(other)
                    if ot == t:
                        continue
                    if counts.get(ot, 0) >= caps.get(ot, 1 << 30):
                        continue
                    if gcap is not None and (gamma_total + score(other)) > (
                        gcap + 1e-12
                    ):
                        continue
                    alt_ok = True
                    break
                if alt_ok:
                    continue

        chosen.append(c)
        counts[t] = counts.get(t, 0) + 1
        gamma_total += add_psi
        if len(chosen) >= lim:
            break

    return chosen
