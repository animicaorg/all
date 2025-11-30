import copy
import os
import random
from typing import Any, Iterable, List, Optional, Tuple

import pytest

# Shared helpers and native handle from the package test utilities
from . import SKIP_HEAVY, TEST_SEED, is_ci, native  # type: ignore

# Python reference NMT (pure-Python)
# Expected to expose root()/open()/verify() or nmt_root()/nmt_open()/nmt_verify()
try:
    from omni.da import nmt as pyref  # type: ignore
except Exception as e:  # pragma: no cover - make error helpful
    raise RuntimeError(
        "Could not import Python NMT reference module 'omni.da.nmt'. "
        "Ensure omni/da/nmt.py is available on PYTHONPATH."
    ) from e


# ---------- Adapter layer to tolerate small API differences ----------


def _py_root(leaves: List[bytes], ns: bytes) -> bytes:
    if hasattr(pyref, "nmt_root"):
        return _normalize_bytes(pyref.nmt_root(leaves, ns))  # type: ignore[attr-defined]
    if hasattr(pyref, "root"):
        return _normalize_bytes(pyref.root(leaves, ns))  # type: ignore[attr-defined]
    raise AttributeError("Reference NMT module missing root/nmt_root function")


def _py_open_inclusion(leaves: List[bytes], index: int, ns: bytes) -> Any:
    if hasattr(pyref, "nmt_open"):
        return pyref.nmt_open(leaves, index, ns)  # type: ignore[attr-defined]
    if hasattr(pyref, "open"):
        return pyref.open(leaves, index, ns)  # type: ignore[attr-defined]
    raise AttributeError("Reference NMT module missing open/nmt_open function")


def _py_verify(proof: Any, leaf: bytes, root: bytes) -> bool:
    if hasattr(pyref, "nmt_verify"):
        return bool(pyref.nmt_verify(proof, leaf, root))  # type: ignore[attr-defined]
    if hasattr(pyref, "verify"):
        return bool(pyref.verify(proof, leaf, root))  # type: ignore[attr-defined]
    # Some implementations put verify on the proof itself
    if hasattr(proof, "verify"):
        return bool(proof.verify(leaf, root))  # type: ignore[attr-defined]
    raise AttributeError("Reference NMT module missing verify/nmt_verify/proof.verify")


# ---------- Helpers ----------

NAMESPACE_SIZE = 8  # bytes (fixed in tests; must match pyref expectations)


