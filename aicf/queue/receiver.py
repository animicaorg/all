from __future__ import annotations

"""
aicf.queue.receiver
===================

Ingest completed job outputs (content digests) and proof references from providers.

Responsibilities
----------------
- Verify the job exists and is currently leased to the submitting provider.
- Check lease freshness and basic integrity of the submitted payload.
- Record the output digest and any proof references.
- Transition job status to COMPLETED (idempotent if already completed with the same digest).
- Emit metrics.

This module purposefully stays storage-agnostic via a minimal protocol that the
queue backend must implement.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence,
                    Tuple)

log = logging.getLogger(__name__)

# ────────────────────────────── Optional metrics ──────────────────────────────

try:
    from aicf.metrics import \
        COUNTER_COMPLETIONS_ACCEPTED as _C_OK  # type: ignore
    from aicf.metrics import COUNTER_COMPLETIONS_REJECTED as _C_REJ
    from aicf.metrics import \
        COUNTER_PROOFS_REFERENCED as _C_PROOFS  # may not exist; handled below
    from aicf.metrics import HISTOGRAM_COMPLETION_LATENCY_SECONDS as _H_LAT
except Exception:  # pragma: no cover - metrics are optional

    class _Noop:
        def inc(self, *_: float, **__: Any) -> None: ...
        def observe(self, *_: float, **__: Any) -> None: ...

    _C_OK = _Noop()
    _C_REJ = _Noop()
    _H_LAT = _Noop()

    # Provide a best-effort proofs counter if the metrics module doesn't export it.
    _C_PROOFS = _Noop()

# ─────────────────────────────── Errors & types ───────────────────────────────

try:
    from aicf.errors import (AICFError, JobExpired, LeaseLost,  # type: ignore
                             RegistryError)
except Exception:

    class AICFError(RuntimeError): ...

    class LeaseLost(AICFError): ...

    class JobExpired(AICFError): ...

    class RegistryError(AICFError): ...


try:
    from aicf.aitypes.job import JobStatus  # type: ignore
except Exception:

    class JobStatus:
        QUEUED = "QUEUED"
        PENDING = "PENDING"
        LEASED = "LEASED"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"
        TOMBSTONED = "TOMBSTONED"
        EXPIRED = "EXPIRED"


# ─────────────────────────────── Storage protocol ─────────────────────────────


class ReceiverStorage(Protocol):
    """
    Minimal interface the receiver requires from queue storage.

    All mutating operations should be atomic (e.g., executed within a DB
    transaction). Timestamps must be stored in UTC.
    """

    def get_job(self, job_id: str) -> Optional[Mapping[str, Any]]:
        """
        Return a job record with at least:
            {
              "job_id": str,
              "status": str,
              "provider_id": Optional[str],   # current lease holder, if leased
              "enqueued_at": datetime,
              "leased_at": Optional[datetime],
              "lease_expires_at": Optional[datetime],
              "output_digest": Optional[str],
              "completed_at": Optional[datetime],
            }
        """

    def get_active_lease(self, job_id: str) -> Optional[Mapping[str, Any]]:
        """
        Return active lease for the job, if any, with at least:
            {"job_id": str, "provider_id": str, "lease_expires_at": datetime}
        """

    def mark_completed(
        self,
        job_id: str,
        provider_id: str,
        output_digest: str,
        proof_refs: Sequence[Mapping[str, Any]],
        completed_at: datetime,
        meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """
        Persist completion: set status=COMPLETED, output_digest, completed_at,
        attach proof_refs (JSON), and release/close the lease.
        """

    def append_event(
        self,
        job_id: str,
        event_type: str,
        ts: datetime,
        details: Mapping[str, Any],
    ) -> None:
        """Optional: record an internal audit/event entry."""


# ─────────────────────────────── Registry protocol ────────────────────────────


class ProviderRegistry(Protocol):
    """Optional registry checks for provider status/identity."""

    def is_allowed(self, provider_id: str) -> bool: ...
    def is_jailed(self, provider_id: str) -> bool: ...


# ─────────────────────────────── Input dataclass ──────────────────────────────


_HEX_RE = re.compile(r"^(0x)?[0-9a-fA-F]+$")


@dataclass(frozen=True)
class CompletionPayload:
    """
    Provider-submitted completion.

    Fields:
      - job_id: target job
      - provider_id: submitting provider (must hold the active lease)
      - output_digest: hex digest (sha3-256/512, blake3-256, etc.), 32 or 64 bytes
      - proof_refs: optional structured references to DA commits / on-chain proofs / attestation bundle digests
      - meta: optional small metadata map (runtime, model info, qos stats)
    """

    job_id: str
    provider_id: str
    output_digest: str
    proof_refs: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    meta: Mapping[str, Any] = field(default_factory=dict)


# ─────────────────────────────── Receiver engine ──────────────────────────────


class CompletionReceiver:
    """
    Validates and records job completions from providers.

    Typical usage:
        rx = CompletionReceiver(storage, registry)
        ack = rx.accept(CompletionPayload(...))
    """

    def __init__(
        self, storage: ReceiverStorage, registry: Optional[ProviderRegistry] = None
    ) -> None:
        self.storage = storage
        self.registry = registry

    # Public API -------------------------------------------------------------

    def accept(
        self, payload: CompletionPayload, *, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Validate the completion and mark the job as COMPLETED. Returns an
        acknowledgement dict. Raises AICFError subclasses on invalid state.
        """
        ts = _utc(now)

        # 1) Basic input validation
        digest_norm = _normalize_hex(payload.output_digest)
        _validate_digest(digest_norm)
        proofs_norm = _sanitize_proof_refs(payload.proof_refs)

        # 2) Provider registry checks (optional)
        if self.registry:
            if not self.registry.is_allowed(payload.provider_id):
                _C_REJ.inc(1)  # type: ignore
                raise RegistryError(
                    f"provider {payload.provider_id} is not allowlisted"
                )
            if self.registry.is_jailed(payload.provider_id):
                _C_REJ.inc(1)  # type: ignore
                raise RegistryError(f"provider {payload.provider_id} is jailed")

        # 3) Job & lease checks
        job = self.storage.get_job(payload.job_id)
        if not job:
            _C_REJ.inc(1)  # type: ignore
            raise AICFError(f"job {payload.job_id} not found")

        status = str(job.get("status", "")).upper()
        if status == JobStatus.COMPLETED:
            # Idempotent acceptance if digests match
            prev = _normalize_hex(str(job.get("output_digest") or ""))
            if prev and prev == digest_norm:
                _C_OK.inc(1)  # type: ignore
                return {
                    "ok": True,
                    "job_id": payload.job_id,
                    "idempotent": True,
                    "output_digest": "0x" + prev,
                    "completed_at": _iso(job.get("completed_at")),
                }
            _C_REJ.inc(1)  # type: ignore
            raise AICFError(
                f"job {payload.job_id} already completed with a different digest"
            )

        if status not in (JobStatus.LEASED, JobStatus.PENDING):
            _C_REJ.inc(1)  # type: ignore
            raise AICFError(
                f"job {payload.job_id} not in a completable state: {status}"
            )

        lease = self.storage.get_active_lease(payload.job_id)
        if not lease:
            _C_REJ.inc(1)  # type: ignore
            raise LeaseLost(f"no active lease for job {payload.job_id}")

        lease_provider = str(lease.get("provider_id") or "")
        if lease_provider != payload.provider_id:
            _C_REJ.inc(1)  # type: ignore
            raise LeaseLost(
                f"job {payload.job_id} lease held by {lease_provider}, not {payload.provider_id}"
            )

        lease_exp = _dt(lease.get("lease_expires_at"))
        if lease_exp and lease_exp < ts:
            _C_REJ.inc(1)  # type: ignore
            raise JobExpired(
                f"lease expired at {lease_exp.isoformat()} for job {payload.job_id}"
            )

        # 4) Persist completion
        self.storage.mark_completed(
            job_id=payload.job_id,
            provider_id=payload.provider_id,
            output_digest=digest_norm,
            proof_refs=proofs_norm,
            completed_at=ts,
            meta=payload.meta,
        )

        # 5) Event & metrics
        try:
            self.storage.append_event(
                payload.job_id,
                "COMPLETED",
                ts,
                {
                    "provider_id": payload.provider_id,
                    "output_digest": "0x" + digest_norm,
                    "proof_refs_count": len(proofs_norm),
                },
            )
        except Exception:  # pragma: no cover - optional
            pass

        _C_OK.inc(1)  # type: ignore
        if proofs_norm:
            try:
                _C_PROOFS.inc(float(len(proofs_norm)))  # type: ignore
            except Exception:
                pass

        enq_at = _dt(job.get("enqueued_at"))
        if enq_at:
            _H_LAT.observe(max(0.0, (ts - enq_at).total_seconds()))  # type: ignore

        return {
            "ok": True,
            "job_id": payload.job_id,
            "output_digest": "0x" + digest_norm,
            "proof_refs": proofs_norm,
            "completed_at": ts.isoformat(),
        }


