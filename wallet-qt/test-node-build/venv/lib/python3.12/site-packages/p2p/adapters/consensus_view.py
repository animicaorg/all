"""
p2p.adapters.consensus_view
===========================

A *lightweight* consensus adapter used by P2P header sync to quickly filter
obviously-bad headers *before* invoking the heavy validator. It performs:

- Chain-id check against local params
- Policy-root equality checks (PoIES & PQ alg-policy), if present
- Θ (theta) sanity across consecutive headers with conservative clamps

This module intentionally does **not**:
- recompute Σψ or acceptance,
- verify proofs,
- touch nullifiers,
- or read/write any persistent state.

Keep imports cheap; this runs on hot paths in header sync.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional, Tuple

from core.types.header import Header
from core.types.params import ChainParams

# --------------------------
# Helpers
# --------------------------


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first present attribute/key among names, else default."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
        if isinstance(obj, dict) and n in obj:
            return obj[n]
    return default


def _require_bytes_equal(name: str, got: bytes | None, want: bytes | None) -> None:
    if want is None:
        return
    if not isinstance(got, (bytes, bytearray)) or len(got) == 0:
        raise ValueError(f"{name} missing or not bytes")
    if bytes(got) != bytes(want):
        raise ValueError(f"{name} mismatch")


def _params_to_dict(params: ChainParams) -> Dict[str, Any]:
    if is_dataclass(params):
        return asdict(params)  # type: ignore[no-any-return]
    if isinstance(params, dict):
        return dict(params)
    # Best-effort: fall back to attribute scraping
    out: Dict[str, Any] = {}
    for k in (
        "chainId",
        "theta0",
        "theta_initial",
        "theta_min",
        "theta_max",
        "theta_max_step_ratio",
        "retarget_max_step_ratio",
        "target_block_time_secs",
    ):
        v = getattr(params, k, None)
        if v is not None:
            out[k] = v
    return out


def _theta_bounds(prev_theta: int, params: Dict[str, Any]) -> Tuple[int, int]:
    """
    Compute conservative allowed bounds for next Θ given previous Θ and config.

    We intentionally avoid importing the full retarget math here; instead we use
    a symmetric fractional clamp around the previous value and intersect with
    absolute min/max if configured.

    Recognized keys from params (all optional; sane defaults used if absent):
      - theta_min: absolute lower bound (default 1000 µ-nats)
      - theta_max: absolute upper bound (default 1_000_000 µ-nats)
      - theta_max_step_ratio / retarget_max_step_ratio: e.g. 0.25 = ±25% per block
    """
    theta_min = int(params.get("theta_min", 1_000))
    theta_max = int(params.get("theta_max", 1_000_000))
    step_ratio = float(
        params.get("theta_max_step_ratio", params.get("retarget_max_step_ratio", 0.25))
    )
    if step_ratio <= 0 or step_ratio > 1:
        step_ratio = 0.25
    lo = int(prev_theta * (1.0 - step_ratio))
    hi = int(prev_theta * (1.0 + step_ratio))
    lo = max(lo, theta_min)
    hi = min(hi, theta_max)
    if lo > hi:
        lo, hi = hi, hi  # collapse to a single point to be safe
    return lo, hi


def _has_timestamp(h: Header) -> bool:
    return hasattr(h, "timestamp") and isinstance(getattr(h, "timestamp"), int)


# --------------------------
# Public adapter
# --------------------------


class ConsensusView:
    """
    Cheap consensus checks for header sync.

    Parameters
    ----------
    params : ChainParams
        Local chain parameters (only a few fields are read here).
    poies_policy_root : bytes | None
        Expected PoIES policy Merkle root. If None, the check is skipped.
    alg_policy_root : bytes | None
        Expected PQ alg-policy Merkle root. If None, the check is skipped.
    """

    def __init__(
        self,
        params: ChainParams,
        poies_policy_root: bytes | None = None,
        alg_policy_root: bytes | None = None,
    ) -> None:
        self.params = params
        self.params_dict = _params_to_dict(params)
        self.expected_chain_id = int(
            _get(self.params_dict, "chainId", default=0)
        ) or int(_get(self.params, "chainId", default=0))
        self.poies_policy_root = poies_policy_root
        self.alg_policy_root = alg_policy_root

    # -------- policy roots --------

    def _check_policy_roots(self, h: Header) -> None:
        # Accept multiple canonical field spellings
        poies_got = _get(h, "poiesPolicyRoot", "poies_policy_root", "poies_root")
        alg_got = _get(
            h, "algPolicyRoot", "alg_policy_root", "pq_alg_policy_root", "alg_root"
        )
        _require_bytes_equal("poiesPolicyRoot", poies_got, self.poies_policy_root)
        _require_bytes_equal("algPolicyRoot", alg_got, self.alg_policy_root)

    # -------- theta schedule --------

    def _check_theta(self, prev: Optional[Header], h: Header) -> None:
        theta_now = int(_get(h, "theta", default=0))
        if theta_now <= 0:
            raise ValueError("Θ must be > 0")

        # Genesis: nothing to compare against
        height = int(_get(h, "height", default=0))
        if prev is None or height == 0:
            # If params define an explicit initial Θ, optionally check it
            theta0 = _get(self.params_dict, "theta0", "theta_initial")
            if theta0 is not None and int(theta0) > 0 and int(theta0) != theta_now:
                # Allow small bootstrapping differences (±10%) to tolerate config drift in devnets
                lo, hi = int(int(theta0) * 0.9), int(int(theta0) * 1.1)
                if not (lo <= theta_now <= hi):
                    raise ValueError("Θ at genesis outside tolerance")
            return

        prev_theta = int(_get(prev, "theta", default=theta_now))
        lo, hi = _theta_bounds(prev_theta, self.params_dict)
        if not (lo <= theta_now <= hi):
            raise ValueError(
                f"Θ step out of bounds: got {theta_now}, expected in [{lo}, {hi}]"
            )

        # Optional timestamp sanity if both headers carry it: ensure forward time
        if _has_timestamp(prev) and _has_timestamp(h):
            if int(h.timestamp) <= int(prev.timestamp):
                raise ValueError("non-monotonic header timestamp")

    # -------- chain id --------

    def _check_chain_id(self, h: Header) -> None:
        cid = int(_get(h, "chainId", default=0))
        if cid <= 0:
            raise ValueError("header.chainId must be > 0")
        if self.expected_chain_id and cid != self.expected_chain_id:
            raise ValueError(
                f"header.chainId mismatch: got {cid}, want {self.expected_chain_id}"
            )

    # -------- public API --------

    def validate_header(self, h: Header, prev: Optional[Header]) -> None:
        """
        Perform cheap checks suitable for P2P sync:

        - chainId equals local params (if configured)
        - policy roots match configured expectations (if provided)
        - Θ is within conservative bounds vs previous Θ
        """
        self._check_chain_id(h)
        self._check_policy_roots(h)
        self._check_theta(prev, h)


__all__ = ["ConsensusView"]
