from __future__ import annotations

"""
ConsensusViewAdapter
====================

A thin, import-resilient bridge from the miner to the consensus/ and proofs/
modules. It exposes:

• get_live_view()                 → live Θ (theta, µ-nats), Γ caps, escort-q and policy id
• set_theta_from_header(header)   → update Θ from a Header object (if it carries theta)
• score_preview(metrics_list)     → compute ψ_i for a batch of proofs and Σψ (with caps)
• acceptance_check(u_or_mu, ms)   → S = H(u) + Σψ vs Θ ; returns bool + rich breakdown

This adapter *prefers* calling the real implementations in:
  - consensus.policy / consensus.caps / consensus.scorer / consensus.math
  - proofs.policy_adapter (metrics → ψ-inputs)

If any of those are missing in your environment, it falls back to safe,
conservative estimators so single-node dev-mining still works (albeit with
simplified ψ).

Inputs
------
`metrics_list` is a list of per-proof metrics dictionaries as returned by
proofs/* verifiers (e.g. proofs.metrics.*). Examples (fields are illustrative):

    {"kind": "hash",    "d_ratio": 0.32}
    {"kind": "ai",      "ai_units": 420, "traps_ratio": 0.83, "qos": 0.97}
    {"kind": "quantum", "quantum_units": 12.4, "traps_ratio": 0.91, "qos": 0.95}
    {"kind": "storage", "qos": 0.99}
    {"kind": "vdf",     "vdf_seconds": 3.1}

The adapter maps these to the scorer’s ψ-inputs using proofs.policy_adapter
when available.

Units
-----
Θ and ψ are handled in fixed-point *µ-nats* (micro-nats) when interfacing with
consensus/math. If you pass a float *u* (uniform(0,1]) to `acceptance_check`,
we convert H(u)=−ln(u) to µ-nats internally.

Typical usage
-------------
    view = ConsensusViewAdapter.from_policy_file(
        policy_path="spec/poies_policy.yaml",
        theta_micro=2_300_000,   # live Θ (can be updated from header)
    )
    live = view.get_live_view()
    psi = view.score_preview(metrics_batch)
    ok, br = view.acceptance_check(u_or_mu=0.00012, metrics_list=metrics_batch)

"""

from dataclasses import dataclass
from math import log
from typing import Any, Dict, List, Optional, Tuple, Callable, Sequence

# ---------------------------------------------------------------------------
# Logging (best-effort)
try:
    from core.logging import get_logger
    log = get_logger("mining.adapters.consensus_view")
except Exception:  # noqa: BLE001
    import logging
    log = logging.getLogger("mining.adapters.consensus_view")
    if not log.handlers:
        logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Optional imports from consensus/
PolicyType = Any
try:
    from consensus.policy import load_policy as _load_policy  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _load_policy = None  # type: ignore[assignment]

try:
    from consensus.caps import apply_caps as _apply_caps  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _apply_caps = None  # type: ignore[assignment]

try:
    from consensus.math import to_micro_nats as _to_micro_nats  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _to_micro_nats = None  # type: ignore[assignment]

# Various scorer shapes, duck-typed at runtime
_ScorerCtor: Optional[Callable[..., Any]] = None
_score_fn: Optional[Callable[..., Any]] = None
try:
    # Preferred: a class with `.score_batch(psi_inputs)` and `.sum_psi(...)`
    from consensus.scorer import Scorer as _Scorer  # type: ignore[attr-defined]
    _ScorerCtor = _Scorer  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    try:
        # Fallback: a function `score_batch(policy, psi_inputs)` → per-item ψ
        from consensus.scorer import score_batch as _score_batch  # type: ignore[attr-defined]
        _score_fn = _score_batch
    except Exception:  # noqa: BLE001
        pass

# Optional mapping from proofs.metrics → ψ-inputs for the scorer
_map_metrics: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
try:
    from proofs.policy_adapter import metrics_to_psi as _metrics_to_psi  # type: ignore[attr-defined]
    _map_metrics = _metrics_to_psi  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    try:
        # Some trees export a slightly different name:
        from proofs.policy_adapter import map_metrics_to_psi as _map2  # type: ignore[attr-defined]
        _map_metrics = _map2  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        _map_metrics = None  # Final fallback below


@dataclass
class LiveView:
    theta_micro: int
    policy_id: Optional[str]
    total_gamma_cap: Optional[float]
    caps: Optional[Dict[str, float]]
    escort_q: Optional[float]