# ──────────────────────────────── Helpers ────────────────────────────────────


def _normalize_hex(h: str) -> str:
    if not isinstance(h, str):
        raise AICFError("digest must be a hex string")
    h = h.strip()
    if h.startswith(("0x", "0X")):
        h = h[2:]
    if not _HEX_RE.match("0x" + h):
        raise AICFError("digest must be hex")
    return h.lower()


def _validate_digest(h: str) -> None:
    # Accept common digest sizes: 32 bytes (64 hex) or 64 bytes (128 hex)
    if len(h) not in (64, 128):
        raise AICFError("digest must be 32 or 64 bytes (64/128 hex chars)")


_ALLOWED_PROOF_KINDS = {
    "da_commitment",  # Namespaced DA commitment (e.g., NMT root / blob commitment)
    "onchain_proof",  # reference to chain height+nullifier that will settle
    "attestation",  # TEE/QPU bundle digest
    "vdf_proof",  # if applicable
}


def _sanitize_proof_refs(
    refs: Sequence[Mapping[str, Any]],
) -> Tuple[Mapping[str, Any], ...]:
    out: list[Mapping[str, Any]] = []
    for ref in refs or ():
        kind = str(ref.get("kind") or "").strip()
        if kind not in _ALLOWED_PROOF_KINDS:
            # Ignore unknown kinds rather than fail hard; receiver is permissive.
            log.debug("receiver: ignoring unknown proof kind=%r", kind)
            continue

        # Copy a small, safe projection to avoid unbounded blobs here.
        proj: Dict[str, Any] = {"kind": kind}

        if kind == "da_commitment":
            # expect fields: commitment (hex), namespace (int)
            c = ref.get("commitment")
            if isinstance(c, str):
                proj["commitment"] = "0x" + _normalize_hex(c)
            ns = ref.get("namespace")
            if isinstance(ns, int) and 0 <= ns < (1 << 32):
                proj["namespace"] = ns

        elif kind == "onchain_proof":
            # expect: height (int), nullifier (hex)
            h = ref.get("height")
            if isinstance(h, int) and h >= 0:
                proj["height"] = h
            n = ref.get("nullifier")
            if isinstance(n, str):
                proj["nullifier"] = "0x" + _normalize_hex(n)

        elif kind == "attestation":
            # expect: bundle_digest (hex), provider_cert_hint (str)
            b = ref.get("bundle_digest")
            if isinstance(b, str):
                proj["bundle_digest"] = "0x" + _normalize_hex(b)
            hint = ref.get("provider_cert_hint")
            if isinstance(hint, str) and len(hint) <= 256:
                proj["provider_cert_hint"] = hint

        elif kind == "vdf_proof":
            # expect: proof_digest (hex)
            d = ref.get("proof_digest")
            if isinstance(d, str):
                proj["proof_digest"] = "0x" + _normalize_hex(d)

        out.append(proj)

    return tuple(out)


def _utc(dt: Optional[datetime] = None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt(x: Any) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    try:
        # Safe-ish ISO parser for common cases
        return datetime.fromisoformat(str(x))  # type: ignore[arg-type]
    except Exception:
        return None


def _iso(x: Any) -> Optional[str]:
    d = _dt(x)
    return d.isoformat() if d else None


__all__ = [
    "CompletionPayload",
    "CompletionReceiver",
    "ReceiverStorage",
    "ProviderRegistry",
]
