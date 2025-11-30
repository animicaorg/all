import dataclasses
from typing import Any

import pytest

# The registry is the single source of truth for algorithm metadata.
from pq.py import registry as R


def _collect_algs():
    """
    Gather all algorithm infos (both signature and KEM) from the registry,
    regardless of the exact public API shape. This keeps the test resilient
    if you extend the registry structure later.
    """
    algs = []

    # Preferred: explicit registries
    for name in ("SIGNATURES", "SIG_ALGS", "SIGS"):
        if hasattr(R, name):
            d = getattr(R, name)
            if isinstance(d, dict):
                algs.extend(list(d.values()))

    for name in ("KEMS", "KEM_ALGS"):
        if hasattr(R, name):
            d = getattr(R, name)
            if isinstance(d, dict):
                algs.extend(list(d.values()))

    # Fallback: search any dicts whose values look like @dataclass alg infos
    if not algs:
        for attr in dir(R):
            obj = getattr(R, attr)
            if isinstance(obj, dict):
                for v in obj.values():
                    if hasattr(v, "name") and hasattr(v, "alg_id"):
                        algs.append(v)

    # Final assert to ensure we found something meaningful
    assert algs, "No algorithms discovered in pq.py.registry"
    return algs


def _by_name() -> dict[str, Any]:
    # Preferred mappings
    for name in ("BY_NAME", "ALG_BY_NAME", "MAP_BY_NAME"):
        if hasattr(R, name):
            d = getattr(R, name)
            if isinstance(d, dict) and d:
                return d
    # Build from collected algs
    m = {}
    for info in _collect_algs():
        m[getattr(info, "name")] = info
    return m


def _by_id() -> dict[int, Any]:
    for name in ("BY_ID", "ALG_BY_ID", "MAP_BY_ID"):
        if hasattr(R, name):
            d = getattr(R, name)
            if isinstance(d, dict) and d:
                return d
    m = {}
    for info in _collect_algs():
        m[int(getattr(info, "alg_id"))] = info
    return m


def _get(name: str) -> Any:
    """
    Resolve an algorithm by name using common registry helpers or fallbacks.
    """
    # Try helpers if present
    for fn in ("get", "get_alg", "get_signature_alg", "get_kem_alg"):
        if hasattr(R, fn):
            try:
                val = getattr(R, fn)(name)  # type: ignore
                if val is not None:
                    return val
            except TypeError:
                # Helper might accept ids only; ignore
                pass

    # Map lookups
    if name in _by_name():
        return _by_name()[name]

    # As a last resort, scan
    for info in _collect_algs():
        if getattr(info, "name") == name:
            return info

    raise KeyError(name)


def _is_sig(info: Any) -> bool:
    return any(
        hasattr(info, f) for f in ("sig_len", "signature_len", "signature_bytes")
    )


def _is_kem(info: Any) -> bool:
    return all(hasattr(info, f) for f in ("ct_len", "ss_len"))


def test_alg_ids_unique_and_names_unique():
    algs = _collect_algs()
    ids = [int(getattr(a, "alg_id")) for a in algs]
    names = [str(getattr(a, "name")) for a in algs]

    assert len(ids) == len(set(ids)), "alg_id values must be unique"
    assert len(names) == len(set(names)), "algorithm names must be unique"

    # Ensure mapping round-trips are consistent
    bid = _by_id()
    bname = _by_name()
    for a in algs:
        assert bid[int(a.alg_id)].name == a.name
        assert bname[a.name].alg_id == a.alg_id


def test_known_signature_alg_sizes():
    # Expected sizes (bytes) for the selected NIST finalists/standards
    # Dilithium3
    d3 = _get("dilithium3")
    assert _is_sig(d3), "dilithium3 should be a signature algorithm"
    assert getattr(d3, "pk_len") == 1952
    assert getattr(d3, "sk_len") in (4000, 4032)  # impls vary on CRYPTO_SECRETKEYBYTES
    assert getattr(d3, "sig_len") == 3293

    # SPHINCS+ SHAKE-128s
    sp = _get("sphincs_shake_128s")
    assert _is_sig(sp), "sphincs_shake_128s should be a signature algorithm"
    assert getattr(sp, "pk_len") == 32
    assert getattr(sp, "sk_len") == 64
    # NIST "128s" signature size:
    assert getattr(sp, "sig_len") == 7856


def test_known_kem_sizes():
    ky = _get("kyber768")
    assert _is_kem(ky), "kyber768 should be a KEM algorithm"
    assert getattr(ky, "pk_len") == 1184
    assert getattr(ky, "sk_len") in (2400, 2400)  # tolerant if alias present
    assert getattr(ky, "ct_len") == 1088
    assert getattr(ky, "ss_len") == 32


def test_lookup_consistency_by_id_and_name():
    # For a few known names, ensure BY_ID <-> BY_NAME are consistent
    bname = _by_name()
    bid = _by_id()

    for name in ("dilithium3", "sphincs_shake_128s", "kyber768"):
        info = _get(name)
        assert bname[name].alg_id == info.alg_id
        # Find it in the id map too
        assert bid[int(info.alg_id)].name == name


def test_dataclass_like_shape():
    """
    Sanity: ensure registry entries are simple dataclass-like objects
    with the basic fields we rely on elsewhere.
    """
    for info in _collect_algs():
        assert hasattr(info, "name")
        assert hasattr(info, "alg_id")
        assert hasattr(info, "pk_len")
        assert hasattr(info, "sk_len")
        # Entries should be immutable-ish dataclasses or simple objects.
        assert not isinstance(info, dict)
        # If it's a real dataclass, fields should be hashable.
        if dataclasses.is_dataclass(info):  # pragma: no branch
            tuple(getattr(info, f.name) for f in dataclasses.fields(info))  # no crash
