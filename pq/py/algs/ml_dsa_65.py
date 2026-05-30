"""ML-DSA-65 (real FIPS 204) signature backend.

Wraps `animica._vendor.dilithium_py_v2` (vendored copy of jack4818/dilithium-py,
MIT). Exposes the chain's standard backend interface:

    sizes: {"pk", "sk", "sig"}
    keypair(seed: bytes|None) -> (sk, pk)
    sign(sk: bytes, msg: bytes) -> sig
    verify(pk: bytes, msg: bytes, sig: bytes) -> bool

Sizes per FIPS 204:
    pk = 1952 bytes
    sk = 4032 bytes
    sig = 3309 bytes
"""

from __future__ import annotations

from typing import Optional, Tuple

# Vendored, pure-Python ML-DSA-65 reference. The vendored copy lives at
# python/animica/_vendor/dilithium_py_v2/. Imported lazily so missing
# vendor files surface as `is_available() == False` rather than as a
# hard import error.
_PURE_PYTHON_OK = False
sizes = {"pk": 1952, "sk": 4032, "sig": 3309}

try:  # pragma: no cover - import-time dep gate
    from animica._vendor.dilithium_py_v2.ml_dsa import ML_DSA_65 as _Impl

    _PURE_PYTHON_OK = True
except Exception:
    _Impl = None  # type: ignore


def is_available() -> bool:
    return _PURE_PYTHON_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Generate an ML-DSA-65 keypair. Returns (sk, pk) for parity with
    the rest of pq.py.algs backends (note: dilithium-py's native
    `ML_DSA_65.keygen()` returns (pk, sk), so we swap)."""
    if not _PURE_PYTHON_OK:
        raise NotImplementedError(
            "ML-DSA-65 reference impl unavailable. Check that "
            "python/animica/_vendor/dilithium_py_v2/ ships with the package."
        )
    # The reference takes a 32-byte ksi seed via `keygen_internal`; the
    # public `keygen()` uses os.urandom internally. To get deterministic
    # behavior when callers pass a seed (mostly tests + KAT vectors) we
    # use the internal entry. With no seed, we fall back to the random
    # public API so a fresh wallet really is fresh.
    if seed is None:
        pk, sk = _Impl.keygen()
    else:
        if len(seed) != 32:
            raise ValueError("ML-DSA-65 seed must be 32 bytes")
        pk, sk = _Impl._keygen_internal(seed)  # type: ignore[attr-defined]
    return sk, pk


def sign(sk: bytes, msg: bytes) -> bytes:
    """Sign `msg` with `sk`. Random per-signature nonce (hedged)."""
    if not _PURE_PYTHON_OK:
        raise NotImplementedError("ML-DSA-65 reference impl unavailable.")
    return _Impl.sign(sk, msg)


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    """Verify a signature. Returns False for any failure (length,
    out-of-range coefficients, hash mismatch) rather than raising."""
    if not _PURE_PYTHON_OK:
        return False
    try:
        return bool(_Impl.verify(pk, msg, sig))
    except Exception:
        return False


# Backwards-compatible aliases — some code paths reach in via these names.
def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """Compatibility wrapper returning (pk, sk)."""
    sk, pk = keypair(seed)
    return (pk, sk)
