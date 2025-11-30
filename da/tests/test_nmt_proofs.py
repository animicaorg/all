import importlib
import inspect
from copy import deepcopy
from typing import Any, Callable, Iterable, List, Optional, Tuple, Union

import pytest

# ---------------------------
# Generic helpers (tolerant to internal API naming drift)
# ---------------------------


def _sha3_256() -> Callable[[bytes], bytes]:
    hmod = importlib.import_module("da.utils.hash")
    for name in ("sha3_256", "sha3_256_bytes", "hash_sha3_256"):
        if hasattr(hmod, name):
            fn = getattr(hmod, name)

            def _wrap(b: bytes, _fn=fn):
                out = _fn(b)
                return out if isinstance(out, (bytes, bytearray)) else bytes(out)

            return _wrap
    raise RuntimeError("No sha3_256 in da.utils.hash")


def _encode_leaf() -> Callable[[int, bytes, int], bytes]:
    cmod = importlib.import_module("da.nmt.codec")
    for name in ("encode_leaf", "leaf_encode", "leaf_serialize"):
        if hasattr(cmod, name):
            fn = getattr(cmod, name)
            return lambda ns, data, ns_bytes=8, _fn=fn: _fn(ns, data, ns_bytes)
    raise RuntimeError("No encode_leaf in da.nmt.codec")


def _new_tree(ns_bytes: int = 8):
    tmod = importlib.import_module("da.nmt.tree")
    for cname in ("NMT", "NamespacedMerkleTree", "NmtTree", "Tree"):
        if hasattr(tmod, cname):
            cls = getattr(tmod, cname)
            try:
                return cls(ns_bytes=ns_bytes)  # type: ignore
            except TypeError:
                return cls()  # type: ignore
    raise RuntimeError("No NMT class in da.nmt.tree")


def _append_leaf(tree: Any, ns: int, data: bytes) -> None:
    for m in ("append", "add", "push", "insert"):
        if hasattr(tree, m):
            getattr(tree, m)(ns, data)
            return
    raise RuntimeError("Tree lacks append/add/push/insert")


def _finalize_root(tree: Any) -> bytes:
    for m in ("finalize", "get_root", "root"):
        if hasattr(tree, m):
            r = getattr(tree, m)
            out = r() if callable(r) else r
            return out if isinstance(out, (bytes, bytearray)) else bytes(out)
    raise RuntimeError("Tree lacks finalize/get_root/root")


def _build_inclusion_proof(tree: Any, index: int) -> Any:
    # Prefer tree methods
    for m in ("inclusion_proof", "get_inclusion_proof", "prove", "proof_for_index"):
        if hasattr(tree, m):
            return getattr(tree, m)(index)
    # Fallback to proofs module
    pmod = importlib.import_module("da.nmt.proofs")
    for name in ("build_inclusion", "inclusion", "inclusion_proof"):
        if hasattr(pmod, name):
            fn = getattr(pmod, name)
            return fn(tree, index)
    raise RuntimeError("No inclusion-proof builder found")


def _build_range_proof(tree: Any, ns_min: int, ns_max: int) -> Any:
    # Prefer tree methods
    for m in ("range_proof", "namespace_proof", "get_range_proof", "prove_range"):
        if hasattr(tree, m):
            return getattr(tree, m)(ns_min, ns_max)
    # Fallback to proofs module
    pmod = importlib.import_module("da.nmt.proofs")
    for name in ("build_range", "namespace_range", "range_proof"):
        if hasattr(pmod, name):
            fn = getattr(pmod, name)
            return fn(tree, ns_min, ns_max)
    raise RuntimeError("No range-proof builder found")


