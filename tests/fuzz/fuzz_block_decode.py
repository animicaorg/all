# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **Header/Block CBOR decode & stable hash**.

What this exercises
-------------------
- Decode arbitrary bytes as CBOR using the project's canonical CBOR if present,
  else cbor2 (canonical) or msgspec.cbor.
- If the decoded object *looks like* a Header or Block, compute canonical
  SignBytes via core.encoding.canonical (when available) and hash with sha3_256.
- Re-encode → re-decode and ensure the computed hash is identical (stability).
- Always checks decode(encode(x)) idempotence under structure-normalization.

Run with the shared harness
---------------------------
python tests/fuzz/atheris_runner.py \
  --target tests.fuzz.fuzz_block_decode:fuzz \
  tests/fuzz/corpus_blocks tests/fuzz/corpus

Or directly:
python -m tests.fuzz.fuzz_block_decode tests/fuzz/corpus_blocks
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple

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
    # Tiny fallback so the file still imports; only understands {} and [].
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

# ---------------- Hash / SignBytes helpers ----------------


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


def _canonical_sign_bytes_header(obj: Any) -> Optional[bytes]:
    can = _import_optional("core.encoding.canonical")
    if not can:
        return None
    for name in ("header_sign_bytes", "sign_bytes_header", "sign_bytes"):
        fn = getattr(can, name, None)
        if callable(fn):
            try:
                return fn(obj)
            except Exception:
                continue
    return None


def _canonical_sign_bytes_block(obj: Any) -> Optional[bytes]:
    can = _import_optional("core.encoding.canonical")
    if not can:
        return None
    for name in ("block_sign_bytes", "sign_bytes_block", "sign_bytes"):
        fn = getattr(can, name, None)
        if callable(fn):
            try:
                return fn(obj)
            except Exception:
                continue
    return None


# ---------------- Shape detection & normalization ----------------

_HDR_HINT_KEYS = {
    "parent",
    "parentHash",
    "roots",
    "stateRoot",
    "txRoot",
    "height",
    "number",
    "nonce",
    "theta",
    "mix",
    "mixSeed",
    "chainId",
}
_BLK_HINT_KEYS = {"header", "txs", "transactions", "proofs", "receipts"}


def _is_header_like(x: Any) -> bool:
    if not isinstance(x, dict):
        return False
    keys = {str(k) for k in x.keys()}
    # must have at least two common header-ish keys, including some hash/parent + height-ish
    return (("parent" in keys or "parentHash" in keys) and ("height" in keys or "number" in keys)) or (
        len(_HDR_HINT_KEYS & keys) >= 3
    )


def _is_block_like(x: Any) -> bool:
    if not isinstance(x, dict):
        return False
    keys = {str(k) for k in x.keys()}
    return "header" in keys or len(_BLK_HINT_KEYS & keys) >= 2


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


# ---------------- Core checkers ----------------


def _roundtrip_and_check(obj: Any) -> Any:
    enc1 = CBOR_DUMPS(obj)
    obj2 = CBOR_LOADS(enc1)
    enc2 = CBOR_DUMPS(obj2)
    obj3 = CBOR_LOADS(enc2)
    if _normalize_for_eq(obj) != _normalize_for_eq(obj3):
        raise AssertionError("CBOR round-trip not idempotent under normalization")
    return obj3


def _hash_stability(kind: str, obj: Any) -> None:
    """
    Compute canonical SignBytes (if available) and hash; ensure stable across re-encode/decode.
    Falls back to hashing the canonical CBOR if SignBytes is not available.
    """
    if kind == "header":
        sb = _canonical_sign_bytes_header(obj)
    else:
        sb = _canonical_sign_bytes_block(obj)

    # Fallback: hash canonical CBOR encoding directly
    if sb is None:
        try:
            sb = CBOR_DUMPS(obj)
        except Exception:
            return  # cannot encode; just stop here

    h1 = _sha3_256(sb)
    if not h1 or len(h1) != 32:
        return

    # Re-encode/decode and recompute
    obj2 = CBOR_LOADS(CBOR_DUMPS(obj))
    if kind == "header":
        sb2 = _canonical_sign_bytes_header(obj2) or CBOR_DUMPS(obj2)
    else:
        sb2 = _canonical_sign_bytes_block(obj2) or CBOR_DUMPS(obj2)
    h2 = _sha3_256(sb2)
    if not h2 or len(h2) != 32:
        return
    if h1 != h2:
        raise AssertionError(f"{kind} hash not stable across encode/decode")


# -------------------- Exported fuzz target --------------------


def fuzz(data: bytes) -> None:
    # Keep hard cap to avoid pathological inputs
    if len(data) > (1 << 20):  # 1 MiB
        return
    try:
        obj = CBOR_LOADS(data)
    except Exception:
        return

    try:
        obj = _roundtrip_and_check(obj)
    except (RecursionError, MemoryError):
        return

    # Compute stable hashes for header/block-like objects
    try:
        if _is_block_like(obj):
            _hash_stability("block", obj)
            # Also try header inside the block, if present
            hdr = obj.get("header") if isinstance(obj, dict) else None
            if isinstance(hdr, dict) and _is_header_like(hdr):
                _hash_stability("header", hdr)
        elif _is_header_like(obj):
            _hash_stability("header", obj)
    except (RecursionError, MemoryError):
        return


# -------------------- Direct execution (optional) --------------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_block_decode] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_blocks"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
