"""
Animica • DA • Blob Envelope Validation

Lightweight validation for blob "envelopes" and request metadata before we
accept a blob for commitment/storage. This runs *before* computing the NMT
commitment and writing to disk.

What this module checks:
- Envelope shape and types (namespace, optional mime, optional declared size)
- Namespace range (against constants)
- Content-Length (when known) vs MAX_BLOB_BYTES
- Basic MIME sanity (length & characters)
- Optional declared size must not exceed MAX_BLOB_BYTES (and must be non-negative)

It deliberately avoids heavy dependencies. If jsonschema/cbor validators are
desired, they can be layered on by callers; this module provides a deterministic
core with explicit errors.

Raise `DAError` (or a subclass) on failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from da.constants import MAX_BLOB_BYTES
from da.errors import DAError, NamespaceRangeError

# Prefer to delegate range checks to the namespace helper if available.
try:
    from da.nmt.namespace import \
        assert_namespace_id as _assert_ns  # type: ignore
except Exception:  # pragma: no cover
    _assert_ns = None  # fallback to local check below

# If constants expose explicit namespace bounds, use them; otherwise fallback.
try:
    from da.constants import MAX_NAMESPACE_ID, MIN_NAMESPACE_ID  # type: ignore
except Exception:  # pragma: no cover
    MIN_NAMESPACE_ID, MAX_NAMESPACE_ID = 0, (1 << 32) - 1


# ----------------------------- Data types ----------------------------------


@dataclass(frozen=True)
class ValidEnvelope:
    """
    Canonical, sanitized envelope used by DA ingestion.
    """

    namespace: int
    mime: Optional[str] = None
    declared_size: Optional[int] = (
        None  # what client claims; may be checked against Content-Length
    )


# ----------------------------- Validators ----------------------------------


_ASCII_PRINTABLE = re.compile(r"^[\x20-\x7E]+$")  # space..tilde (no control chars)


def _validate_namespace(ns: Any) -> int:
    if not isinstance(ns, int):
        raise NamespaceRangeError("namespace must be an integer")
    if _assert_ns is not None:
        # Delegate to central checker (may enforce reserved ranges)
        _assert_ns(ns)
        return ns
    # Fallback basic bounds
    if ns < int(MIN_NAMESPACE_ID) or ns > int(MAX_NAMESPACE_ID):
        raise NamespaceRangeError(
            f"namespace out of range [{MIN_NAMESPACE_ID}, {MAX_NAMESPACE_ID}]: {ns}"
        )
    return ns


def _validate_mime(mime: Any) -> Optional[str]:
    if mime is None:
        return None
    if not isinstance(mime, str):
        raise DAError("mime must be a string when provided")
    if len(mime) == 0:
        return None
    if len(mime) > 255:
        raise DAError("mime too long (max 255 characters)")
    if not _ASCII_PRINTABLE.match(mime):
        raise DAError("mime contains non-printable characters")
    return mime


def _validate_declared_size(sz: Any) -> Optional[int]:
    if sz is None:
        return None
    if not isinstance(sz, int):
        raise DAError("size_bytes must be an integer when provided")
    if sz < 0:
        raise DAError("size_bytes must be non-negative")
    if sz > int(MAX_BLOB_BYTES):
        raise DAError(f"size_bytes exceeds MAX_BLOB_BYTES={MAX_BLOB_BYTES}")
    return sz


# ----------------------------- Public API ----------------------------------


def validate_envelope_obj(obj: Mapping[str, Any]) -> ValidEnvelope:
    """
    Validate a Python mapping that represents a client-provided envelope.

    Expected keys:
      - "namespace": int (required)
      - "mime": str (optional)
      - "size_bytes": int (optional; client's declaration)

    Returns a `ValidEnvelope` with sanitized fields.
    """
    if not isinstance(obj, Mapping):
        raise DAError("envelope must be an object/mapping")

    if "namespace" not in obj:
        raise DAError("envelope.namespace is required")

    ns = _validate_namespace(obj.get("namespace"))
    mime = _validate_mime(obj.get("mime"))
    declared_size = _validate_declared_size(obj.get("size_bytes"))
    return ValidEnvelope(namespace=ns, mime=mime, declared_size=declared_size)


def precheck_content_length(content_length: Optional[int]) -> None:
    """
    Validate HTTP Content-Length (if known). Raises on invalid values.
    """
    if content_length is None:
        return
    if not isinstance(content_length, int):
        raise DAError("Content-Length must be an integer when present")
    if content_length < 0:
        raise DAError("Content-Length must be non-negative")
    if content_length > int(MAX_BLOB_BYTES):
        raise DAError(f"Content-Length exceeds MAX_BLOB_BYTES={MAX_BLOB_BYTES}")


def validate_headers(headers: Mapping[str, str]) -> Tuple[Optional[int], Optional[str]]:
    """
    Inspect a case-insensitive header mapping and return (content_length, content_type).

    Performs the same bounds checks as `precheck_content_length`.
    """
    # Normalize keys to lowercase for portability
    lower = {k.lower(): v for k, v in headers.items()}
    cl_raw = lower.get("content-length")
    ct = lower.get("content-type")

    cl: Optional[int] = None
    if cl_raw is not None:
        try:
            cl = int(cl_raw.strip())
        except Exception as e:  # pragma: no cover
            raise DAError(f"invalid Content-Length: {cl_raw!r}") from e
        precheck_content_length(cl)

    # Light sanity on Content-Type if present
    if ct is not None:
        if len(ct) > 255:
            raise DAError("Content-Type header too long")
        if not _ASCII_PRINTABLE.match(ct):
            raise DAError("Content-Type contains non-printable characters")

    return cl, ct


def cross_check_sizes(envelope: ValidEnvelope, content_length: Optional[int]) -> None:
    """
    If both declared_size (from envelope) and Content-Length are present, ensure they agree.
    """
    if envelope.declared_size is None or content_length is None:
        return
    if int(envelope.declared_size) != int(content_length):
        raise DAError(
            f"declared size ({envelope.declared_size}) does not match Content-Length ({content_length})"
        )


def ensure_accept_ok(
    *,
    envelope_obj: Mapping[str, Any],
    headers: Optional[Mapping[str, str]] = None,
) -> ValidEnvelope:
    """
    One-shot helper for REST handlers:

        env = ensure_accept_ok(envelope_obj=request.json(), headers=request.headers)

    Steps:
      1) Validate/normalize headers (Content-Length/Type).
      2) Validate/normalize envelope (namespace/mime/size).
      3) Cross-check sizes when available.

    Returns the sanitized `ValidEnvelope`.
    """
    content_length: Optional[int] = None
    if headers is not None:
        content_length, _ = validate_headers(headers)

    env = validate_envelope_obj(envelope_obj)
    # Prefer envelope.declared_size when present, but enforce bounds for either path
    precheck_content_length(content_length)
    if env.declared_size is not None:
        precheck_content_length(env.declared_size)

    cross_check_sizes(env, content_length)
    return env


__all__ = [
    "ValidEnvelope",
    "validate_envelope_obj",
    "validate_headers",
    "precheck_content_length",
    "cross_check_sizes",
    "ensure_accept_ok",
]
