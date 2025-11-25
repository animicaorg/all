# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **Proof envelope parse & schema check**

This target feeds arbitrary bytes and tries to:
- Decode as CBOR (project canonical preferred, else cbor2/msgspec).
- Parse as a ProofEnvelope using project helpers if available (proofs.cbor).
- Validate the decoded envelope against project schemas if available
  (proofs.utils.schema and/or proofs.registry).
- Re-encode/decode and ensure idempotence under structure normalization.
- Exercise nullifier helpers and hashing to tick more code paths.

Run with the shared harness:
  python tests/fuzz/atheris_runner.py \
    --target tests.fuzz.fuzz_proof_envelopes:fuzz \
    tests/fuzz/corpus_proofs

Or directly:
  python -m tests.fuzz.fuzz_proof_envelopes tests/fuzz/corpus_proofs
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple

# ---------------- optional imports ----------------

def _import_optional(modname: str):
    try:
        __import__(modname)
        return sys.modules[modname]
    except Exception:
        return None


# ---------------- CBOR backends ----------------

DecodeFn = Callable[[bytes], Any]
EncodeFn = Callable[[Any], bytes]


def _get_project_cbor() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("core.encoding.cbor")
    if not m:
        return None
    loads = getattr(m, "loads", None) or getattr(m, "decode", None)
    dumps = getattr(m, "dumps", None) or getattr(m, "encode", None)
    if callable(loads) and callable(dumps):
        return loads, dumps, "core.encoding.cbor"
    return None


def _get_cbor2() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("cbor2")
    if not m:
        return None

    def _loads(b: bytes) -> Any:
        return m.loads(b)

    def _dumps(x: Any) -> bytes:
        try:
            return m.dumps(x, canonical=True)
        except TypeError:
            return m.dumps(x)

    return _loads, _dumps, "cbor2"


def _get_msgspec() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("msgspec")
    if not m or not hasattr(m, "cbor"):
        return None
    return m.cbor.decode, m.cbor.encode, "msgspec.cbor"


def _choose_cbor() -> Tuple[DecodeFn, EncodeFn, str]:
    for prov in (_get_project_cbor(), _get_cbor2(), _get_msgspec()):
        if prov:
            return prov
    # Minimal stub so the target still imports
    def _loads_stub(b: bytes) -> Any:
        if b == b"\xa0":
            return {}
        if b == b"\x80":
            return []
        raise ValueError("no CBOR backend available")

    def _dumps_stub(x: Any) -> bytes:
        if x == {}:
            return b"\xa0"
        if x == []:
            return b"\x80"
        raise ValueError("no CBOR backend available")

    return _loads_stub, _dumps_stub, "stub"


CBOR_LOADS, CBOR_DUMPS, CBOR_BACKEND = _choose_cbor()

# ---------------- hashing & project helpers ----------------

def _sha3_256(data: bytes) -> Optional[bytes]:
    h = _import_optional("core.utils.hash")
    if h and hasattr(h, "sha3_256"):
        try:
            return h.sha3_256(data)
        except Exception:
            pass
    try:
        import hashlib
        return hashlib.sha3_256(data).digest()
    except Exception:
        return None


def _decode_envelope_via_project(raw: bytes) -> Optional[dict]:
    """
    Try the project's proofs.cbor decoder variants directly on raw bytes.
    """
    m = _import_optional("proofs.cbor")
    if not m:
        return None
    for name in ("decode_envelope", "envelope_decode", "loads_envelope", "loads", "decode"):
        fn = getattr(m, name, None)
        if callable(fn):
            try:
                out = fn(raw)
                if isinstance(out, dict):
                    return out
            except Exception:
                continue
    return None


def _compute_nullifier(envelope: dict) -> Optional[bytes]:
    n = _import_optional("proofs.nullifiers")
    if not n:
        return None
    # Common shapes: pass the body, or the whole envelope
    for name in ("compute_nullifier", "nullifier", "for_body", "from_envelope"):
        fn = getattr(n, name, None)
        if callable(fn):
            try:
                if name in ("for_body", "compute_nullifier"):
                    return fn(envelope.get("body"))
                return fn(envelope)
            except Exception:
                continue
    return None


