"""
Verifier registry for Useful Work Proofs.

Manages pluggable verifiers for different proof schemes.
"""

from __future__ import annotations

from typing import Dict, Callable, Optional, List
import time
import logging

from .types import UsefulWorkProof, VerifyResult, VerifyStatus, ShareContext
from .policy import get_policy

log = logging.getLogger(__name__)

# Verifier signature
VerifierFunc = Callable[[UsefulWorkProof, ShareContext], VerifyResult]

# Registry of verifiers
_VERIFIERS: Dict[str, VerifierFunc] = {}


def register_verifier(scheme_id: str, verifier: VerifierFunc) -> None:
    """
    Register a verifier for a proof scheme.
    
    Args:
        scheme_id: Unique identifier for the scheme
        verifier: Verification function
    """
    _VERIFIERS[scheme_id] = verifier
    log.debug(f"Registered verifier for scheme: {scheme_id}")


def get_verifier(scheme_id: str) -> Optional[VerifierFunc]:
    """Get the verifier for a scheme."""
    return _VERIFIERS.get(scheme_id)


def list_schemes() -> List[str]:
    """List all registered schemes."""
    return list(_VERIFIERS.keys())


def verify_proof(
    proof: UsefulWorkProof,
    context: ShareContext,
    *,
    time_budget_ms: Optional[int] = None,
) -> VerifyResult:
    """
    Verify a useful work proof with policy enforcement and time budgeting.
    
    Args:
        proof: The proof to verify
        context: Share context
        time_budget_ms: Optional time budget override
        
    Returns:
        Verification result
    """
    policy = get_policy()
    
    # Check if UWP is enabled
    if not policy.enabled:
        return VerifyResult(
            status=VerifyStatus.SCHEME_DISABLED,
            reason="UWP system is disabled by policy",
        )
    
    # Check if scheme is enabled
    if not policy.is_scheme_enabled(proof.scheme_id):
        return VerifyResult(
            status=VerifyStatus.SCHEME_DISABLED,
            reason=f"Scheme {proof.scheme_id} is disabled by policy",
        )
    
    # Get verifier
    verifier = get_verifier(proof.scheme_id)
    if verifier is None:
        return VerifyResult(
            status=VerifyStatus.SCHEME_UNSUPPORTED,
            reason=f"No verifier registered for scheme: {proof.scheme_id}",
        )
    
    # Get time budget
    scheme_policy = policy.get_scheme_policy(proof.scheme_id)
    if time_budget_ms is None:
        time_budget_ms = scheme_policy.max_verify_ms if scheme_policy else 500
    
    # Verify with timeout
    start_time = time.time()
    
    try:
        result = verifier(proof, context)
        
        # Check time budget
        elapsed_ms = (time.time() - start_time) * 1000
        if elapsed_ms > time_budget_ms:
            log.warning(f"Verification exceeded time budget: {elapsed_ms:.1f}ms > {time_budget_ms}ms")
            return VerifyResult(
                status=VerifyStatus.SKIPPED_BUDGET,
                reason=f"Verification took {elapsed_ms:.1f}ms (budget: {time_budget_ms}ms)",
            )
        
        return result
        
    except Exception as e:
        log.error(f"Verifier error for scheme {proof.scheme_id}: {e}", exc_info=True)
        return VerifyResult(
            status=VerifyStatus.VERIFIER_ERROR,
            reason=f"Internal verifier error: {str(e)[:100]}",
        )


__all__ = [
    "VerifierFunc",
    "register_verifier",
    "get_verifier",
    "list_schemes",
    "verify_proof",
]