def _normalize_bytes(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        s = x[2:] if x.startswith("0x") else x
        if len(s) % 2:
            s = "0" + s
        return bytes.fromhex(s)
    raise TypeError(f"Unsupported bytes-like: {type(x)}")


def _rand(seed: Optional[int]) -> random.Random:
    if seed is None:
        seed = int.from_bytes(os.urandom(8), "little")
    return random.Random(seed)


def _ns(rng: random.Random, fixed: Optional[bytes] = None) -> bytes:
    if fixed is not None:
        assert len(fixed) == NAMESPACE_SIZE
        return fixed
    return bytes(rng.getrandbits(8) for _ in range(NAMESPACE_SIZE))


def _leaf_data(pattern: str, size: int, rng: random.Random) -> bytes:
    if pattern == "zeros":
        return b"\x00" * size
    if pattern == "ones":
        return b"\xff" * size
    if pattern == "inc":
        return bytes((i % 256 for i in range(size)))
    if pattern == "random":
        return bytes(rng.getrandbits(8) for _ in range(size))
    raise ValueError(f"unknown pattern {pattern}")


def _mk_leaves(
    n: int, pattern: str, rng: random.Random, size_range=(0, 256)
) -> List[bytes]:
    lo, hi = size_range
    return [_leaf_data(pattern, rng.randrange(lo, hi + 1), rng) for _ in range(n)]


def _mutate_bytes(b: bytes) -> bytes:
    if not b:
        return b"\x01"  # create non-empty invalid
    ba = bytearray(b)
    ba[0] ^= 0x01
    return bytes(ba)


# ---------- Parameter spaces ----------

LEAF_COUNTS_SMALL = [0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 33]
LEAF_COUNTS_HEAVY = [64, 128, 257]
PATTERNS = ["zeros", "ones", "inc", "random"]


# ---------- Root parity & determinism ----------


@pytest.mark.parametrize(
    "count", LEAF_COUNTS_SMALL + ([] if SKIP_HEAVY or is_ci() else LEAF_COUNTS_HEAVY)
)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_nmt_root_parity(pattern: str, count: int):
    rng = _rand(TEST_SEED)
    ns = _ns(rng)
    leaves = _mk_leaves(count, pattern, rng)

    root_native = _normalize_bytes(native.nmt_root(leaves, ns))
    root_py = _py_root(leaves, ns)

    assert (
        root_native == root_py
    ), f"root mismatch ns={ns.hex()} count={count} pattern={pattern}"


@pytest.mark.parametrize("count", [0, 1, 2, 8, 31, 32])
def test_nmt_root_is_deterministic(count: int):
    rng = _rand(TEST_SEED)
    ns = _ns(rng)
    leaves = _mk_leaves(count, "random", rng)

    r1 = _normalize_bytes(native.nmt_root(leaves, ns))
    r2 = _normalize_bytes(native.nmt_root(list(leaves), bytes(ns)))  # copies
    assert r1 == r2 == _py_root(leaves, ns)


# ---------- Inclusion proof: verify with both impls, then negative tests ----------


@pytest.mark.parametrize("count", [1, 2, 3, 7, 8, 15, 16, 31, 32])
@pytest.mark.parametrize("pick_index", [0, -1, "mid", "rand"])
def test_inclusion_proof_roundtrip(count: int, pick_index: Any):
    rng = _rand(TEST_SEED)
    ns = _ns(rng)
    leaves = _mk_leaves(count, "random", rng, size_range=(0, 512))
    root = _normalize_bytes(native.nmt_root(leaves, ns))

    if pick_index == "mid":
        idx = count // 2
    elif pick_index == "rand":
        idx = rng.randrange(0, count)
    else:
        idx = int(pick_index) % count

    proof = _py_open_inclusion(leaves, idx, ns)
    leaf = leaves[idx]

    # Sanity: Python reference verifies
    assert _py_verify(
        proof, leaf, root
    ), "reference impl failed to verify a valid proof"

    # Native verifies the reference proof object/shape
    assert bool(
        native.nmt_verify(proof, leaf, root)
    ), "native failed to verify a valid proof"

    # Negative: mutate leaf -> should fail both
    bad_leaf = _mutate_bytes(leaf)
    assert not _py_verify(proof, bad_leaf, root), "reference verified mutated leaf"
    assert not native.nmt_verify(proof, bad_leaf, root), "native verified mutated leaf"

    # Negative: mutate proof if we can detect typical fields
    mutated = _maybe_mutate_proof(proof)
    if mutated is not None:
        assert not _py_verify(mutated, leaf, root), "reference verified mutated proof"
        assert not native.nmt_verify(
            mutated, leaf, root
        ), "native verified mutated proof"


def _maybe_mutate_proof(proof: Any) -> Optional[Any]:
    """
    Best-effort mutation across common proof encodings:
    - dict with 'siblings' (list[bytes]) or 'path'
    - object with .siblings or .path
    Returns a deep-copied mutated proof or None if not recognized.
    """
    try:
        p = copy.deepcopy(proof)
    except Exception:
        p = proof  # try shallow fallback

    # dict-like
    if isinstance(p, dict):
        if "siblings" in p and isinstance(p["siblings"], list) and p["siblings"]:
            p["siblings"][0] = _mutate_bytes(_normalize_bytes(p["siblings"][0]))
            return p
        if "path" in p and isinstance(p["path"], list) and p["path"]:
            p["path"][0] = _mutate_bytes(_normalize_bytes(p["path"][0]))
            return p

    # object-like
    for attr in ("siblings", "path"):
        if hasattr(p, attr):
            val = getattr(p, attr)
            if isinstance(val, list) and val:
                val = list(val)
                val[0] = _mutate_bytes(_normalize_bytes(val[0]))
                try:
                    setattr(p, attr, val)
                except Exception:
                    pass
                return p

    return None


# ---------- Namespaces: changing ns must change the root (unless degenerate) ----------


@pytest.mark.parametrize("count", [1, 2, 8, 32])
def test_namespace_affects_root(count: int):
    rng = _rand(TEST_SEED)
    leaves = _mk_leaves(count, "inc", rng, size_range=(1, 64))
    ns1 = _ns(rng, fixed=(b"\x00" * NAMESPACE_SIZE))
    ns2 = _ns(rng, fixed=(b"\xff" * NAMESPACE_SIZE))

    r1 = _normalize_bytes(native.nmt_root(leaves, ns1))
    r2 = _normalize_bytes(native.nmt_root(leaves, ns2))
    assert r1 != r2, "different namespaces should yield different roots"


# ---------- Bytes-like acceptance for leaves ----------


@pytest.mark.parametrize("factory", [bytes, bytearray, memoryview])
def test_leaves_accept_bytes_like(factory):
    rng = _rand(TEST_SEED)
    ns = _ns(rng)
    raw = _mk_leaves(8, "random", rng, size_range=(3, 12))
    leaves = [factory(b) for b in raw]

    native_root = _normalize_bytes(native.nmt_root(leaves, ns))
    py_root = _py_root([bytes(b) for b in leaves], ns)
    assert native_root == py_root


# ---------- Stress-ish (optional, gated) ----------


@pytest.mark.skipif(
    SKIP_HEAVY or is_ci(), reason="heavy test; set SKIP_HEAVY=0 to enable"
)
def test_many_random_configs_stochastic():
    rng = _rand(TEST_SEED)
    for _ in range(200):
        count = rng.randrange(0, 200)
        ns = _ns(rng)
        pattern = rng.choice(PATTERNS)
        leaves = _mk_leaves(count, pattern, rng, size_range=(0, 256))
        root_native = _normalize_bytes(native.nmt_root(leaves, ns))
        root_py = _py_root(leaves, ns)
        assert (
            root_native == root_py
        ), f"mismatch in randomized trial (count={count}, pattern={pattern})"
