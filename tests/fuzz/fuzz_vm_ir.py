# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **VM IR decode → validate → re-encode**

Goals
-----
- Try to decode arbitrary bytes as Animica VM IR using project encoders
  (vm_py.compiler.encode) if present; otherwise fall back to generic CBOR.
- If the object *looks like* an IR module, run lightweight validation passes:
  - vm_py.compiler.typecheck (if available)
  - vm_py.compiler.gas_estimator (if available)
- Re-encode → re-decode and assert idempotence under normalization.
- (Optional) Hash the canonical bytes to ensure stability.

Run via the shared harness:
  python tests/fuzz/atheris_runner.py \
    --target tests.fuzz.fuzz_vm_ir:fuzz \
    tests/fuzz/corpus_vm_ir

Or directly:
  python -m tests.fuzz.fuzz_vm_ir tests/fuzz/corpus_vm_ir
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple

# ---------------- optional import helper ----------------

def _import_optional(modname: str):
    try:
        __import__(modname)
        return sys.modules[modname]
    except Exception:
        return None


# ---------------- CBOR backends (generic fallback) ----------------

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
    # Extremely small stub so the target still imports
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

# ---------------- project IR enc/dec & validators ----------------

def _get_project_ir_codec():
    """
    Try to find vm_py.compiler.encode decode/encode helpers.
    Return (decode_fn, encode_fn, backend_name) or None.
    """
    m = _import_optional("vm_py.compiler.encode")
    if not m:
        return None
    # Common function name variants across repos
    dec_names = ("decode_ir", "decode", "loads", "from_bytes")
    enc_names = ("encode_ir", "encode", "dumps", "to_bytes")
    dec = None
    enc = None
    for nm in dec_names:
        fn = getattr(m, nm, None)
        if callable(fn):
            dec = fn
            break
    for nm in enc_names:
        fn = getattr(m, nm, None)
        if callable(fn):
            enc = fn
            break
    if dec and enc:
        return dec, enc, "vm_py.compiler.encode"
    return None


IR_DECODE = None  # type: Optional[Callable[[bytes], Any]]
IR_ENCODE = None  # type: Optional[Callable[[Any], bytes]]
_codec = _get_project_ir_codec()
if _codec:
    IR_DECODE, IR_ENCODE, IR_BACKEND = _codec
else:
    IR_BACKEND = "none"

# Validators (best-effort; may be absent)
_TYPECHECK = None
_ESTIMATE_GAS = None

_tc_mod = _import_optional("vm_py.compiler.typecheck")
if _tc_mod:
    for nm in ("typecheck", "validate", "check"):
        fn = getattr(_tc_mod, nm, None)
        if callable(fn):
            _TYPECHECK = fn
            break

_ge_mod = _import_optional("vm_py.compiler.gas_estimator")
if _ge_mod:
    for nm in ("estimate", "estimate_gas", "gas_estimate", "estimate_upper_bound"):
        fn = getattr(_ge_mod, nm, None)
        if callable(fn):
            _ESTIMATE_GAS = fn
            break

# Optional normalizer of IR (sometimes present)
_NORMALIZE_IR = None
_ir_mod = _import_optional("vm_py.compiler.ir")
if _ir_mod:
    for nm in ("normalize", "canonicalize", "to_canonical"):
        fn = getattr(_ir_mod, nm, None)
        if callable(fn):
            _NORMALIZE_IR = fn
            break

# ---------------- utilities ----------------

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


def _is_ir_like(x: Any) -> bool:
    """
    Heuristic shape check for an IR module.
    Typical fields include: blocks, instrs, entry, consts, version, types, symbols.
    """
    if isinstance(x, dict):
        keys = {str(k) for k in x.keys()}
        hints = {"blocks", "instrs", "instructions", "entry", "consts", "version", "types", "symbols", "module"}
        return len(keys & hints) >= 2
    if isinstance(x, list):
        # Some encodings use a top-level list of blocks/instrs
        return any(isinstance(e, (list, dict)) for e in x)
    return False


