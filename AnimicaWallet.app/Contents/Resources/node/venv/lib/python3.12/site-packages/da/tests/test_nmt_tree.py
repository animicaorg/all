import importlib
from typing import Callable, Iterable, List, Tuple

import pytest

# ---------------------------
# Helpers to tolerate minor API drift across modules we own.
# ---------------------------


def _hx(s: str) -> bytes:
    s = s.lower()
    if s.startswith("0x"):
        s = s[2:]
    return bytes.fromhex(s)


def _get_sha3_256() -> Callable[[bytes], bytes]:
    hmod = importlib.import_module("da.utils.hash")
    for name in ("sha3_256", "sha3_256_bytes", "hash_sha3_256"):
        if hasattr(hmod, name):
            fn = getattr(hmod, name)

            # normalize to return bytes
            def _wrap(b: bytes, _fn=fn):
                out = _fn(b)
                return out if isinstance(out, (bytes, bytearray)) else bytes(out)

            return _wrap
    raise RuntimeError("da.utils.hash missing sha3_256()")


def _get_encode_leaf() -> Callable[[int, bytes, int], bytes]:
    cmod = importlib.import_module("da.nmt.codec")
    for name in ("encode_leaf", "leaf_encode", "leaf_serialize"):
        if hasattr(cmod, name):
            fn = getattr(cmod, name)
            return lambda ns, data, ns_bytes=8, _fn=fn: _fn(ns, data, ns_bytes)
    raise RuntimeError(
        "da.nmt.codec missing encode_leaf() / leaf_encode() / leaf_serialize()"
    )


def _compute_root_via_commit(
    leaves: Iterable[Tuple[int, bytes]], ns_bytes: int
) -> bytes:
    """Try commit helpers; fall back to explicit tree if unavailable."""
    # commit module route
    try:
        cmod = importlib.import_module("da.nmt.commit")
        for name in ("nmt_root", "compute_root", "commit_root"):
            if hasattr(cmod, name):
                fn = getattr(cmod, name)
                root = (
                    fn(list(leaves), ns_bytes)
                    if fn.__code__.co_argcount >= 2
                    else fn(list(leaves))
                )
                return root if isinstance(root, (bytes, bytearray)) else bytes(root)
    except ModuleNotFoundError:
        pass

    # tree class route
    tmod = importlib.import_module("da.nmt.tree")
    cls = None
    for cname in ("NMT", "NamespacedMerkleTree", "NmtTree", "Tree"):
        if hasattr(tmod, cname):
            cls = getattr(tmod, cname)
            break
    if cls is None:
        raise RuntimeError("No NMT tree class found in da.nmt.tree")

    # construct with ns width if supported
    try:
        tree = cls(ns_bytes=ns_bytes)  # type: ignore[call-arg]
    except TypeError:
        tree = cls()  # type: ignore[call-arg]

    # append leaves (method name variants)
    append = (
        getattr(tree, "append", None)
        or getattr(tree, "add", None)
        or getattr(tree, "push", None)
    )
    if append is None:
        raise RuntimeError("No append/add/push on NMT tree")

    for ns, data in leaves:
        append(ns, data)

    # finalize root (method name variants)
    for rname in ("finalize", "get_root", "root"):
        if hasattr(tree, rname):
            r = getattr(tree, rname)
            root = r() if callable(r) else r
            return root if isinstance(root, (bytes, bytearray)) else bytes(root)

    raise RuntimeError("No finalize/get_root/root on NMT tree")


def _compute_root(leaves: Iterable[Tuple[int, bytes]], ns_bytes: int = 8) -> bytes:
    return _compute_root_via_commit(leaves, ns_bytes)


# ---------------------------
# Tests
# ---------------------------


def test_single_leaf_root_equals_leaf_hash():
    sha3_256 = _get_sha3_256()
    enc_leaf = _get_encode_leaf()

    ns = 0xAA
    data = _hx("0x01ff")
    ns_bytes = 8

    leaf_enc = enc_leaf(ns, data, ns_bytes)
    expected = sha3_256(leaf_enc)

    root = _compute_root([(ns, data)], ns_bytes)
    assert isinstance(root, (bytes, bytearray)) and len(root) == 32
    assert (
        root == expected
    ), "Single-leaf NMT root must equal hash(encode_leaf(ns,data))"


def test_ordering_independent_across_distinct_namespaces():
    ns_bytes = 8
    a = (0x01, b"hello")
    b = (0xFF, b"world")

    root1 = _compute_root([a, b], ns_bytes)
    root2 = _compute_root([b, a], ns_bytes)

    assert (
        root1 == root2
    ), "Reordering different namespaces must not change NMT root (sort by ns)"


def test_relative_order_matters_within_same_namespace():
    ns_bytes = 8
    leaves1 = [(0x01, b"a"), (0x01, b"b"), (0x01, b"c")]
    leaves2 = [(0x01, b"a"), (0x01, b"c"), (0x01, b"b")]  # swap within same ns

    r1 = _compute_root(leaves1, ns_bytes)
    r2 = _compute_root(leaves2, ns_bytes)

    assert (
        r1 != r2
    ), "Within the same namespace, original relative order must be preserved (stable sort)"


def test_mixed_namespaces_stable_sort_contract():
    """Mix same-ns and different-ns leaves; root must equal root of stably sorted list."""
    ns_bytes = 8
    leaves_unsorted: List[Tuple[int, bytes]] = [
        (0x05, b"x"),
        (0x01, b"a"),
        (0x03, b"q"),
        (0x01, b"b"),
        (0x03, b"r"),
    ]
    # Stable sort by (ns, original-order)
    order = sorted(
        range(len(leaves_unsorted)), key=lambda i: (leaves_unsorted[i][0], i)
    )
    leaves_sorted = [leaves_unsorted[i] for i in order]

    r_unsorted = _compute_root(leaves_unsorted, ns_bytes)
    r_sorted = _compute_root(leaves_sorted, ns_bytes)

    assert (
        r_unsorted == r_sorted
    ), "Implementation must behave like stable sort by (namespace, original index)"


def test_multiple_leaves_nonzero_root_length_and_type():
    ns_bytes = 8
    leaves = [(0x01, b"aa"), (0x02, b"bb"), (0x03, b"cc"), (0x03, b"dd")]
    root = _compute_root(leaves, ns_bytes)
    assert isinstance(root, (bytes, bytearray))
    assert len(root) == 32, "NMT root must be 32-byte hash"
    # nontrivial sanity: roots should differ if we mutate a payload
    root2 = _compute_root(
        [
            (ns, (data + b"!") if i == 2 else data)
            for i, (ns, data) in enumerate(leaves)
        ],
        ns_bytes,
    )
    assert root != root2, "Change in a leaf payload must change NMT root"