def _verify_inclusion(
    root: bytes, index: int, ns: int, data: bytes, proof: Any, ns_bytes: int
) -> bool:
    # Try verify module first
    for modname in ("da.nmt.verify", "da.nmt.proofs"):
        try:
            vmod = importlib.import_module(modname)
        except ModuleNotFoundError:
            continue
        for fname in (
            "verify_inclusion",
            "verify_inclusion_proof",
            "inclusion_verify",
            "verify_leaf",
        ):
            if hasattr(vmod, fname):
                fn = getattr(vmod, fname)
                sig = inspect.signature(fn)
                kwargs = {}
                # Best-effort argument mapping by parameter names
                for pname in sig.parameters:
                    if pname in ("root", "expected_root"):
                        kwargs[pname] = root
                    elif pname in ("index", "leaf_index", "i"):
                        kwargs[pname] = index
                    elif pname in ("ns", "namespace", "ns_id"):
                        kwargs[pname] = ns
                    elif pname in ("data", "payload", "leaf_data"):
                        kwargs[pname] = data
                    elif pname in ("proof", "inclusion_proof", "p"):
                        kwargs[pname] = proof
                    elif pname in ("ns_bytes", "namespace_bytes", "ns_width"):
                        kwargs[pname] = ns_bytes
                try:
                    out = fn(**kwargs)
                except TypeError:
                    # Try positional: root, index, ns, data, proof, ns_bytes
                    try:
                        out = fn(root, index, ns, data, proof, ns_bytes)
                    except Exception as e:
                        raise
                if isinstance(out, bool):
                    return out
                # some impls may return None/raise for bad proofs and truthy for ok
                return bool(out)
    raise RuntimeError("No inclusion verifier found")


def _verify_range(
    root: bytes, ns_min: int, ns_max: int, proof: Any, ns_bytes: int
) -> bool:
    for modname in ("da.nmt.verify", "da.nmt.proofs"):
        try:
            vmod = importlib.import_module(modname)
        except ModuleNotFoundError:
            continue
        for fname in (
            "verify_range",
            "verify_namespace_range",
            "range_verify",
            "verify_range_proof",
        ):
            if hasattr(vmod, fname):
                fn = getattr(vmod, fname)
                sig = inspect.signature(fn)
                kwargs = {}
                for pname in sig.parameters:
                    if pname in ("root", "expected_root"):
                        kwargs[pname] = root
                    elif pname in ("ns_min", "min_ns", "left_ns"):
                        kwargs[pname] = ns_min
                    elif pname in ("ns_max", "max_ns", "right_ns"):
                        kwargs[pname] = ns_max
                    elif pname in ("proof", "range_proof", "p"):
                        kwargs[pname] = proof
                    elif pname in ("ns_bytes", "namespace_bytes", "ns_width"):
                        kwargs[pname] = ns_bytes
                try:
                    out = fn(**kwargs)
                except TypeError:
                    try:
                        out = fn(root, ns_min, ns_max, proof, ns_bytes)
                    except Exception as e:
                        raise
                if isinstance(out, bool):
                    return out
                return bool(out)
    raise RuntimeError("No range verifier found")


def _corrupt_proof(proof: Any) -> Any:
    """Try to flip a bit in the first branch-like byte sequence we can find."""
    p = deepcopy(proof)
    # dict shape with 'branches'
    if isinstance(p, dict):
        for key in ("branches", "siblings", "path"):
            if key in p and isinstance(p[key], list) and p[key]:
                first = p[key][0]
                if isinstance(first, (bytes, bytearray)):
                    b = bytearray(first)
                    b[0] ^= 0x01
                    p[key][0] = bytes(b)
                    return p
                if isinstance(first, str) and first.startswith("0x"):
                    hx = bytearray(bytes.fromhex(first[2:]))
                    if not hx:
                        hx = bytearray(b"\x00")
                    hx[0] ^= 0x01
                    p[key][0] = "0x" + hx.hex()
                    return p
    # list of bytes
    if isinstance(p, list) and p and isinstance(p[0], (bytes, bytearray)):
        b = bytearray(p[0])
        b[0] ^= 0x01
        p[0] = bytes(b)
        return p
    # raw bytes
    if isinstance(p, (bytes, bytearray)):
        b = bytearray(p)
        if not b:
            b = bytearray(b"\x00")
        b[0] ^= 0x01
        return bytes(b)
    return p  # if we can't find a branch, return unchanged (the test will skip corruption assert)


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture
def sample_leaves() -> List[Tuple[int, bytes]]:
    # namespaces: 0x01 has two leaves, 0x03 has two, 0xFF has one
    return [
        (0x01, b"a"),
        (0x03, b"q"),
        (0x01, b"b"),
        (0xFF, b"z"),
        (0x03, b"r"),
    ]


