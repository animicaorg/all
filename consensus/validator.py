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
    if header.policy_alg_root != policy.alg_policy_root:
        return ValidationOutcome(
            ok=False,
            reason="alg-policy-root-mismatch",
            theta_micro=header.theta_micro,
            h_micro=0,
            psi_micro=0,
            s_micro=0,
            bad_index=None,
            bad_stage="score",
            normalized_envelopes=(),
            breakdown={
                "expected": int.from_bytes(policy.alg_policy_root, "big"),
                "header": int.from_bytes(header.policy_alg_root, "big"),
            },
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


__all__ = [
    "Scorer",
    "NullifierStore",
    "ValidationOutcome",
    "validate_block",
]