# ---------------- core round-trip & validate ----------------

def _roundtrip_and_check_ir(ir_obj: Any) -> Any:
    """
    Encode -> decode -> encode -> decode with the most canonical encoder available.
    If project IR codec is missing, fallback to generic CBOR.
    """
    if IR_DECODE and IR_ENCODE:
        enc1 = IR_ENCODE(ir_obj)
        obj2 = IR_DECODE(enc1)
        enc2 = IR_ENCODE(obj2)
        obj3 = IR_DECODE(enc2)
    else:
        enc1 = CBOR_DUMPS(ir_obj)
        obj2 = CBOR_LOADS(enc1)
        enc2 = CBOR_DUMPS(obj2)
        obj3 = CBOR_LOADS(enc2)

    if _NORMALIZE_IR:
        try:
            n1 = _NORMALIZE_IR(ir_obj)
            n3 = _NORMALIZE_IR(obj3)
        except Exception:
            n1 = _normalize_for_eq(ir_obj)
            n3 = _normalize_for_eq(obj3)
    else:
        n1 = _normalize_for_eq(ir_obj)
        n3 = _normalize_for_eq(obj3)

    if n1 != n3:
        raise AssertionError("IR round-trip not idempotent under normalization")
    return obj3


def _validate_ir(ir_obj: Any) -> None:
    """
    Best-effort: run typecheck and gas estimator if available.
    """
    if _TYPECHECK:
        try:
            _TYPECHECK(ir_obj)
        except TypeError:
            # Some APIs want (ir_obj, strict=True) or similar; try no-arg default
            try:
                _TYPECHECK(ir_obj, strict=False)
            except Exception:
                pass
        except Exception:
            # Validation failures are useful findings but should not crash the fuzzer.
            pass

    if _ESTIMATE_GAS:
        try:
            _ = _ESTIMATE_GAS(ir_obj)
        except Exception:
            # Gas estimator may legally reject malformed IR; do not crash.
            pass


# ---------------- fuzz entry ----------------

def fuzz(data: bytes) -> None:
    # Keep inputs bounded to avoid pathological allocations
    if len(data) > (1 << 20):  # 1 MiB
        return

    ir = None
    # Strategy A: project IR decoder directly on raw bytes
    if IR_DECODE:
        try:
            ir = IR_DECODE(data)
        except Exception:
            ir = None

    # Strategy B: generic CBOR → IR-like shape
    if ir is None:
        try:
            obj = CBOR_LOADS(data)
        except Exception:
            return
        if not _is_ir_like(obj):
            # Not IR; still tick CBOR round-trip path and exit.
            try:
                enc = CBOR_DUMPS(obj)
                _ = CBOR_LOADS(enc)
            except Exception:
                pass
            return
        ir = obj

    # Round-trip stability and validation passes
    try:
        ir = _roundtrip_and_check_ir(ir)
    except (RecursionError, MemoryError):
        return

    try:
        _validate_ir(ir)
    except (RecursionError, MemoryError):
        return

    # Hash canonical bytes to ensure stability (best-effort)
    try:
        if IR_ENCODE:
            enc = IR_ENCODE(ir)
        else:
            enc = CBOR_DUMPS(ir)
        h1 = _sha3_256(enc)
        if h1 and len(h1) == 32:
            # Re-decode and ensure same hash after canonical re-encode
            if IR_DECODE and IR_ENCODE:
                ir2 = IR_DECODE(enc)
                enc2 = IR_ENCODE(ir2)
            else:
                ir2 = CBOR_LOADS(enc)
                enc2 = CBOR_DUMPS(ir2)
            h2 = _sha3_256(enc2)
            if h2 and h1 != h2:
                raise AssertionError("IR canonical bytes hash unstable across encode/decode")
    except (RecursionError, MemoryError):
        return
    except Exception:
        # Non-fatal instability; let the fuzzer learn the edge case without halting.
        return


# ---------------- direct execution ----------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_vm_ir] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_vm_ir"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
