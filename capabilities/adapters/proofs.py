"""
capabilities.adapters.proofs
===========================

Parse on-chain proof *envelopes* and map them to `ResultRecord` objects
used by the capabilities job/result pipeline.

This adapter is deliberately defensive: it tolerates multiple envelope
shapes/codecs and only fills fields that are known to exist in the
imported `ResultRecord` dataclass (using reflection), so it stays
compatible as upstream types evolve.

Typical use (from a block resolver):

    from capabilities.adapters import proofs as proofs_adapter

    results = []
    for env_bytes in proof_envelopes:   # CBOR-encoded envelopes
        rec = proofs_adapter.result_from_envelope(
            env_bytes,
            height=block_height,
            chain_id=chain_id,
        )
        if rec is not None:
            results.append(rec)

"""

from __future__ import annotations

import binascii
from dataclasses import fields, is_dataclass
from typing import Any, Dict, Optional, Tuple

# ---- Optional imports from proofs/ ----------------------------------------

# We try to use the canonical decoder from proofs.cbor if present.
_DECODERS: Tuple[str, ...] = (
    "decode_envelope",
    "decode_envelope_bytes",
    "decode",  # last-resort name
)

try:  # pragma: no cover - exercised in integration tests
    import proofs.cbor as proofs_cbor  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    proofs_cbor = None  # fallback to cbor2/msgspec below

# A loose dependency on the proofs registry/types for friendly type names.
try:  # pragma: no cover
    from proofs import \
        registry as proofs_registry  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    proofs_registry = None

# ---- Capabilities job/result types ----------------------------------------

from capabilities.jobs.types import JobKind, ResultRecord

