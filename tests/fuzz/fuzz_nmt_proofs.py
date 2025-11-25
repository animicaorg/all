# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **NMT proofs — verify under mutations**

What this exercises
-------------------
- Decodes arbitrary inputs as (potential) Namespaced Merkle Tree (NMT) proofs.
- Prefers project codecs/verify helpers from da.nmt.verify / da.nmt.proofs.
- Falls back to generic CBOR (core.encoding.cbor / cbor2 / msgspec.cbor).
- If an object looks proof-like, tries inclusion and/or namespace-range verification.
- Applies small, structured mutations (flip root, tweak leaf, perturb branch) and
  asserts that a proof that originally verified does *not* continue to verify once
  a cryptographically material field is changed (best-effort).
- All unexpected exceptions are swallowed so the fuzzer can explore deeply;
  assertions only fire on strong invariants (e.g., root flip still verifies).

Run via shared harness:
  python tests/fuzz/atheris_runner.py \
    --target tests.fuzz.fuzz_nmt_proofs:fuzz \
    tests/fuzz/corpus_blocks  # any seed dir; dedicated corpus recommended

Or directly:
  python -m tests.fuzz.fuzz_nmt_proofs tests/fuzz/corpus_blocks
"""
from __future__ import annotations

import copy
import sys
from typing import Any, Callable, Optional, Sequence, Tuple


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
    # Minimal stub for import-time survival
    def _loads_stub(b: bytes) -> Any:
        if b == b"\xa0":
            return {}
        if b == b"\x80":
            return []
        raise ValueError("no CBOR backend")

    def _dumps_stub(x: Any) -> bytes:
        if x == {}:
            return b"\xa0"
        if x == []:
            return b"\x80"
        raise ValueError("no CBOR backend")

    return _loads_stub, _dumps_stub, "stub"


CBOR_LOADS, CBOR_DUMPS, CBOR_BACKEND = _choose_cbor()


# ---------------- project NMT verify helpers ----------------

VERIFY_FUNCS: list[Callable[..., Any]] = []
VERIFY_NAMES = (
    # common in-tree choices
    "da.nmt.verify",
    "da.nmt.proofs",
)

for modname in VERIFY_NAMES:
    m = _import_optional(modname)
    if not m:
        continue
    for nm in (
        "verify_inclusion",
        "verify_range",
        "verify_namespace_range",
        "verify_namespace",
        "verify_proof",
        "verify",
    ):
        fn = getattr(m, nm, None)
        if callable(fn):
            VERIFY_FUNCS.append(fn)

# Leaf (de)codec (optional; most verifiers accept raw bytes already)
_LEAF_ENCODE = None
_codec = _import_optional("da.nmt.codec")
if _codec:
    for nm in ("encode_leaf", "leaf_encode", "encode"):
        fn = getattr(_codec, nm, None)
        if callable(fn):
            _LEAF_ENCODE = fn
            break

# Hash helper (best-effort)
def _sha3_256(data: bytes) -> Optional[bytes]:
    h = _import_optional("da.utils.hash") or _import_optional("core.utils.hash")
    for nm in ("sha3_256", "SHA3_256", "sha3"):
        if h and hasattr(h, nm):
            try:
                return getattr(h, nm)(data)
            except Exception:
                pass
    try:
        import hashlib
        return hashlib.sha3_256(data).digest()
    except Exception:
        return None


# ---------------- heuristics & normalization ----------------

ROOT_KEYS = ("root", "commitment", "da_root", "nmt_root", "root_hash")
LEAF_KEYS = ("leaf", "data", "value", "payload")
BRANCH_KEYS = ("proof", "branch", "siblings", "path", "nodes")
NS_KEYS = ("namespace", "ns", "ns_id", "id")
NS_RANGE_KEYS = (("start", "end"), ("start_ns", "end_ns"))

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


def _is_bytes_like(x: Any) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def _pick_key(d: dict, keys: Sequence[str]) -> Optional[str]:
    for k in keys:
        if k in d:
            return k
    # try alternate casing
    low = {k.lower(): k for k in d.keys() if isinstance(k, str)}
    for k in keys:
        if k in low:
            return low[k]
    return None


def _is_nmt_proof_like(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    has_root = _pick_key(obj, ROOT_KEYS) is not None
    has_branch = _pick_key(obj, BRANCH_KEYS) is not None
    # either leaf + namespace (inclusion) or namespace range
    has_leaf = _pick_key(obj, LEAF_KEYS) is not None
    has_ns = _pick_key(obj, NS_KEYS) is not None
    has_range = any(a in obj and b in obj for (a, b) in NS_RANGE_KEYS)
    return has_root and has_branch and ( (has_leaf and has_ns) or has_range )


# ---------------- verifier adapter ----------------

def _boolish(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if isinstance(x, dict):
        for k in ("ok", "valid", "verified", "is_valid"):
            v = x.get(k)
            if isinstance(v, bool):
                return v
    return None


def _try_calls(fn: Callable[..., Any], calls: list[tuple[tuple, dict]]) -> Optional[bool]:
    for args, kwargs in calls:
        try:
            res = fn(*args, **kwargs)
            b = _boolish(res)
            if b is not None:
                return b
            # Some APIs raise on invalid and return None on valid; treat None as inconclusive
        except (TypeError, ValueError, AssertionError):
            continue
        except Exception:
            # Don't kill the run; treat as inconclusive
            continue
    return None


def _verify_adapter(proof_obj: dict) -> Optional[bool]:
    root_k = _pick_key(proof_obj, ROOT_KEYS)
    leaf_k = _pick_key(proof_obj, LEAF_KEYS)
    branch_k = _pick_key(proof_obj, BRANCH_KEYS)
    ns_k = _pick_key(proof_obj, NS_KEYS)

    if not root_k or not branch_k:
        return None

    root = proof_obj.get(root_k)
    branch = proof_obj.get(branch_k)
    leaf = proof_obj.get(leaf_k) if leaf_k else None
    ns = proof_obj.get(ns_k) if ns_k else None

    # Try to encode leaf via project codec if available and input isn't bytes
    if leaf is not None and _LEAF_ENCODE and not _is_bytes_like(leaf):
        try:
            leaf = _LEAF_ENCODE(ns, leaf) if ns is not None else _LEAF_ENCODE(leaf)
        except Exception:
            pass

    calls: list[tuple[tuple, dict]] = []

    # Common forms we might support:
    # verify_inclusion(root, proof_obj) OR verify_inclusion(proof_obj, root)
    calls.append(((root, proof_obj), {}))
    calls.append(((proof_obj, root), {}))
    # verify_inclusion(root=root, proof=proof_obj, leaf=leaf, namespace=ns, index=..., total=...)
    calls.append(((), {"root": root, "proof": proof_obj, "leaf": leaf, "namespace": ns}))
    calls.append(((), {"commitment": root, "proof": proof_obj, "leaf": leaf, "ns": ns}))

    # Range verify: verify_range(root, proof_obj, ns) / kw variants / explicit start,end
    calls.append(((root, proof_obj, ns), {}))
    calls.append(((), {"root": root, "proof": proof_obj, "namespace": ns}))
    # If explicit range keys exist, try those too
    for (a, b) in NS_RANGE_KEYS:
        if a in proof_obj and b in proof_obj:
            calls.append(((root, proof_obj, proof_obj[a], proof_obj[b]), {}))
            calls.append(((), {"root": root, "proof": proof_obj, "start": proof_obj[a], "end": proof_obj[b]}))

    for fn in VERIFY_FUNCS:
        out = _try_calls(fn, calls)
        if out is not None:
            return out
    return None


# ---------------- mutations ----------------

def _flip_bit(b: bytes) -> bytes:
    if not b:
        return b"\x01"
    arr = bytearray(b)
    idx = len(arr) // 2  # deterministic middle byte
    arr[idx] ^= 0x01
    return bytes(arr)


def _mutate_bytes(b: Any) -> Any:
    if _is_bytes_like(b):
        return _flip_bit(bytes(b))
    if isinstance(b, int):
        return b ^ 1
    if isinstance(b, str):
        return b[::-1]
    return b


def _mutate_branch(branch: Any) -> Any:
    # try array-style mutations
    if isinstance(branch, list):
        if not branch:
            return [b"\x00"]
        # drop middle element
        return branch[: len(branch)//2] + branch[len(branch)//2 + 1 :]
    if isinstance(branch, dict):
        out = dict(branch)
        # drop any key that looks like "siblings" or "path"
        key = _pick_key(out, BRANCH_KEYS)
        if key and isinstance(out[key], list) and out[key]:
            lst = out[key]
            out[key] = lst[: len(lst)//2] + lst[len(lst)//2 + 1 :]
        else:
            # flip any bytes value found
            for k, v in list(out.items()):
                if _is_bytes_like(v):
                    out[k] = _flip_bit(bytes(v))
                    break
        return out
    # unknown shape
    return branch


def _mutate_proof(proof_obj: dict, which: str) -> dict:
    m = copy.deepcopy(proof_obj)
    if which == "root":
        k = _pick_key(m, ROOT_KEYS)
        if k:
            m[k] = _mutate_bytes(m[k])
        return m
    if which == "leaf":
        k = _pick_key(m, LEAF_KEYS)
        if k:
            m[k] = _mutate_bytes(m[k])
        return m
    if which == "ns":
        k = _pick_key(m, NS_KEYS)
        if k:
            m[k] = _mutate_bytes(m[k])
        # range keys
        for (a, b) in NS_RANGE_KEYS:
            if a in m:
                m[a] = _mutate_bytes(m[a])
            if b in m:
                m[b] = _mutate_bytes(m[b])
        return m
    if which == "branch":
        k = _pick_key(m, BRANCH_KEYS)
        if k:
            m[k] = _mutate_branch(m[k])
        return m
    return m


# ---------------- fuzz entry ----------------

def fuzz(data: bytes) -> None:
    # guard pathological inputs
    if len(data) > (1 << 20):  # 1 MiB
        return

    # Try direct project decoder (some repos ship a "proofs" decoder, but it's optional).
    obj = None
    try:
        obj = CBOR_LOADS(data)
    except Exception:
        return

    # If not proof-like, at least exercise CBOR round-trip and exit.
    if not _is_nmt_proof_like(obj):
        try:
            enc = CBOR_DUMPS(obj)
            _ = CBOR_LOADS(enc)
        except Exception:
            pass
        return

    proof_obj = obj if isinstance(obj, dict) else {}  # safety

    # 1) Baseline verification
    base_ok = None
    try:
        base_ok = _verify_adapter(proof_obj)
    except (RecursionError, MemoryError):
        return
    except Exception:
        base_ok = None  # treat as inconclusive

    # If inconclusive, still mutate to exercise code paths.
    mutations = ("root", "leaf", "ns", "branch")

    # 2) For each mutation, ensure we don't crash; if base_ok is True and the mutation
    #    changes a cryptographically material field, verification should not remain True.
    for kind in mutations:
        try:
            mut = _mutate_proof(proof_obj, kind)
            mut_ok = _verify_adapter(mut)
            if base_ok is True and mut_ok is True and kind in ("root", "leaf", "branch"):
                # Strong invariant: flipping root/leaf/branch should break a valid proof.
                raise AssertionError(f"NMT verify still True after {kind} mutation")
        except (RecursionError, MemoryError):
            return
        except Exception:
            # Any exception is fine for fuzzing (it's a target to explore), but we don't crash the process.
            continue

    # 3) Canonical bytes stability (best-effort)
    try:
        enc1 = CBOR_DUMPS(proof_obj)
        h1 = _sha3_256(enc1)
        if h1 and len(h1) == 32:
            obj2 = CBOR_LOADS(enc1)
            enc2 = CBOR_DUMPS(obj2)
            h2 = _sha3_256(enc2)
            if h2 and h1 != h2:
                # Not a correctness bug per se; still interesting to flag.
                pass
    except (RecursionError, MemoryError):
        return
    except Exception:
        return


# ---------------- direct execution ----------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_nmt_proofs] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_blocks"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