class ConsensusViewAdapter:
    """
    Live Θ/Γ/caps/escort view + scoring/acceptance helpers.
    """

    def __init__(self, policy: PolicyType, theta_micro: int) -> None:
        self.policy = policy
        self.theta_micro = int(theta_micro)

        # Try to read some optional fields from the policy (duck-typing)
        self._policy_id = getattr(policy, "policy_id", None) or getattr(policy, "id", None)
        self._total_gamma_cap = getattr(policy, "total_gamma_cap", None) or getattr(policy, "gamma_total", None)
        self._caps = getattr(policy, "caps", None) or getattr(policy, "per_type_caps", None)
        self._escort_q = getattr(policy, "escort_q", None) or getattr(policy, "escort_quota", None)

        # Prepare a scorer instance if the implementation is class-based
        self._scorer = _ScorerCtor(policy) if _ScorerCtor is not None else None

    # ------------------------------------------------------------------ ctors
    @classmethod
    def from_policy_file(cls, policy_path: str, theta_micro: int) -> "ConsensusViewAdapter":
        if _load_policy is None:
            raise ImportError("consensus.policy.load_policy is not available in this environment")
        policy = _load_policy(policy_path)  # type: ignore[no-any-return]
        return cls(policy=policy, theta_micro=theta_micro)

    @classmethod
    def from_policy_obj(cls, policy: Any, theta_micro: int) -> "ConsensusViewAdapter":
        return cls(policy=policy, theta_micro=theta_micro)

    # ------------------------------------------------------- live policy view
    def get_live_view(self) -> LiveView:
        return LiveView(
            theta_micro=self.theta_micro,
            policy_id=self._policy_id,
            total_gamma_cap=self._total_gamma_cap,
            caps=self._caps if isinstance(self._caps, dict) else None,
            escort_q=float(self._escort_q) if self._escort_q is not None else None,
        )

    # Θ can be carried in the header (depending on your chain header design)
    def set_theta_from_header(self, header: Any) -> None:
        # Try the common attribute names
        for name in ("theta", "Theta", "theta_micro", "ThetaMicro"):
            if hasattr(header, name):
                val = getattr(header, name)
                self.theta_micro = int(val)
                log.debug("Theta updated from header", extra={"theta_micro": self.theta_micro})
                return

    # ----------------------------------------------------------- ψ computation
    def _map_metrics_to_psi_inputs(self, metrics_item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a metrics dict into the scorer's ψ-input record using proofs.policy_adapter
        if present; otherwise accept a precomputed {'psi': …, 'kind': …} or apply a very
        conservative heuristic so dev-mining still functions.
        """
        if _map_metrics is not None:
            return _map_metrics(metrics_item)  # type: ignore[no-any-return]

        # Fallbacks:
        if "psi" in metrics_item:
            # Already pre-computed by caller
            return {"kind": metrics_item.get("kind", "unknown"), "psi": float(metrics_item["psi"])}

        # Extremely conservative heuristic:
        kind = str(metrics_item.get("kind", "unknown")).lower()
        if kind == "hash":
            # Hash shares normally contribute via H(u), not ψ; keep ψ≈0
            return {"kind": "hash", "psi": 0.0}
        if kind == "ai":
            # Scale by traps*QoS; tiny coefficient to avoid over-crediting without policy
            units = float(metrics_item.get("ai_units", 0.0))
            traps = float(metrics_item.get("traps_ratio", 0.0))
            qos = float(metrics_item.get("qos", 0.0))
            psi = 0.001 * units * max(0.0, min(1.0, traps)) * max(0.0, min(1.0, qos))
            return {"kind": "ai", "psi": psi}
        if kind == "quantum":
            units = float(metrics_item.get("quantum_units", 0.0))
            traps = float(metrics_item.get("traps_ratio", 0.0))
            qos = float(metrics_item.get("qos", 0.0))
            psi = 0.002 * units * max(0.0, min(1.0, traps)) * max(0.0, min(1.0, qos))
            return {"kind": "quantum", "psi": psi}
        if kind == "storage":
            qos = float(metrics_item.get("qos", 0.0))
            psi = 0.0001 * qos
            return {"kind": "storage", "psi": psi}
        if kind == "vdf":
            sec = float(metrics_item.get("vdf_seconds", 0.0))
            psi = 0.0002 * sec
            return {"kind": "vdf", "psi": psi}

        # Unknown → zero
        return {"kind": kind, "psi": 0.0}

    def score_preview(self, metrics_list: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Compute per-proof ψ_i and Σψ for a batch, enforcing caps if consensus.caps
        is available. Returns a breakdown:

        {
           "items": [{"kind":"ai","psi":1.23,"psi_capped":1.10}, ...],
           "sum_psi": 3.21,
           "sum_psi_capped": 3.05,
           "caps_applied": true|false
        }
        """
        # Map metrics → ψ-inputs expected by the scorer
        psi_inputs: List[Dict[str, Any]] = [self._map_metrics_to_psi_inputs(m) for m in metrics_list]

        # If we have a real scorer, prefer it
        items_out: List[Dict[str, Any]] = []
        sum_psi = 0.0

        if self._scorer is not None and hasattr(self._scorer, "score_batch"):
            try:
                scored = self._scorer.score_batch(psi_inputs)  # type: ignore[attr-defined]
                # Expect items like {"kind": "ai", "psi": float, ...}
                for it in scored:
                    k = it.get("kind")
                    p = float(it.get("psi", 0.0))
                    items_out.append({"kind": k, "psi": p})
                    sum_psi += p
            except Exception as e:  # noqa: BLE001
                log.warning("scorer.score_batch failed; falling back", extra={"err": str(e)})
                self._scorer = None  # downgrade
                items_out, sum_psi = _fallback_sum(psi_inputs)

        elif _score_fn is not None:
            try:
                scored = _score_fn(self.policy, psi_inputs)  # type: ignore[misc]
                for it in scored:
                    k = it.get("kind")
                    p = float(it.get("psi", 0.0))
                    items_out.append({"kind": k, "psi": p})
                    sum_psi += p
            except Exception as e:  # noqa: BLE001
                log.warning("consensus.scorer.score_batch failed; falling back", extra={"err": str(e)})
                items_out, sum_psi = _fallback_sum(psi_inputs)
        else:
            items_out, sum_psi = _fallback_sum(psi_inputs)

        # Apply caps if available
        caps_applied = False
        sum_psi_capped = sum_psi
        if _apply_caps is not None:
            try:
                capped_items = _apply_caps(self.policy, items_out)  # type: ignore[misc]
                # Expect each item to carry "psi_capped"
                new_items: List[Dict[str, Any]] = []
                sum_c = 0.0
                for it in capped_items:
                    base = float(it.get("psi", 0.0))
                    capped = float(it.get("psi_capped", base))
                    new_items.append({"kind": it.get("kind"), "psi": base, "psi_capped": capped})
                    sum_c += capped
                items_out = new_items
                sum_psi_capped = sum_c
                caps_applied = True
            except Exception as e:  # noqa: BLE001
                log.warning("caps application failed; using uncapped values", extra={"err": str(e)})

        return {
            "items": items_out,
            "sum_psi": sum_psi,
            "sum_psi_capped": sum_psi_capped,
            "caps_applied": caps_applied,
        }

    # ------------------------------------------------------------ acceptance S
    def acceptance_check(
        self,
        u_or_mu: float | int,
        metrics_list: Sequence[Dict[str, Any]],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Compute S = H(u) + Σψ (capped) and compare to Θ (theta_micro).
        Returns (accepted: bool, breakdown: dict)

        u_or_mu:
            - If float in (0,1], treated as *u* and converted to µ-nats via H(u)=−ln(u).
            - If int, treated as µ-nats already.
        """
        # Convert H(u) to µ-nats
        h_mu = _u_or_mu_to_micro(u_or_mu)

        # Score batch
        br = self.score_preview(metrics_list)
        sum_psi = float(br["sum_psi_capped"] if br.get("caps_applied") else br["sum_psi"])

        # Convert ψ (nats) → µ-nats if scorer returns in nats; we assume ψ already in nats.
        # If consensus.math.to_micro_nats is available, use it; otherwise multiply by 1e6.
        if _to_micro_nats is not None:
            psi_mu = int(round(_to_micro_nats(sum_psi)))  # type: ignore[misc]
        else:
            psi_mu = int(round(sum_psi * 1_000_000.0))

        S_mu = h_mu + psi_mu
        theta = int(self.theta_micro)
        accepted = S_mu >= theta

        breakdown = {
            "theta_micro": theta,
            "H_u_micro": h_mu,
            "psi_micro": psi_mu,
            "S_micro": S_mu,
            "margin_micro": S_mu - theta,
            "caps_applied": bool(br.get("caps_applied")),
            "items": br.get("items", []),
        }
        return accepted, breakdown


# ---------------------------------------------------------------------------
# Helpers

def _fallback_sum(psi_inputs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float]:
    items_out: List[Dict[str, Any]] = []
    total = 0.0
    for it in psi_inputs:
        k = it.get("kind")
        p = float(it.get("psi", 0.0))
        items_out.append({"kind": k, "psi": p})
        total += p
    return items_out, total


def _u_or_mu_to_micro(u_or_mu: float | int) -> int:
    if isinstance(u_or_mu, int):
        return int(u_or_mu)
    u = float(u_or_mu)
    if not (0.0 < u <= 1.0):
        raise ValueError("u must be in (0,1] when provided as float")
    # Prefer consensus.math if available
    if _to_micro_nats is not None:
        try:
            # −ln(u) in nats → µ-nats
            return int(round(_to_micro_nats(-log(u))))  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            pass
    # Fallback: direct compute in µ-nats
    return int(round((-log(u)) * 1_000_000.0))


__all__ = ["ConsensusViewAdapter", "LiveView"]