__all__ = [
    "decode_envelope_any",
    "classify_job_kind",
    "extract_task_id",
    "result_from_envelope",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _hex_to_bytes_maybe(x: Any) -> Optional[bytes]:
    """
    Accept bytes, 0x-hex str, or None. Return bytes or None.
    """
    if x is None:
        return None
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        if len(s) % 2 == 1:
            s = "0" + s
        try:
            return binascii.unhexlify(s)
        except binascii.Error:
            return None
    return None


def _dataclass_kwargs_safe(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter `data` to keys present on dataclass `cls`. If `cls` is not a
    dataclass, return `data` unchanged.
    """
    if not is_dataclass(cls):
        return data
    allow = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in allow}


# ---------------------------------------------------------------------------
# Envelope decoding
# ---------------------------------------------------------------------------


def decode_envelope_any(obj: Any) -> Optional[Dict[str, Any]]:
    """
    Decode a proof *envelope* from one of:
      - raw CBOR bytes (preferred)
      - already-decoded dict-like with keys ('type_id','body','nullifier')

    Returns a dict with at least: {'type_id': int, 'body': Any, 'nullifier': bytes?}
    or None if decoding fails.
    """
    # If it's already a mapping with the right keys, accept it.
    if isinstance(obj, dict) and "type_id" in obj and "body" in obj:
        # normalize nullifier to bytes if present
        env = dict(obj)
        if "nullifier" in env:
            nb = _hex_to_bytes_maybe(env["nullifier"])
            env["nullifier"] = nb if nb is not None else env["nullifier"]
        return env

    # If we have the proofs.cbor helpers, try them in a few common names.
    if proofs_cbor is not None:
        for name in _DECODERS:
            dec = getattr(proofs_cbor, name, None)
            if callable(dec):
                try:
                    env = dec(obj)  # type: ignore[misc,call-arg]
                    if isinstance(env, dict) and "type_id" in env and "body" in env:
                        # normalize nullifier
                        if "nullifier" in env:
                            nb = _hex_to_bytes_maybe(env["nullifier"])
                            env["nullifier"] = (
                                nb if nb is not None else env["nullifier"]
                            )
                        return env
                except Exception:
                    pass  # try next decoder

    # Fallback: attempt generic CBOR decode
    if isinstance(obj, (bytes, bytearray, memoryview)):
        raw = bytes(obj)
        # Prefer msgspec if available (fast), else cbor2
        try:
            import msgspec  # type: ignore

            dec = msgspec.json.Decoder()  # dummy to ensure import ok
            # We actually want CBOR; msgspec provides msgspec.json/* only,
            # so skip and use cbor2 below if no dedicated CBOR.
            raise ImportError  # force cbor2 path
        except Exception:
            try:
                import cbor2  # type: ignore

                env = cbor2.loads(raw)
                if isinstance(env, dict) and "type_id" in env and "body" in env:
                    if "nullifier" in env:
                        nb = _hex_to_bytes_maybe(env["nullifier"])
                        env["nullifier"] = nb if nb is not None else env["nullifier"]
                    return env
            except Exception:
                return None

    return None


# ---------------------------------------------------------------------------
# Classification / extraction
# ---------------------------------------------------------------------------


def classify_job_kind(env: Dict[str, Any]) -> Optional[JobKind]:
    """
    Map a proof envelope to JobKind, if applicable.
    - AI proofs  -> JobKind.AI
    - Quantum    -> JobKind.QUANTUM
    - Other kinds (HashShare/Storage/VDF) return None (not capabilities jobs).
    """
    type_id = env.get("type_id")
    body = env.get("body", {}) or {}

    # If the registry can tell us a friendly name, use it.
    friendly = None
    if proofs_registry is not None:
        try:  # pragma: no cover
            friendly = proofs_registry.name_for_type_id(type_id)  # type: ignore[attr-defined]
        except Exception:
            friendly = None

    # Heuristics by friendly name or by body keys
    name = str(friendly or "").lower()
    if "ai" in name:
        return JobKind.AI
    if "quantum" in name:
        return JobKind.QUANTUM

    # Fallback: key-based hints
    keys = {str(k).lower() for k in (body.keys() if isinstance(body, dict) else [])}
    if {"tee", "qos", "traps"}.intersection(keys) or "ai_metrics" in keys:
        return JobKind.AI
    if {"trap", "circuit", "qpu", "shots"}.intersection(
        keys
    ) or "quantum_metrics" in keys:
        return JobKind.QUANTUM

    return None


def extract_task_id(env: Dict[str, Any]) -> Optional[bytes]:
    """
    Best-effort extraction of the deterministic job/task id from the proof.
    Looks for the following (first hit wins), normalizing hex → bytes:
      - env['body']['task_id']
      - env['body']['job_id']
      - env['body']['request']['task_id']
      - env['task_id'] (top-level, non-standard)
    Returns None if not found.
    """
    body = env.get("body", {}) or {}
    candidates = [
        (body, "task_id"),
        (body, "job_id"),
        (body.get("request", {}) if isinstance(body, dict) else {}, "task_id"),
        (env, "task_id"),
    ]
    for scope, key in candidates:
        try:
            val = scope.get(key) if isinstance(scope, dict) else None
        except Exception:
            val = None
        b = _hex_to_bytes_maybe(val)
        if b:
            return b
    return None


def _extract_output_digest(env: Dict[str, Any]) -> Optional[bytes]:
    """
    Find an output/result digest from common field names, normalize hex → bytes.
    """
    body = env.get("body", {}) or {}
    for k in ("output_digest", "result_digest", "output_hash", "digest"):
        b = _hex_to_bytes_maybe(body.get(k)) if isinstance(body, dict) else None
        if b:
            return b
    return None


def _extract_metrics(env: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pull out a metrics object from known locations; ensure it's JSON-serializable-ish.
    """
    body = env.get("body", {}) or {}
    if isinstance(body, dict):
        for k in ("metrics", "ai_metrics", "quantum_metrics"):
            v = body.get(k)
            if isinstance(v, dict):
                return dict(v)
    return {}


# ---------------------------------------------------------------------------
# Mapping to ResultRecord
# ---------------------------------------------------------------------------


def result_from_envelope(
    obj: Any,
    *,
    height: int,
    chain_id: Optional[int] = None,
) -> Optional[ResultRecord]:
    """
    Decode an envelope-like `obj` and build a `ResultRecord` for AI/Quantum
    proofs. Returns None for non-capability proof kinds (e.g., HashShare).

    Only fields present on `ResultRecord` are set; others are ignored.
    """
    env = decode_envelope_any(obj)
    if not env:
        return None

    kind = classify_job_kind(env)
    if kind is None:
        return None  # not a capabilities job proof

    task_id = extract_task_id(env)
    output_digest = _extract_output_digest(env)
    nullifier = env.get("nullifier")
    metrics = _extract_metrics(env)

    # Prepare kwargs conservatively; filter to ResultRecord's declared fields.
    kwargs: Dict[str, Any] = {
        "task_id": task_id,
        "kind": kind,
        "height": int(height),
        "chain_id": int(chain_id) if chain_id is not None else None,
        "output_digest": output_digest,
        "nullifier": (
            nullifier
            if isinstance(nullifier, (bytes, bytearray, memoryview))
            else _hex_to_bytes_maybe(nullifier)
        ),
        "metrics": metrics,
        "source": "proof",  # helpful provenance if the dataclass exposes it
    }

    safe_kwargs = _dataclass_kwargs_safe(ResultRecord, kwargs)
    return ResultRecord(**safe_kwargs)  # type: ignore[call-arg]