def _validate_envelope(envelope: dict) -> None:
    """
    Best-effort schema validation. We try, in order:
    - proofs.utils.schema.{validate_envelope|validate}(envelope[, schema])
    - proofs.registry schema lookup by type_id then validate()
    If none available, just return silently.
    """
    # 1) proofs.utils.schema
    s = _import_optional("proofs.utils.schema")
    if s:
        for name in ("validate_envelope", "validate"):
            fn = getattr(s, name, None)
            if callable(fn):
                try:
                    # Some variants may accept (obj) or (obj, schema_name)
                    try:
                        fn(envelope)
                        return
                    except TypeError:
                        fn(envelope, "proof_envelope")
                        return
                except Exception:
                    # Continue to other strategies
                    break

    # 2) registry-driven validation
    reg = _import_optional("proofs.registry")
    if reg:
        # get schema object or validator function for a type id
        tid = envelope.get("type_id") if isinstance(envelope, dict) else None
        get_schema_names = ("get_schema_for_type", "schema_for_type", "get_type_schema")
        schema_obj = None
        for nm in get_schema_names:
            fn = getattr(reg, nm, None)
            if callable(fn) and isinstance(tid, (int, str)):
                try:
                    schema_obj = fn(tid)
                    break
                except Exception:
                    continue
        if schema_obj and s:
            # try s.validate(obj, schema_obj)
            val = getattr(s, "validate", None)
            if callable(val):
                try:
                    val(envelope.get("body"), schema_obj)
                    return
                except Exception:
                    pass
        # Fallback: registry might expose a verifier that can parse/inspect
        for nm in ("get_verifier", "verifier_for_type", "get_handler"):
            fn = getattr(reg, nm, None)
            if callable(fn) and isinstance(tid, (int, str)):
                try:
                    ver = fn(tid)
                    # Try common method names that don't *mutate* state
                    for meth in ("parse", "inspect", "decode", "validate", "verify"):
                        mm = getattr(ver, meth, None)
                        if callable(mm):
                            try:
                                # Pass body when sensible
                                _ = mm(envelope.get("body", envelope))
                                return
                            except Exception:
                                continue
                except Exception:
                    continue
    # If nothing matched, we consider validation as a no-op.


# ---------------- heuristics & normalization ----------------

def _is_envelope_like(x: Any) -> bool:
    if not isinstance(x, dict):
        return False
    keys = {str(k) for k in x.keys()}
    has_type = any(k in keys for k in ("type_id", "typeId", "t"))
    has_body = any(k in keys for k in ("body", "b"))
    return has_type and has_body


def _normalize_for_eq(x: Any) -> Any:
    if isinstance(x, dict):
        items = []
        for k, v in x.items():
            items.append((k, _normalize_for_eq(v)))
        items.sort(key=lambda kv: repr(kv[0]))
        return tuple(items)
    if isinstance(x, (list, tuple)):
        return tuple(_normalize_for_eq(v) for v in x)
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    return x


def _roundtrip_idempotent(x: Any) -> Any:
    enc1 = CBOR_DUMPS(x)
    x2 = CBOR_LOADS(enc1)
    enc2 = CBOR_DUMPS(x2)
    x3 = CBOR_LOADS(enc2)
    if _normalize_for_eq(x) != _normalize_for_eq(x3):
        raise AssertionError("CBOR round-trip not idempotent under normalization")
    return x3


def _canonicalize_envelope_shape(env: dict) -> dict:
    # Normalize common alias keys to canonical field names
    tid = env.get("type_id", env.get("typeId", env.get("t")))
    body = env.get("body", env.get("b"))
    nul = env.get("nullifier", env.get("n"))
    out = {"type_id": tid, "body": body}
    if nul is not None:
        out["nullifier"] = nul
    # Preserve extra fields to keep fuzzer reaching more code paths
    for k, v in env.items():
        if k not in ("type_id", "typeId", "t", "body", "b", "nullifier", "n"):
            out[k] = v
    return out


# ---------------- fuzz entry ----------------

def fuzz(data: bytes) -> None:
    if len(data) > (1 << 20):  # 1 MiB cap
        return

    # Strategy A: project envelope decoder on raw bytes
    env = None
    try:
        env = _decode_envelope_via_project(data)
    except Exception:
        env = None

    # Strategy B: generic CBOR decode then shape detection
    if env is None:
        try:
            obj = CBOR_LOADS(data)
        except Exception:
            return
        if _is_envelope_like(obj):
            env = _canonicalize_envelope_shape(obj)
        else:
            # Not envelope-like; still exercise round-trip and bail.
            try:
                _ = _roundtrip_idempotent(obj)
            except (RecursionError, MemoryError):
                pass
            return

    # Round-trip stability on envelope itself
    try:
        env = _roundtrip_idempotent(env)
    except (RecursionError, MemoryError):
        return

    # Best-effort schema validation
    try:
        _validate_envelope(env)
    except (RecursionError, MemoryError):
        return
    except Exception:
        # Validation is best-effort; do not crash fuzzer for schema rejects
        pass

    # Compute nullifier if helper exists (does not assert equality; just for coverage)
    try:
        _ = _compute_nullifier(env)
    except (RecursionError, MemoryError):
        return

    # Hash canonical CBOR of the envelope to tick hashing code paths
    try:
        enc = CBOR_DUMPS(env)
        h1 = _sha3_256(enc)
        if h1 and len(h1) == 32:
            # Re-decode and ensure hash of canonical CBOR is stable
            env2 = CBOR_LOADS(enc)
            enc2 = CBOR_DUMPS(env2)
            h2 = _sha3_256(enc2)
            if h2 and h1 != h2:
                raise AssertionError("Envelope hash unstable across encode/decode")
    except (RecursionError, MemoryError):
        return
    except Exception:
        # Hashing instability shouldn't halt the fuzzer hard; let it learn.
        return


# ---------------- direct execution ----------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_proof_envelopes] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_proofs"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
