import importlib
import inspect
import os
import random
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest

# ------------------------------------------------------------
# Adapters: tolerant to small API variations across modules
# ------------------------------------------------------------


def _import(path: str):
    return importlib.import_module(path)


def _as_bytes(x: Union[bytes, bytearray, str]) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        s = x[2:] if x.startswith("0x") else x
        try:
            return bytes.fromhex(s)
        except ValueError:
            return s.encode("utf-8")
    raise TypeError(f"cannot coerce type {type(x)} to bytes")


def _extract_commit_fields(ret: Any) -> Tuple[bytes, int, Optional[int]]:
    """
    Normalize various return shapes to (root_bytes, size, ns).
    Accepted shapes:
      - (root, size, ns) or (root, size)
      - dict with keys 'root'/'commitment' and 'size'/'length' and optional 'ns'/'namespace'
      - dataclass with similar fields
      - object with attributes root|commitment, size|length, ns|namespace
      - hex string for root with no other metadata (size/ns inferred as None)
    """
    root: Optional[bytes] = None
    size: Optional[int] = None
    ns: Optional[int] = None

    def _from_mapping(m: Dict[str, Any]):
        nonlocal root, size, ns
        for k in ("root", "commitment", "commit", "da_root"):
            if k in m and root is None:
                root = _as_bytes(m[k])
        for k in ("size", "length", "byte_len", "bytes"):
            if k in m and size is None and isinstance(m[k], int):
                size = int(m[k])
        for k in ("ns", "namespace", "namespace_id"):
            if k in m and ns is None and isinstance(m[k], int):
                ns = int(m[k])

    if isinstance(ret, (tuple, list)):
        if len(ret) >= 1:
            root = _as_bytes(ret[0])
        if len(ret) >= 2 and isinstance(ret[1], int):
            size = int(ret[1])
        if len(ret) >= 3 and isinstance(ret[2], int):
            ns = int(ret[2])
    elif isinstance(ret, dict):
        _from_mapping(ret)
    elif is_dataclass(ret):
        _from_mapping(asdict(ret))
    else:
        # try attributes
        if hasattr(ret, "__dict__"):
            d = {k: getattr(ret, k) for k in dir(ret) if not k.startswith("_")}
            _from_mapping(d)
        # maybe it's just a hex/bytes
        if root is None and isinstance(ret, (bytes, bytearray, str)):
            root = _as_bytes(ret)

    if root is None:
        raise AssertionError("Could not locate commitment root in return value")
    return root, (size if size is not None else -1), ns


