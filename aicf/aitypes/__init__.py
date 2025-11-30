from __future__ import annotations

from typing import Literal

from aicf.queue.jobkind import JobKind

"""
Lightweight shared types for the AI Compute Fund (AICF).

These are intentionally minimal so they can be imported from both runtime
code and type-checkers without importing heavier submodules.

Conventions
-----------
- All *Id types are hex-encoded, lowercase strings (without 0x).
- Monetary values are represented in the smallest unit (nano-tokens) as ints.
- Timestamps are UNIX seconds.
"""


from typing import NewType

# ────────────────────────────────────────────────────────────────────────────────
# Identifiers
# ────────────────────────────────────────────────────────────────────────────────

JobId = NewType("JobId", str)  # hex lowercase, domain-separated derivation
ProviderId = NewType("ProviderId", str)  # hex lowercase, registry key / address hash
LeaseId = NewType("LeaseId", str)  # hex lowercase, assignment lease identifier
ProofId = NewType("ProofId", str)  # hex lowercase, provider proof submission id
PayoutId = NewType("PayoutId", str)  # hex lowercase, settlement record id
SlashId = NewType("SlashId", str)  # hex lowercase, slash event id

# ────────────────────────────────────────────────────────────────────────────────
# Core literals / enums
# ────────────────────────────────────────────────────────────────────────────────
AssignmentStatus = Literal[
    "assigned",  # lease issued and active
    "renewed",  # lease extended
    "lost",  # lease lost (provider offline / QoS breach)
    "expired",  # lease expired naturally
]

ProofResult = Literal[
    "accepted",  # proof verified and eligible for payout
    "rejected",  # proof verified but failed policy/quality
    "invalid",  # proof/attestation invalid
]

PayoutStatus = Literal[
    "queued",  # pending batch/epoch settlement
    "settled",  # on-chain/off-ledger settlement complete
    "failed",  # settlement failed (temporary)
]

SlashingReason = Literal[
    "traps_fail",  # failed traps / integrity checks
    "qos_fail",  # latency/throughput SLA violation
    "availability_fail",  # result or proof availability failure
    "misbehavior",  # double-sign / equivocation / fraud
    "other",
]

# ────────────────────────────────────────────────────────────────────────────────
# Primitive numeric types
# ────────────────────────────────────────────────────────────────────────────────

TokenAmount = NewType("TokenAmount", int)  # nano-tokens (smallest unit)
Timestamp = NewType("Timestamp", int)  # UNIX seconds
BlockHeight = NewType("BlockHeight", int)  # chain height

# ────────────────────────────────────────────────────────────────────────────────
# Small helpers
# ────────────────────────────────────────────────────────────────────────────────


def is_hex_id(s: str) -> bool:
    """Return True iff `s` is a non-empty lowercase hex string (no 0x prefix)."""
    if not s or s.startswith("0x"):
        return False
    for ch in s:
        if ch not in "0123456789abcdef":
            return False
    return True


__all__ = [
    # ids
    "JobId",
    "ProviderId",
    "LeaseId",
    "ProofId",
    "PayoutId",
    "SlashId",
    # literals/enums
    "JobKind",
    "AssignmentStatus",
    "ProofResult",
    "PayoutStatus",
    "SlashingReason",
    # primitives
    "TokenAmount",
    "Timestamp",
    "BlockHeight",
    # helpers
    "is_hex_id",
]
