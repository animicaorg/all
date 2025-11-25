from __future__ import annotations
"""
ProofClaim: link an on-chain Proof* record to an AICF JobRecord.

This type provides the minimal, canonical linkage fields that let the
AICF queue/settlement layer recognize that a specific off-chain job
(task) has been proven on-chain and is therefore eligible for settlement.

Key fields
----------
- task_id:
    Deterministic identifier derived at enqueue time
    (see capabilities.jobs.id). Lowercase hex, no "0x".
- nullifier:
    Domain-separated, single-use identifier produced by the proof system
    to prevent reuse across blocks/windows. Lowercase 64-hex.
- height:
    Block height in which the proof was included (for replay detection
    and to compute time-to-settlement windows).
- provider_id:
    The provider that executed the job and is claiming the proof.
- job_id:
    Optional local queue id that originated the task. Redundant if your
    system keys everything by task_id; present here for convenience.
- proof_digest:
    Optional digest of the proof envelope committed on-chain. When
    present it must be a 64-char lowercase hex (sha3-256).
- work_units:
    Optional normalized work score attributed to this claim (post
    verification). Used by payout accounting; 0 if not applicable.
- included_at:
    Optional UNIX seconds when the containing block was observed.

Notes
-----
This module is intentionally agnostic about the exact on-chain Proof*
encoding; it only captures the binding needed by AICF. Higher layers
perform attestation normalization and verification, then populate this
structure for settlement.
"""


from dataclasses import dataclass, asdict
from typing import Dict,Mapping,Optional

from . import (
    ProviderId,
    JobId,
    TaskId,
    BlockHeight,
    Timestamp,
    TokenAmount,  # may be used by callers; kept for consistency
    is_hex_id,
)

ProofKind = Literal["ai", "quantum"]


@dataclass
class ProofClaim:
    """
    Canonical linkage between an on-chain proof and an AICF job/task.
    """
    kind: ProofKind
    task_id: TaskId
    nullifier: str
    height: BlockHeight
    provider_id: ProviderId
    job_id: Optional[JobId] = None
    proof_digest: Optional[str] = None
    work_units: int = 0
    included_at: Optional[Timestamp] = None

    def validate(self) -> None:
        if self.kind not in ("ai", "quantum"):
            raise ValueError("ProofClaim.kind must be 'ai' or 'quantum'")

        if not is_hex_id(self.task_id):
            raise ValueError("task_id must be lowercase hex (no 0x)")
        if not is_hex_id(self.provider_id):
            raise ValueError("provider_id must be lowercase hex (no 0x)")

        if self.job_id is not None and not is_hex_id(self.job_id):
            raise ValueError("job_id must be lowercase hex (no 0x) when set")

        _require_hex_digest(self.nullifier, "nullifier")

        if self.proof_digest is not None:
            _require_hex_digest(self.proof_digest, "proof_digest")

        if int(self.height) < 0:
            raise ValueError("height must be >= 0")

        if self.work_units < 0:
            raise ValueError("work_units must be >= 0")

        if self.included_at is not None and int(self.included_at) <= 0:
            raise ValueError("included_at must be > 0 when set")

    def to_dict(self) -> Dict[str, object]:
        self.validate()
        d = asdict(self)
        d["height"] = int(self.height)
        if self.included_at is not None:
            d["included_at"] = int(self.included_at)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, object]) -> "ProofClaim":
        claim = ProofClaim(
            kind=str(d.get("kind", "ai")),  # type: ignore[assignment]
            task_id=TaskId(str(d.get("task_id", ""))),
            nullifier=str(d.get("nullifier", "")),
            height=BlockHeight(int(d.get("height", 0))),
            provider_id=ProviderId(str(d.get("provider_id", ""))),
            job_id=(JobId(str(d["job_id"])) if d.get("job_id") else None),
            proof_digest=(str(d["proof_digest"]) if d.get("proof_digest") else None),
            work_units=int(d.get("work_units", 0)),
            included_at=(Timestamp(int(d["included_at"])) if d.get("included_at") else None),
        )
        claim.validate()
        return claim


# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────

def _require_hex_digest(v: str, label: str) -> None:
    if not isinstance(v, str) or len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
        raise ValueError(f"{label} must be 64-char lowercase hex (sha3-256)")
