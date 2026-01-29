"""
Typed exceptions for the Animica proofs module.

Design goals
- Structured: machine-readable code + human message + contextual fields.
- Safe: no heavy deps; pure stdlib.
- Composable: wrap lower-level exceptions with preserved causes.
- Stable across processes: to_dict()/from_dict() round-trip.

The specific subtypes exported here are:
  - ProofError (base)
  - AttestationError
  - NullifierReuseError
  - SchemaError
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class ProofErrorCode(str, Enum):
    """Canonical error codes for proofs/ verification & parsing."""

    UNKNOWN = "UNKNOWN"

    # Schema / decoding / shape issues
    SCHEMA = "SCHEMA"  # generic schema failure (JSON-Schema, CDDL, etc.)
    DECODE = "DECODE"  # bytes/CBOR/JSON decode error
    SIZE_LIMIT = "SIZE_LIMIT"  # proof too large / field too large

    # Policy / linkage issues
    POLICY_MISMATCH = "POLICY_MISMATCH"  # policy roots/ids/versions mismatch
    ROOT_MISMATCH = "ROOT_MISMATCH"  # header roots do not bind to proof set

    # Nullifiers
    NULLIFIER_REUSE = "NULLIFIER_REUSE"  # nullifier seen in TTL window

    # Per-proof families (high-level classification)
    ATTESTATION = "ATTESTATION"  # TEE/QPU attestation parse/verify failed
    HASH_SHARE = "HASH_SHARE_INVALID"  # PoW-ish share invalid
    AI_PROOF = "AI_PROOF_INVALID"  # AI proof bundle invalid
    QUANTUM_PROOF = "QUANTUM_PROOF_INVALID"  # Quantum proof/traps invalid
    STORAGE_PROOF = "STORAGE_PROOF_INVALID"  # PoSt heartbeat invalid
    VDF_PROOF = "VDF_PROOF_INVALID"  # VDF verification failed


@dataclass
class ProofError(Exception):
    """
    Base structured error for proofs/.

    Fields:
      code:  stable machine code (ProofErrorCode | str)
      msg:   human-readable summary
      ctx:   small dict of contextual fields (hex strings, heights, ids, etc.)
      cause: optional underlying exception (not serialized by default)
    """

    code: ProofErrorCode | str = ProofErrorCode.UNKNOWN
    msg: str = "proof error"
    ctx: Dict[str, Any] = field(default_factory=dict)
    cause: Optional[BaseException] = None

    def __post_init__(self) -> None:
        # Ensure ctx is json-serializable friendly (primitives only) where possible.
        # We keep it relaxed here; callers should prefer str/hex/int/bool.
        if not isinstance(self.ctx, dict):
            self.ctx = {"_ctx_type_error": str(type(self.ctx)), "repr": repr(self.ctx)}

    # Exception protocol
    def __str__(self) -> str:
        parts = [f"[{self.code}] {self.msg}"]
        if self.ctx:
            parts.append(f"ctx={self.ctx}")
        if self.cause:
            parts.append(f"cause={self.cause!r}")
        return " ".join(parts)

    def with_context(self, **extra: Any) -> "ProofError":
        """Return a shallow copy with merged context."""
        merged = dict(self.ctx)
        merged.update(extra)
        return ProofError(code=self.code, msg=self.msg, ctx=merged, cause=self.cause)

    # Serialization helpers
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": str(self.code),
            "msg": self.msg,
            "ctx": self.ctx,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProofError":
        code_raw = d.get("code", ProofErrorCode.UNKNOWN)
        try:
            code = ProofErrorCode(code_raw)  # type: ignore[arg-type]
        except Exception:
            code = str(code_raw)
        return cls(
            code=code, msg=str(d.get("msg", "proof error")), ctx=dict(d.get("ctx", {}))
        )

    # Convenience factories
    @classmethod
    def wrap(
        cls,
        code: ProofErrorCode | str,
        msg: str,
        *,
        ctx: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> "ProofError":
        return cls(code=code, msg=msg, ctx=dict(ctx or {}), cause=cause)


class SchemaError(ProofError):
    """Schema / decoding validation failure (JSON-Schema, CDDL, CBOR shape, required fields)."""

    def __init__(
        self,
        msg: str = "schema validation failed",
        *,
        path: Optional[str] = None,
        keyword: Optional[str] = None,
        ctx: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        base_ctx: Dict[str, Any] = {}
        if path is not None:
            base_ctx["path"] = path
        if keyword is not None:
            base_ctx["keyword"] = keyword
        if ctx:
            base_ctx.update(ctx)
        super().__init__(code=ProofErrorCode.SCHEMA, msg=msg, ctx=base_ctx, cause=cause)


class AttestationError(ProofError):
    """TEE/QPU attestation parsing or verification failure (chain of trust, measurements, claims)."""

    def __init__(
        self,
        msg: str = "attestation verification failed",
        *,
        vendor: Optional[str] = None,
        reason: Optional[str] = None,
        ctx: Optional[Mapping[str, Any]] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        base_ctx: Dict[str, Any] = {}
        if vendor is not None:
            base_ctx["vendor"] = vendor
        if reason is not None:
            base_ctx["reason"] = reason
        if ctx:
            base_ctx.update(ctx)
        super().__init__(
            code=ProofErrorCode.ATTESTATION, msg=msg, ctx=base_ctx, cause=cause
        )


class NullifierReuseError(ProofError):
    """Raised when a proof's nullifier has been seen within the active TTL window."""

    def __init__(
        self,
        nullifier_hex: str,
        *,
        first_height: Optional[int] = None,
        attempted_height: Optional[int] = None,
        ttl_blocks: Optional[int] = None,
        ctx: Optional[Mapping[str, Any]] = None,
    ) -> None:
        base_ctx: Dict[str, Any] = {"nullifier": nullifier_hex}
        if first_height is not None:
            base_ctx["first_height"] = int(first_height)
        if attempted_height is not None:
            base_ctx["attempted_height"] = int(attempted_height)
        if ttl_blocks is not None:
            base_ctx["ttl_blocks"] = int(ttl_blocks)
        if ctx:
            base_ctx.update(ctx)
        super().__init__(
            code=ProofErrorCode.NULLIFIER_REUSE,
            msg="nullifier already used within TTL window",
            ctx=base_ctx,
        )