def _commit_fn():
    """Find a commit function in da.blob.commitment with signature (data: bytes, ns: int) -> any."""
    mod = _import("da.blob.commitment")
    # common names
    for name in ("commit", "commit_blob", "compute_commitment", "make_commitment"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            if callable(fn):
                return fn
    # class-based
    for cname in ("Committer", "Commit", "BlobCommitter"):
        if hasattr(mod, cname):
            cls = getattr(mod, cname)
            try:
                inst = cls()
            except Exception:
                inst = None
            for meth in ("commit", "compute", "run"):
                if inst is not None and hasattr(inst, meth):
                    return getattr(inst, meth)
                if hasattr(cls, meth):
                    return getattr(cls, meth)
    raise RuntimeError("No commit function found in da.blob.commitment")


def _encoder_leaves_fn():
    """
    Optional: find a function that turns (data, ns) -> List[bytes] (namespaced leaves).
    If not available, return None to skip the cross-check.
    """
    try:
        mod = _import("da.erasure.encoder")
    except ModuleNotFoundError:
        return None

    for name in ("encode_leaves", "blob_to_leaves", "encode", "build_leaves"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            if callable(fn):
                return fn
    return None


def _nmt_commit_fn():
    """Find NMT commit function: (leaves: List[bytes]) -> bytes root."""
    mod = _import("da.nmt.commit")
    for name in ("commit", "compute_root", "nmt_root"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            if callable(fn):
                return fn
    raise RuntimeError("No NMT commit function in da.nmt.commit")


# ------------------------------------------------------------
# Test fixtures
# ------------------------------------------------------------


def _rnd_bytes(n: int, seed: int) -> bytes:
    random.seed(seed)
    return bytes(random.getrandbits(8) for _ in range(n))


SAMPLES = [
    (b"", 7),
    (_rnd_bytes(4 * 1024, 1234), 24),
    (_rnd_bytes(64 * 1024 + 13, 5678), 42),
]


# ------------------------------------------------------------
# Tests
# ------------------------------------------------------------


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_commitment_is_deterministic(data: bytes, ns: int):
    commit = _commit_fn()
    r1 = _extract_commit_fields(commit(data, ns))
    r2 = _extract_commit_fields(commit(data, ns))
    assert r1[0] == r2[0], "same input must produce identical commitment root"
    if r1[1] != -1 and r2[1] != -1:
        assert (
            r1[1] == r2[1] == len(data)
        ), "reported size must be stable and equal to input length"
    if r1[2] is not None and r2[2] is not None:
        assert r1[2] == r2[2] == ns, "namespace must round-trip unchanged"
    # root length sanity (sha3-256 or similar)
    assert len(r1[0]) >= 16, "commitment root unexpectedly short"


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_commitment_changes_with_namespace(data: bytes, ns: int):
    commit = _commit_fn()
    root_a, *_ = _extract_commit_fields(commit(data, ns))
    root_b, *_ = _extract_commit_fields(commit(data, ns + 1))
    assert root_a != root_b, "changing namespace id must change commitment root"


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_commitment_changes_with_content(data: bytes, ns: int):
    if len(data) == 0:
        pytest.skip("empty sample has no single-byte flip to test")
    commit = _commit_fn()
    root_a, *_ = _extract_commit_fields(commit(data, ns))
    flipped = bytearray(data)
    flipped[0] ^= 0xFF
    root_b, *_ = _extract_commit_fields(commit(bytes(flipped), ns))
    assert root_a != root_b, "changing content must change commitment root"


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_commitment_accepts_bytearray_equivalently(data: bytes, ns: int):
    commit = _commit_fn()
    try:
        r1 = _extract_commit_fields(commit(data, ns))[0]
        r2 = _extract_commit_fields(commit(bytearray(data), ns))[0]
    except TypeError:
        pytest.skip("commit() does not accept bytearray — skipping equivalence test")
    assert r1 == r2, "bytes vs bytearray must produce identical root"


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_commitment_matches_nmt_of_encoder_leaves_when_available(data: bytes, ns: int):
    """
    Cross-check (optional): if the erasure encoder exposes a leaf builder, the NMT root of those
    leaves must match the commitment's root exactly.
    """
    leaves_fn = _encoder_leaves_fn()
    if leaves_fn is None:
        pytest.skip("encoder leaves function not found; skipping cross-check")
    nmt_commit = _nmt_commit_fn()
    commit = _commit_fn()

    try:
        leaves = leaves_fn(data, ns)
    except TypeError:
        # Some encoders might only take data and embed ns elsewhere; try single-arg form.
        leaves = leaves_fn(data)

    assert (
        isinstance(leaves, (list, tuple)) and len(leaves) > 0
    ), "encoder must return a non-empty leaf list"
    assert all(
        isinstance(x, (bytes, bytearray)) for x in leaves
    ), "leaves must be bytes-like"

    manual_root = _as_bytes(nmt_commit(list(leaves)))
    committed_root, *_ = _extract_commit_fields(commit(data, ns))
    assert (
        manual_root == committed_root
    ), "commit() root must equal NMT(root(leaves)) from encoder"


@pytest.mark.parametrize("data,ns", SAMPLES)
def test_reported_size_equals_input_length_when_present(data: bytes, ns: int):
    commit = _commit_fn()
    _, size, _ = _extract_commit_fields(commit(data, ns))
    if size == -1:
        pytest.skip("commit() does not report size; skipping size assertion")
    assert size == len(data), "reported size must equal input length"
