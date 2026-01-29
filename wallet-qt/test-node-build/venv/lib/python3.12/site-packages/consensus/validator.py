"""
Validator
=========

Recomputes the PoIES acceptance score S = H(u) + Σψ for a candidate block,
enforces policy/roots/nullifiers, and checks S ≥ Θ at the header's height.

This module intentionally depends only on *lightweight* consensus interfaces
and abstracts; heavy cryptography lives in `proofs/` (accessed via a small
VerifierRegistry Protocol). Mapping ProofMetrics → ψ (uncapped) and applying
caps/Γ/fairness is delegated to a `Scorer` dependency (see Protocol below).

Usage (in block import):
------------------------
    outcome = validate_block(
        header=header_view,
        proofs=envelopes,
        policy=policy_snapshot,
        verifiers=verifier_registry,
        scorer=scorer_impl,          # adapter over consensus.scorer + caps
        nullifiers=nullifier_store,  # persistence-backed TTL set
    )
    if not outcome.ok:
        raise ConsensusError(outcome.reason)

    # outcome.normalized_envelopes contain canonical CBOR bodies for hashing
    # outcome.psi_micro, outcome.h_micro, outcome.s_micro give detailed breakdown

Design notes
------------
- **Deterministic:** No clocks, I/O, or randomness.
- **Safe numerics:** Convert to µ-nats integers with clamping; reject NaNs/Infs.
- **Extensible:** New proof kinds can be added without touching this file if the
  registry & scorer understand them.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import (Dict, Iterable, List, Optional, Protocol, Sequence, Tuple,
                    runtime_checkable)

from . import work_registry
from .errors import ConsensusError  # re-raised by callers with context
from .interfaces import (PROOF_TYPE_HASHSHARE, HeaderView, PolicySnapshot,
                         ProofEnvelope, ProofMetrics, VerificationResult,
                         VerifierRegistry)

# Fixed-point scale for µ-nats (micro-nats)
_MICRO: int = 1_000_000


# ---------------------------------------------------------------------------
# Protocols for dependency injection (keep validator decoupled & testable)
# ---------------------------------------------------------------------------


@runtime_checkable
class Scorer(Protocol):
    """
    Maps a batch of (type_id, metrics) → ψ_total in µ-nats and returns a
    human-friendly breakdown (e.g., per-kind contributions, caps applied).

    Implementations are expected to:
      - Convert ProofMetrics to ψ inputs (weights/knobs from PoIES policy)
      - Apply per-proof/per-type caps & Γ cap (consensus.caps/policy)
      - Return a *non-negative* integer ψ_total_micro and a breakdown dict
    """

    def score(
        self,
        *,
        items: Sequence[Tuple[int, ProofMetrics]],
        policy: PolicySnapshot,
    ) -> Tuple[int, Dict[str, float]]:
        """
        Returns:
          (psi_total_micro, breakdown)
        Where:
          - psi_total_micro: int (µ-nats), ≥ 0
          - breakdown: { label -> numeric } (free-form, for logs/telemetry)
        """
        ...


@runtime_checkable
class NullifierStore(Protocol):
    """
    Sliding-window TTL store for proof nullifiers. Backed by persistent KV in
    production; tests can pass a simple in-memory set with the same API.
    """

    def seen(self, nullifier: bytes) -> bool: ...
    def record(self, nullifier: bytes, height: int) -> None: ...


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationOutcome:
    ok: bool
    reason: Optional[str]
    # scores (µ-nats)
    theta_micro: int
    h_micro: int
    psi_micro: int
    s_micro: int
    # indices of proofs that failed pre-checks/verifier, if any
    bad_index: Optional[int]
    bad_stage: Optional[str]  # "duplicate-nullifier" | "verifier" | "score"
    # canonicalized envelopes for block persistence (CBOR maps normalized)
    normalized_envelopes: Sequence[ProofEnvelope]
    # human/debug breakdown from scorer
    breakdown: Dict[str, float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_finite_nonneg(x: float) -> bool:
    return (x == x) and (x >= 0.0) and (x < float("inf"))  # NaN check via x==x


def _ln_clamped(x: float) -> float:
    """Safe ln with floor at 0 for x <= 1 (never yields negative H(u))."""
    if not _is_finite_nonneg(x):
        return 0.0
    if x <= 1.0:
        return 0.0
    return log(x)


def _to_micro_nats(x_nat: float) -> int:
    """Convert natural-log units (nats) to µ-nats with clamping."""
    if not _is_finite_nonneg(x_nat):
        return 0
    v = int(round(x_nat * _MICRO))
    return max(0, v)


def _compute_h_micro_from_hashshares(items: Sequence[Tuple[int, ProofMetrics]]) -> int:
    """
    Compute H(u) in µ-nats from HashShare metrics.

    We interpret `d_ratio` (share_difficulty / target_difficulty). Under the
    classical exponential race model, H(u) ≈ ln(d_ratio). We take the *best*
    (max) among included HashShare proofs and clamp at 0.
    """
    best_ln = 0.0
    for type_id, m in items:
        if type_id != PROOF_TYPE_HASHSHARE:
            continue
        ln_val = _ln_clamped(m.d_ratio)
        if ln_val > best_ln:
            best_ln = ln_val
    return _to_micro_nats(best_ln)


def _chain_and_height(header: HeaderView) -> Tuple[int, int]:
    """Best-effort extraction of (chain_id, height) from header-like objects."""
    cid = getattr(header, "chain_id", None) or getattr(header, "chainId", None) or 0
    height = getattr(header, "height", None) or getattr(header, "number", None) or 0
    return int(cid), int(height)


def _work_type_from_header(header: HeaderView) -> Optional[int]:
    """Extract the declared work discriminator from the header, if present."""
    if hasattr(header, "work_type"):
        wt = getattr(header, "work_type")
        if wt is not None:
            return int(wt)
    if hasattr(header, "workType"):
        try:
            return int(getattr(header, "workType"))
        except Exception:
            return None
    if hasattr(header, "__getitem__"):
        for k in ("work_type", "workType"):
            try:
                v = header[k]  # type: ignore[index]
            except Exception:
                v = None
            if v is not None:
                return int(v)
    return None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def validate_block(
    *,
    header: HeaderView,
    proofs: Sequence[ProofEnvelope],
    policy: PolicySnapshot,
    verifiers: VerifierRegistry,
    scorer: Scorer,
    nullifiers: NullifierStore,
) -> ValidationOutcome:
    """
    End-to-end validation of a block's proof set against header/policy.

    Steps:
      1) Enforce alg-policy root binding (header.policy_alg_root == policy.alg_policy_root)
      2) Enforce nullifier freshness (no duplicates in-window)
      3) Verify each proof deterministically via registry (schema, attest, binds)
      4) Map metrics → ψ via scorer (caps & Γ applied inside scorer)
      5) Compute H(u) from HashShare metrics and S = H + Σψ; check S ≥ Θ
      6) Record nullifiers on success and return normalized envelopes

    All steps are *pure* wrt inputs; only `nullifiers.record` performs mutation
    and is invoked *after* acceptance is known (commit point).
    """
    # (1) Policy root binding
    # Check both PoIES and algorithm policy roots
    if hasattr(policy, "poies_policy_root") and hasattr(header, "poies_policy_root"):
        if header.poies_policy_root != policy.poies_policy_root:
            return ValidationOutcome(
                ok=False,
                reason="poies-policy-root-mismatch",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=None,
                bad_stage="policy",
                normalized_envelopes=(),
                breakdown={
                    "expected": int.from_bytes(policy.poies_policy_root, "big"),
                    "header": int.from_bytes(header.poies_policy_root, "big"),
                },
            )
    
    if header.policy_alg_root != policy.alg_policy_root:
        return ValidationOutcome(
            ok=False,
            reason="alg-policy-root-mismatch",
            theta_micro=header.theta_micro,
            h_micro=0,
            psi_micro=0,
            s_micro=0,
            bad_index=None,
            bad_stage="policy",
            normalized_envelopes=(),
            breakdown={
                "expected": int.from_bytes(policy.alg_policy_root, "big"),
                "header": int.from_bytes(header.policy_alg_root, "big"),
                },
            )

    chain_id, height = _chain_and_height(header)
    adaptive_pow = chain_id == 1 and height >= 1
    work_type = _work_type_from_header(header)
    availability: Optional[work_registry.WorkAvailability] = None

    if adaptive_pow:
        availability = work_registry.discover_available_work_types()
        if work_type is None:
            return ValidationOutcome(
                ok=False,
                reason="work-type-required",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=None,
                bad_stage="work",
                normalized_envelopes=(),
                breakdown={"available": sorted(availability.available)},
            )
        if int(work_type) not in availability.available:
            return ValidationOutcome(
                ok=False,
                reason="work-type-unavailable",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=None,
                bad_stage="work",
                normalized_envelopes=(),
                breakdown={"available": sorted(availability.available)},
            )

    # (2) Nullifier freshness (pre-check)
    seen_any_dup = False
    dup_idx: Optional[int] = None
    local_seen: set[bytes] = set()
    for i, env in enumerate(proofs):
        n = bytes(env.nullifier)
        if (n in local_seen) or nullifiers.seen(n):
            seen_any_dup = True
            dup_idx = i
            break
        local_seen.add(n)

    if seen_any_dup:
        return ValidationOutcome(
            ok=False,
            reason="duplicate-nullifier",
            theta_micro=header.theta_micro,
            h_micro=0,
            psi_micro=0,
            s_micro=0,
            bad_index=dup_idx,
            bad_stage="duplicate-nullifier",
            normalized_envelopes=(),
            breakdown={},
        )

    # (3) Verify each proof
    verified: List[Tuple[int, ProofMetrics, bytes]] = []
    normalized_envelopes: List[ProofEnvelope] = []
    for i, env in enumerate(proofs):
        try:
            res: VerificationResult = verifiers.verify(
                envelope=env, header=header, policy=policy
            )
        except Exception as e:  # Defensive: verifiers must not raise, but guard anyway
            return ValidationOutcome(
                ok=False,
                reason=f"verifier-exception:{type(e).__name__}",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=i,
                bad_stage="verifier",
                normalized_envelopes=(),
                breakdown={},
            )

        if not res.ok:
            return ValidationOutcome(
                ok=False,
                reason=f"proof-invalid:{res.reason or 'unspecified'}",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=i,
                bad_stage="verifier",
                normalized_envelopes=(),
                breakdown={},
            )

        # keep normalized CBOR body for receipts hashing/persistence
        normalized_envelopes.append(
            ProofEnvelope(
                type_id=env.type_id,
                body_cbor=res.normalized_body_cbor,
                nullifier=env.nullifier,
            )
        )
        verified.append(
            (
                env.type_id,
                res.metrics,
            )
        )

    if adaptive_pow:
        assert work_type is not None  # guarded above
        if not any(int(tid) == int(work_type) for tid, _ in verified):
            return ValidationOutcome(
                ok=False,
                reason="work-mismatch",
                theta_micro=header.theta_micro,
                h_micro=0,
                psi_micro=0,
                s_micro=0,
                bad_index=None,
                bad_stage="work",
                normalized_envelopes=tuple(normalized_envelopes),
                breakdown={"declared": int(work_type)},
            )

    # (4) Score Σψ via provided scorer (caps inside)
    try:
        psi_micro, breakdown = scorer.score(items=verified, policy=policy)
    except Exception as e:
        return ValidationOutcome(
            ok=False,
            reason=f"score-error:{type(e).__name__}",
            theta_micro=header.theta_micro,
            h_micro=0,
            psi_micro=0,
            s_micro=0,
            bad_index=None,
            bad_stage="score",
            normalized_envelopes=(),
            breakdown={},
        )

    if psi_micro < 0:
        # Scorer must never return negative ψ
        return ValidationOutcome(
            ok=False,
            reason="score-negative",
            theta_micro=header.theta_micro,
            h_micro=0,
            psi_micro=psi_micro,
            s_micro=psi_micro,
            bad_index=None,
            bad_stage="score",
            normalized_envelopes=(),
            breakdown=breakdown,
        )

    # (5) Compute H(u) from hashshare(s) and compare to Θ
    h_micro = _compute_h_micro_from_hashshares(verified)
    s_micro = h_micro + psi_micro
    theta = max(0, int(header.theta_micro))

    if s_micro < theta:
        # Construct a compact reason with a couple of leading contributors (if present)
        top = {}
        # pick up to 3 largest breakdown entries
        for k in sorted(
            breakdown, key=lambda x: abs(float(breakdown[x])), reverse=True
        )[:3]:
            top[k] = breakdown[k]
        return ValidationOutcome(
            ok=False,
            reason="below-theta",
            theta_micro=theta,
            h_micro=h_micro,
            psi_micro=psi_micro,
            s_micro=s_micro,
            bad_index=None,
            bad_stage="score",
            normalized_envelopes=tuple(normalized_envelopes),
            breakdown=top,
        )

    # (6) Commit: record nullifiers after *acceptance*
    for env in proofs:
        nullifiers.record(bytes(env.nullifier), header.height)

    return ValidationOutcome(
        ok=True,
        reason=None,
        theta_micro=theta,
        h_micro=h_micro,
        psi_micro=psi_micro,
        s_micro=s_micro,
        bad_index=None,
        bad_stage=None,
        normalized_envelopes=tuple(normalized_envelopes),
        breakdown=breakdown,
    )


def validate_header(header, proofs=None, policy=None):
    """
    Simplified validator for tests that checks policy roots and θ threshold.
    
    Computes S = H(u) + Σψ and checks S >= θ to enforce the acceptance threshold.
    This ensures blocks with insufficient proof-of-work are properly rejected.
    
    Returns a simple object with an 'accepted' attribute for test compatibility.
    Raises PolicyError on policy root mismatch.
    """
    from .errors import PolicyError
    
    # Check both PoIES and algorithm policy roots
    if policy is not None:
        # Support both dict and object-style policy
        if isinstance(policy, dict):
            poies_root = policy.get("poies_policy_root")
            alg_root = policy.get("alg_policy_root")
        else:
            poies_root = getattr(policy, "poies_policy_root", None)
            alg_root = getattr(policy, "alg_policy_root", None)
        
        # Support both dict and object-style header
        if hasattr(header, "__getitem__") and not hasattr(header, "poies_policy_root"):
            # dict-like
            header_poies = header.get("poies_policy_root")
            header_alg = header.get("alg_policy_root")
        else:
            # object-like
            header_poies = getattr(header, "poies_policy_root", None)
            header_alg = getattr(header, "alg_policy_root", None)
        
        # Check PoIES policy root
        if poies_root is not None and header_poies is not None:
            if header_poies != poies_root:
                raise PolicyError("poies-policy-root-mismatch")
        
        # Check algorithm policy root
        if alg_root is not None and header_alg is not None:
            if header_alg != alg_root:
                raise PolicyError("alg-policy-root-mismatch")
    
    # Compute S = H(u) + Σψ and check against θ threshold
    # This enforces that the block has sufficient proof-of-work
    if proofs is not None:
        # Import math module for H(u) computation
        from . import math as math_mod
        
        # Get u_draw from header (supports multiple field names)
        u_draw = getattr(header, "u_draw", None)
        if u_draw is None:
            u_draw = getattr(header, "nonce", None)
        if u_draw is None and hasattr(header, "__getitem__"):
            u_draw = header.get("u_draw") or header.get("nonce")
        
        # Compute H(u) - the hash-share contribution in µ-nats
        if u_draw is not None:
            try:
                h_micro = int(math_mod.H(u_draw))
            except Exception:
                h_micro = 0
        else:
            h_micro = 0
        
        # Sum Σψ from all proofs (external service contributions in µ-nats)
        psi_sum = 0
        for p in proofs:
            psi_val = getattr(p, "psi_micro", 0)
            psi_sum += psi_val
        
        # Compute total score S = H(u) + Σψ
        s_micro = h_micro + psi_sum
        
        # Get θ threshold from header
        theta_micro = getattr(header, "theta_micro", 0)
        if theta_micro == 0 and hasattr(header, "__getitem__"):
            theta_micro = header.get("theta_micro", 0)
        
        # Check acceptance: S >= θ
        accepted = s_micro >= theta_micro
    else:
        # No proofs provided, accept (backward compatibility)
        accepted = True
    
    # Simple acceptance result for test compatibility
    class Result:
        def __init__(self, accepted):
            self.accepted = accepted
    
    return Result(accepted=accepted)


__all__ = [
    "Scorer",
    "NullifierStore",
    "ValidationOutcome",
    "validate_block",
    "validate_header",
]
