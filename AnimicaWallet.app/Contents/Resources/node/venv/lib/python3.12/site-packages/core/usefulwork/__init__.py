"""
Useful Work Proof (UWP) subsystem for Animica.

This module implements Phase 2 of the mining enhancement: allowing miners to attach
verifiable proofs of useful work (AI training, evaluation, compute contributions) to
their mining shares without requiring full ML recomputation by validators.

Key components:
- UsefulWorkProof: CBOR envelope for proof data
- Verifier registry: Pluggable verifiers for different proof schemes
- Policy gating: Enable/disable schemes, caps, bonuses
- Bonus credit accounting: Reward miners for verified useful work

Design constraints:
- NO full ML training/inference by validators
- Bounded verification (time + memory)
- Proofs are optional (mining works without them)
- Policy-gated acceptance
- No filesystem writes in RPC path
- Graceful degradation if subsystem fails
"""

from .types import (
    UsefulWorkProof,
    EnaEvalMicroProof,
    ComputeReceiptProof,
    VerifyResult,
    VerifyStatus,
    ShareContext,
    Hash,
)
from .registry import (
    register_verifier,
    get_verifier,
    verify_proof,
    list_schemes,
)
from .policy import (
    UWPPolicy,
    SchemePolicy,
    load_policy,
    get_policy,
    is_scheme_enabled,
)
from .cbor_codec import (
    encode_proof,
    decode_proof,
    encode_proof_to_hex,
    decode_proof_from_hex,
    UWPDecodeError,
)

# Auto-register built-in verifiers
from . import verifiers

__all__ = [
    "UsefulWorkProof",
    "EnaEvalMicroProof",
    "ComputeReceiptProof",
    "VerifyResult",
    "VerifyStatus",
    "ShareContext",
    "Hash",
    "register_verifier",
    "get_verifier",
    "verify_proof",
    "list_schemes",
    "UWPPolicy",
    "SchemePolicy",
    "load_policy",
    "get_policy",
    "is_scheme_enabled",
    "encode_proof",
    "decode_proof",
    "encode_proof_to_hex",
    "decode_proof_from_hex",
    "UWPDecodeError",
]
