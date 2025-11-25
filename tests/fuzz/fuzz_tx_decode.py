# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **Tx CBOR decode/encode/validate** round-trips.

Targets
-------
- Accepts arbitrary bytes
- Attempts to decode as CBOR via project canonical encoder if available,
  otherwise falls back to cbor2/msgspec.
- If a Tx-like mapping is decoded, re-encodes with canonical options and
  checks idempotence (decode(encode(x)) == x) modulo ordering.
- If SignBytes helpers exist (core.encoding.canonical), computes sign-bytes
  and a sha3_256 hash to tick more coverage.

How to run (via shared runner)
------------------------------
python tests/fuzz/atheris_runner.py \
  --target tests.fuzz.fuzz_tx_decode:fuzz \
  tests/fuzz/corpus_txs tests/fuzz/corpus

You can also run directly:
python -m tests.fuzz.fuzz_tx_decode tests/fuzz/corpus_txs

Environment
-----------
Uses whichever CBOR backend it finds:
- core.encoding.cbor (preferred: canonical, deterministic)
- cbor2 (with canonical=True)
- msgspec.cbor
"""
from __future__ import annotations

import io
import sys
import types
from typing import Any, Callable, Dict, Optional, Tuple

# ---------------- CBOR backends ----------------

DecodeFn = Callable[[bytes], Any]
EncodeFn = Callable[[Any], bytes]


def _import_optional(modname: str):
    try:
        __import__(modname)
        return sys.modules[modname]
    except Exception:
        return None


def _get_project_cbor() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    """
    Prefer the repo's canonical CBOR if present.
    Expect functions: loads(bytes)->obj, dumps(obj)->bytes
    """
    m = _import_optional("core.encoding.cbor")
    if not m:
        return None
    loads = getattr(m, "loads", None)
    dumps = getattr(m, "dumps", None)
    if callable(loads) and callable(dumps):
        return loads, dumps, "core.encoding.cbor"
    # alt names (rare)
    loads = getattr(m, "decode", None)
    dumps = getattr(m, "encode", None)
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
        # canonical=True ensures deterministic ordering/lengths
        try:
            return m.dumps(x, canonical=True)
        except TypeError:
            # Older cbor2 without canonical kwarg
            return m.dumps(x)

    return _loads, _dumps, "cbor2"


def _get_msgspec() -> Optional[Tuple[DecodeFn, EncodeFn, str]]:
    m = _import_optional("msgspec")
    if not m:
        return None
    cbor = getattr(m, "cbor", None)
    if not cbor:
        return None
    return cbor.decode, cbor.encode, "msgspec.cbor"


def _choose_cbor() -> Tuple[DecodeFn, EncodeFn, str]:
    for prov in (_get_project_cbor(), _get_cbor2(), _get_msgspec()):
        if prov:
            return prov
    # Super-minimal fallback: accept only bytes that look like empty map/array
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
        # This is a stub; raise to surface missing backend
        raise ValueError("no CBOR backend available")
    return _loads_stub, _dumps_stub, "stub"


CBOR_LOADS, CBOR_DUMPS, CBOR_BACKEND = _choose_cbor()

# --------------- Project helpers (optional) ---------------


def _project_sign_bytes(obj: Any) -> Optional[bytes]:
    """
    Try to compute canonical SignBytes if the helper exists.
    """
    m = _import_optional("core.encoding.canonical")
    if not m:
        return None
    enc = getattr(m, "tx_sign_bytes", None) or getattr(m, "sign_bytes_tx", None) or getattr(m, "sign_bytes", None)
    if not callable(enc):
        return None
    try:
        return enc(obj)  # may accept dict/Tx dataclass
    except Exception:
        return None


def _sha3_256(data: bytes) -> Optional[bytes]:
    h = _import_optional("core.utils.hash")
    if h and hasattr(h, "sha3_256"):
        try:
            return h.sha3_256(data)
        except Exception:
            pass
    # Portable fallback using hashlib if available
    try:
        import hashlib

        return hashlib.sha3_256(data).digest()
    except Exception:
        return None


# --------------- Light "Tx-like" detection ----------------


def _is_tx_like(x: Any) -> bool:
    if not isinstance(x, dict):
        return False
    # Heuristic: typical keys
    keys = set(map(str, x.keys()))
    needed = {"chainId", "nonce", "gas"} & keys
    has_kind = any(k in keys for k in ("kind", "type", "txType"))
    has_from = any(k in keys for k in ("from", "sender"))
    return bool(needed) and (has_kind or has_from)


def _normalize_for_eq(x: Any) -> Any:
    """
    Normalize mapping/sequence types for tolerant equality comparison:
    - dicts -> sorted tuples of (key, value_norm)
    - lists -> tuple of value_norm
    - bytes/bytearray -> bytes
    Other scalars unchanged.
    """
    if isinstance(x, dict):
        items = []
        for k, v in x.items():
            items.append((k, _normalize_for_eq(v)))
        # sort by key repr to avoid non-comparable key types explosion
        items.sort(key=lambda kv: repr(kv[0]))
        return tuple(items)
    if isinstance(x, (list, tuple)):
        return tuple(_normalize_for_eq(v) for v in x)
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    return x


def _roundtrip_and_check(obj: Any) -> None:
    """
    Encode -> decode -> canonical encode -> decode; assert idempotence.
    Always verifies that decoding the result yields an equal structure under normalization.
    """
    enc1 = CBOR_DUMPS(obj)
    obj2 = CBOR_LOADS(enc1)
    enc2 = CBOR_DUMPS(obj2)
    obj3 = CBOR_LOADS(enc2)

    n1 = _normalize_for_eq(obj)
    n3 = _normalize_for_eq(obj3)
    if n1 != n3:
        # Provide a compact diff hint
        raise AssertionError("CBOR round-trip not idempotent under normalization")

    # Exercise SignBytes + hash if we detect a Tx-like shape or if the helper accepts it.
    if _is_tx_like(obj3):
        sb = _project_sign_bytes(obj3)
        if sb:
            h = _sha3_256(sb)
            # Touch the bytes to ensure they are consumed in coverage
            if h and len(h) != 32:
                raise AssertionError("sha3_256 length mismatch")
    else:
        # Even if not Tx-like, try SignBytes opportunistically (many helpers accept dicts).
        sb = _project_sign_bytes(obj3)
        if sb:
            _ = _sha3_256(sb)


# -------------------- Exported fuzz target --------------------


def fuzz(data: bytes) -> None:
    """
    Entry-point for atheris_runner.py (expects bytes -> None / exceptions).
    """
    # Hard cap to avoid pathological growth in external backends
    if len(data) > (1 << 20):  # 1 MiB
        return
    try:
        obj = CBOR_LOADS(data)
    except Exception:
        return
    try:
        _roundtrip_and_check(obj)
    except (RecursionError, MemoryError):
        # Treat as interesting but don't crash the fuzzer (OOM/Deep nest)
        return


# -------------------- Direct execution (optional) --------------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write(
            "[fuzz_tx_decode] atheris not installed. Install with: pip install atheris\n"
        )
        return 2
    atheris.instrument_all()
    # Determine corpus paths from argv; if none, synthesize a default
    corpus = [p for p in argv if not p.startswith("-")]
    if not corpus:
        corpus = ["tests/fuzz/corpus_txs"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
