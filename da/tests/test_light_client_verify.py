import importlib
import inspect
import os
import random
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pytest


# =========================================
# Helpers: robust adapters across APIs
# =========================================

def _import(path: str):
    return importlib.import_module(path)


def _as_bytes(x: Union[bytes, bytearray, str, int]) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        s = x[2:] if x.startswith("0x") else x
        try:
            return bytes.fromhex(s)
        except ValueError:
            return s.encode("utf-8")
    if isinstance(x, int):
        # encode as big-endian with minimal length
        if x == 0:
            return b"\x00"
        out = bytearray()
        v = x
        while v:
            out.append(v & 0xFF)
            v >>= 8
        return bytes(reversed(out))
    raise TypeError(f"cannot coerce {type(x)} to bytes")


def _rnd_bytes(n: int, seed: int) -> bytes:
    random.seed(seed)
    return bytes(random.getrandbits(8) for _ in range(n))


# =========================================
# Locate building blocks
# =========================================

def _encode_leaves_fn() -> Callable[[bytes, int], List[bytes]]:
    """
    Returns a function (data: bytes, ns: int) -> List[bytes] encoding the blob into namespaced leaves.
    """
    # Preferred module
    try_mods = ["da.erasure.encoder", "da.erasure.partitioner"]
    for mname in try_mods:
        try:
            mod = _import(mname)
        except ModuleNotFoundError:
            continue
        for name in ("encode_leaves", "blob_to_leaves", "encode", "build_leaves", "partition"):
            if hasattr(mod, name):
                fn = getattr(mod, name)
                if callable(fn):
                    def _wrap(data: bytes, ns: int, _fn=fn):
                        try:
                            return list(_fn(data, ns))
                        except TypeError:
                            # Some APIs embed ns elsewhere; try single-arg
                            return list(_fn(data))
                    return _wrap
    raise RuntimeError("Could not find leaves encoder in da.erasure.encoder|partitioner")