# Handy guard helpers ---------------------------------------------------------


def ensure(
    condition: bool, *, code: ProofErrorCode | str, msg: str, **ctx: Any
) -> None:
    """
    Raise ProofError(code,msg,ctx) if condition is False.
    Keeps call sites concise for validation predicates.
    """
    if not condition:
        raise ProofError(code=code, msg=msg, ctx=ctx)


def schema_guard(
    ok: bool,
    *,
    msg: str,
    path: Optional[str] = None,
    keyword: Optional[str] = None,
    **ctx: Any,
) -> None:
    """
    Raise SchemaError if ok is False. Useful for inline shape checks where a full validator
    is overkill or to augment third-party validator messages with a precise path/keyword.
    """
    if not ok:
        raise SchemaError(msg=msg, path=path, keyword=keyword, ctx=ctx)


def rethrow_as(code: ProofErrorCode | str, *, msg: str, **ctx: Any):
    """
    Context-manager to convert arbitrary exceptions into a typed ProofError
    with an attached cause, preserving the original traceback as __cause__.
      with rethrow_as(ProofErrorCode.DECODE, msg="bad CBOR", where="proofs/cbor"):
          ... code that may raise ...
    """

    class _Ctx:
        def __enter__(self) -> None:
            return None  # type: ignore[return-value]

        def __exit__(self, exc_type, exc, tb) -> bool:
            if exc is None:
                return False
            raise ProofError.wrap(code, msg, ctx=ctx, cause=exc) from exc

    return _Ctx()


__all__ = [
    "ProofError",
    "ProofErrorCode",
    "SchemaError",
    "AttestationError",
    "NullifierReuseError",
    "ensure",
    "schema_guard",
    "rethrow_as",
]