@pytest.fixture
def built_tree(sample_leaves):
    tree = _new_tree(ns_bytes=8)
    for ns, data in sample_leaves:
        _append_leaf(tree, ns, data)
    root = _finalize_root(tree)
    return tree, root


# ---------------------------
# Tests — Inclusion proofs
# ---------------------------


def test_inclusion_proof_valid(built_tree, sample_leaves):
    tree, root = built_tree
    # choose a leaf in the middle to exercise non-trivial paths
    index = 2
    ns, data = sample_leaves[index]
    proof = _build_inclusion_proof(tree, index)
    assert _verify_inclusion(root, index, ns, data, proof, ns_bytes=8)


def test_inclusion_proof_rejects_tampering(built_tree, sample_leaves):
    tree, root = built_tree
    index = 1
    ns, data = sample_leaves[index]
    proof = _build_inclusion_proof(tree, index)
    bad = _corrupt_proof(proof)
    # If corruption couldn't be applied (structure unknown), skip to avoid false negative
    if bad == proof:
        pytest.skip(
            "Proof structure did not expose a branch to corrupt; skipping tamper test"
        )
    ok = _verify_inclusion(root, index, ns, data, proof, ns_bytes=8)
    bad_ok = _verify_inclusion(root, index, ns, data, bad, ns_bytes=8)
    assert ok is True
    assert bad_ok is False


def test_inclusion_proof_wrong_leaf_payload_fails(built_tree, sample_leaves):
    tree, root = built_tree
    index = 0
    ns, data = sample_leaves[index]
    proof = _build_inclusion_proof(tree, index)
    # mutate payload
    bad_data = data + b"!"
    assert _verify_inclusion(root, index, ns, data, proof, ns_bytes=8) is True
    assert _verify_inclusion(root, index, ns, bad_data, proof, ns_bytes=8) is False


def test_inclusion_proof_wrong_namespace_fails(built_tree, sample_leaves):
    tree, root = built_tree
    index = 4
    ns, data = sample_leaves[index]
    proof = _build_inclusion_proof(tree, index)
    wrong_ns = 0x02 if ns != 0x02 else 0x03
    assert _verify_inclusion(root, index, ns, data, proof, ns_bytes=8) is True
    assert _verify_inclusion(root, index, wrong_ns, data, proof, ns_bytes=8) is False


# ---------------------------
# Tests — Namespace range proofs
# ---------------------------


def test_namespace_range_proof_valid_single_ns(built_tree):
    tree, root = built_tree
    # range that selects exactly namespace 0x01
    ns_min = ns_max = 0x01
    proof = _build_range_proof(tree, ns_min, ns_max)
    assert _verify_range(root, ns_min, ns_max, proof, ns_bytes=8) is True


def test_namespace_range_proof_valid_span_two_ns(built_tree):
    tree, root = built_tree
    # span namespaces [0x01, 0x03]
    ns_min, ns_max = 0x01, 0x03
    proof = _build_range_proof(tree, ns_min, ns_max)
    assert _verify_range(root, ns_min, ns_max, proof, ns_bytes=8) is True


def test_namespace_range_proof_wrong_bounds_fail(built_tree):
    tree, root = built_tree
    # Build a proof for [0x01, 0x03] but verify against mismatched bounds
    proof = _build_range_proof(tree, 0x01, 0x03)
    assert _verify_range(root, 0x01, 0x03, proof, ns_bytes=8) is True
    assert _verify_range(root, 0x02, 0x03, proof, ns_bytes=8) is False
    assert _verify_range(root, 0x01, 0x02, proof, ns_bytes=8) is False


def test_namespace_range_proof_tamper_rejected(built_tree):
    tree, root = built_tree
    proof = _build_range_proof(tree, 0x01, 0xFF)
    bad = _corrupt_proof(proof)
    if bad == proof:
        pytest.skip(
            "Range proof structure did not expose a branch to corrupt; skipping tamper test"
        )
    assert _verify_range(root, 0x01, 0xFF, proof, ns_bytes=8) is True
    assert _verify_range(root, 0x01, 0xFF, bad, ns_bytes=8) is False