def _nmt_commit_fn() -> Callable[[Sequence[bytes]], bytes]:
    mod = _import("da.nmt.commit")
    for name in ("commit", "compute_root", "nmt_root"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            if callable(fn):
                return lambda leaves, _fn=fn: _as_bytes(_fn(list(leaves)))
    raise RuntimeError("No NMT commit() function found in da.nmt.commit")


def _build_tree_fn() -> Optional[Callable[[Sequence[bytes]], Any]]:
    """Return a function that builds a tree and returns an object with a proof method, or None."""
    try:
        mod = _import("da.nmt.tree")
    except ModuleNotFoundError:
        return None

    # Class-based builders
    for cname in ("Tree", "NMT", "NamespacedMerkleTree"):
        if hasattr(mod, cname):
            cls = getattr(mod, cname)
            def _builder(leaves: Sequence[bytes], _cls=cls):
                try:
                    # Try common constructors/constructors + append/finalize
                    try:
                        t = _cls(leaves)
                        return t
                    except Exception:
                        t = _cls()
                        for leaf in leaves:
                            for meth in ("append_leaf", "append", "add"):
                                if hasattr(t, meth):
                                    getattr(t, meth)(leaf)
                                    break
                        for fin in ("finalize", "seal", "build"):
                            if hasattr(t, fin):
                                getattr(t, fin)()
                        return t
                except Exception as e:
                    raise
            return _builder

    # Functional builders
    for name in ("build", "from_leaves", "make"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            if callable(fn):
                return lambda leaves, _fn=fn: _fn(list(leaves))

    return None


def _inclusion_proof_builder(leaves: Sequence[bytes]) -> Callable[[int], Any]:
    """
    Return a function idx -> proof object.
    Tries da.nmt.proofs first; falls back to tree.prove_*.
    """
    # Direct proofs module
    try:
        pmod = _import("da.nmt.proofs")
        for bulk in ("build_inclusion_proofs", "inclusion_proofs", "prove_inclusion_set", "proofs_for_indices"):
            if hasattr(pmod, bulk):
                bulk_fn = getattr(pmod, bulk)
                if callable(bulk_fn):
                    def _bulk(idx: int, _fn=bulk_fn):
                        out = _fn(leaves, [idx])
                        # Normalize single-proof returns
                        if isinstance(out, (list, tuple)) and len(out) == 1:
                            return out[0]
                        return out
                    return _bulk
        for single in ("build_inclusion_proof", "inclusion_proof", "prove_inclusion", "proof_for_index"):
            if hasattr(pmod, single):
                single_fn = getattr(pmod, single)
                if callable(single_fn):
                    return lambda idx, _fn=single_fn: _fn(leaves, idx)
    except ModuleNotFoundError:
        pass

    # Tree-based
    t_builder = _build_tree_fn()
    if t_builder is None:
        raise RuntimeError("No proof builder available (neither da.nmt.proofs nor da.nmt.tree)")

    tree = t_builder(leaves)
    for meth in ("prove_inclusion", "inclusion_proof", "get_inclusion_proof", "prove"):
        if hasattr(tree, meth):
            return lambda idx, _meth=meth, _tree=tree: getattr(_tree, _meth)(idx)

    raise RuntimeError("Tree object does not expose an inclusion-proof method")


def _sample_indices_fn() -> Callable[[int, int, int], List[int]]:
    """
    Return a function (n_leaves, s, seed) -> distinct indices.
    Prefer da.sampling.queries plans; fallback to random unique picks.
    """
    try:
        qmod = _import("da.sampling.queries")
        for nm in ("uniform_indices", "uniform", "plan_uniform_indices", "build_uniform"):
            if hasattr(qmod, nm):
                qf = getattr(qmod, nm)
                if callable(qf):
                    def _wrap(n_leaves: int, s: int, seed: int, _fn=qf):
                        try:
                            out = _fn(n=n_leaves, samples=s, seed=seed)
                        except TypeError:
                            out = _fn(n_leaves, s, seed)
                        return list(dict.fromkeys(int(i) for i in out if 0 <= int(i) < n_leaves))  # dedupe, clamp
                    return _wrap
    except ModuleNotFoundError:
        pass

    def _fallback(n_leaves: int, s: int, seed: int) -> List[int]:
        random.seed(seed)
        s = min(s, n_leaves)
        return random.sample(range(n_leaves), s) if s > 0 else []
    return _fallback


def _lc_verify_fn() -> Optional[Callable[..., bool]]:
    """
    Find a light-client verify function. Prefer da.sampling.light_client, then da.sampling.verifier.
    The returned callable should accept (root, samples) or (header, samples); we'll adapt at call time.
    """
    for mname in ("da.sampling.light_client", "da.sampling.verifier"):
        try:
            mod = _import(mname)
        except ModuleNotFoundError:
            continue
        for nm in ("verify_availability", "verify", "verify_samples", "check"):
            if hasattr(mod, nm):
                fn = getattr(mod, nm)
                if callable(fn):
                    return fn
    return None


# =========================================
# Packing samples for verifiers
# =========================================

def _pack_sample(index: int, leaf: bytes, proof: Any) -> Any:
    """
    Build a sample object in a variety of shapes likely to be accepted:
      - dict with keys: index, leaf, proof
      - tuple (index, leaf, proof)
    We'll return a dict; the call adapter will repack as needed.
    """
    return {"index": int(index), "leaf": _as_bytes(leaf), "proof": proof}


def _call_verify(fn: Callable[..., bool], root: bytes, samples: List[Any]) -> bool:
    """
    Try a few call patterns:
      (root, samples)
      (header_dict, samples) with several key aliases for da_root
      keywords
      Provide tuple-shaped samples if dicts fail.
    """
    # First attempt: root + samples (positional/keywords)
    try:
        return bool(fn(root, samples))
    except TypeError:
        pass
    try:
        return bool(fn(root=root, samples=samples))
    except TypeError:
        pass

    # Header-shaped argument
    header_variants = [
        {"da_root": root},
        {"daRoot": root},
        {"roots": {"da": root}},
        {"header": {"da_root": root}},
    ]
    for hdr in header_variants:
        try:
            return bool(fn(hdr, samples))
        except TypeError:
            try:
                return bool(fn(header=hdr, samples=samples))
            except TypeError:
                continue

    # Repack samples as tuples
    tuple_samples = []
    for s in samples:
        if isinstance(s, dict):
            tuple_samples.append((s.get("index"), s.get("leaf"), s.get("proof")))
        else:
            tuple_samples.append(s)

    try:
        return bool(fn(root, tuple_samples))
    except TypeError:
        pass
    for hdr in header_variants:
        try:
            return bool(fn(hdr, tuple_samples))
        except TypeError:
            continue

    # Last attempt: named params with other common names
    try:
        return bool(fn(da_root=root, samples=samples))
    except TypeError:
        pass

    raise


# =========================================
# Test data
# =========================================

DATA = _rnd_bytes(4 * 1024, seed=20250921)
NAMESPACE = 24
SAMPLE_SIZE = 32
SEED = 1337


# =========================================
# Tests
# =========================================

@pytest.mark.parametrize("tamper_mode", ["none", "leaf", "root"])
def test_light_client_verify_true_and_false(tamper_mode: str):
    """
    Build leaves -> NMT root, select sample indices, build inclusion proofs,
    then run the light-client verifier. With correct inputs it must return True.
    If we tamper either a leaf (keeping proof) or the header root, it must return False.
    """
    # Required components (skip if not present yet)
    try:
        encode_leaves = _encode_leaves_fn()
        nmt_commit = _nmt_commit_fn()
        sample_indices = _sample_indices_fn()
        lc_verify = _lc_verify_fn()
    except RuntimeError as e:
        pytest.skip(str(e))
    if lc_verify is None:
        pytest.skip("No light-client verify function found in da.sampling.light_client|verifier")

    leaves = encode_leaves(DATA, NAMESPACE)
    assert isinstance(leaves, list) and len(leaves) > 0, "encoder must return non-empty leaf list"

    root = _as_bytes(nmt_commit(leaves))
    assert isinstance(root, (bytes, bytearray)) and len(root) >= 16

    s = min(SAMPLE_SIZE, len(leaves))
    indices = sample_indices(len(leaves), s, SEED)
    assert len(indices) == s and len(set(indices)) == s

    # Build per-index inclusion proofs
    build_proof_for = _inclusion_proof_builder(leaves)
    samples: List[Any] = []
    for idx in indices:
        proof = build_proof_for(idx)
        samples.append(_pack_sample(idx, leaves[idx], proof))

    # Tamper according to mode
    tampered_root = bytes(root)
    if tamper_mode == "leaf":
        # Flip one byte in the first sample leaf
        if samples:
            leaf0 = bytearray(samples[0]["leaf"])
            leaf0[0] ^= 0xFF
            samples[0]["leaf"] = bytes(leaf0)
    elif tamper_mode == "root":
        r = bytearray(root)
        r[0] ^= 0x01
        tampered_root = bytes(r)

    # Verify
    ok = _call_verify(lc_verify, tampered_root, samples)

    if tamper_mode == "none":
        assert ok is True, "valid samples under correct root must verify True"
    else:
        assert ok is False, f"tampering ({tamper_mode}) must make verification fail"


def test_light_client_rejects_index_out_of_range():
    encode_leaves = _encode_leaves_fn()
    nmt_commit = _nmt_commit_fn()
    lc_verify = _lc_verify_fn()
    if lc_verify is None:
        pytest.skip("No light-client verify function found")

    leaves = encode_leaves(DATA, NAMESPACE)
    root = _as_bytes(nmt_commit(leaves))

    # Build a single correct sample
    build_proof_for = _inclusion_proof_builder(leaves)
    if not leaves:
        pytest.skip("No leaves produced")
    idx = 0
    proof = build_proof_for(idx)
    good_sample = _pack_sample(idx, leaves[idx], proof)

    # Copy and make an out-of-range index with same proof (should be rejected)
    bad_sample = dict(good_sample)
    bad_sample["index"] = len(leaves) + 5

    ok_good = _call_verify(lc_verify, root, [good_sample])
    ok_bad = _call_verify(lc_verify, root, [bad_sample])

    assert ok_good is True, "sanity: a single valid sample should verify True"
    assert ok_bad is False, "out-of-range index must be rejected"


