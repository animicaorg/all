from __future__ import annotations

"""
Animica PQ: Kyber768 (a.k.a. ML-KEM-768) KEM wrappers.

Priority of implementations:
  1) python-oqs (Open Quantum Safe) — production-capable if installed
  2) Explicitly opted-in DEV-ONLY fallback (ANIMICA_UNSAFE_PQ_FAKE=1), which is NOT secure
     and exists purely to let the rest of the stack run on machines without liboqs.
     Do not use on real networks.

Uniform surface exposed to higher layers (see pq/py/algs/__init__.py):
  - sizes: dict {"pk","sk","ct","ss"} (ints)
  - is_available() -> bool
  - keypair(seed: bytes|None) -> (sk: bytes, pk: bytes)
  - encapsulate(pk: bytes) -> (ct: bytes, ss: bytes)
  - decapsulate(sk: bytes, ct: bytes) -> ss: bytes
"""

import os
from typing import Optional, Tuple, Dict

# --------------------------------------------------------------------------------------
# Try python-oqs first
# --------------------------------------------------------------------------------------
_OQS_OK = False
_OQS_MECH: Optional[str] = None
_sizes: Dict[str, int] = {"pk": 0, "sk": 0, "ct": 0, "ss": 0}

# liboqs/python-oqs has gone through naming tweaks; try several
_POSSIBLE_MECH_NAMES = [
    "Kyber768",
    "Kyber-768",
    "Kyber768-90s",
    "ML-KEM-768",
    "ML-KEM-768-90s",
]

try:
    import oqs  # type: ignore

    enabled = set(getattr(oqs, "get_enabled_kem_mechanisms", lambda: [])())
    for nm in _POSSIBLE_MECH_NAMES:
        if nm in enabled:
            _OQS_MECH = nm
            break
    if _OQS_MECH:
        with oqs.KEM(_OQS_MECH) as _probe:  # type: ignore[arg-type]
            _sizes = {
                "pk": _probe.length_public_key,      # type: ignore[attr-defined]
                "sk": _probe.length_secret_key,      # type: ignore[attr-defined]
                "ct": _probe.length_ciphertext,      # type: ignore[attr-defined]
                "ss": _probe.length_shared_secret,   # type: ignore[attr-defined]
            }
        _OQS_OK = True
except Exception:
    _OQS_OK = False
    _OQS_MECH = None

# --------------------------------------------------------------------------------------
# Unsafe dev fallback (only if explicitly enabled)
# --------------------------------------------------------------------------------------
_DEV_FAKE_OK = False
if not _OQS_OK and os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
    _DEV_FAKE_OK = True
    # Chosen arbitrarily for local-only operation
    _sizes = {"pk": 32, "sk": 32, "ct": 48, "ss": 32}

# Local SHA3 helpers for the fake mode (avoid importing our higher-level utils here)
def _sha3_256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_256(data).digest()


def _sha3_512(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha3_512(data).digest()


# --------------------------------------------------------------------------------------
# Helpers to cope with python-oqs API differences
# --------------------------------------------------------------------------------------
def _oqs_encapsulate(kem_obj, pk: bytes) -> Tuple[bytes, bytes]:  # pragma: no cover - tiny shim
    """
    python-oqs historically used 'encap_secret'/'decap_secret'. Some builds expose
    'encapsulate'/'decapsulate'. Try both.
    """
    if hasattr(kem_obj, "encap_secret"):
        return kem_obj.encap_secret(pk)  # type: ignore[attr-defined]
    if hasattr(kem_obj, "encapsulate"):
        return kem_obj.encapsulate(pk)  # type: ignore[attr-defined]
    raise RuntimeError("oqs.KEM has neither encap_secret nor encapsulate")


def _oqs_decapsulate(kem_obj, ct: bytes) -> bytes:  # pragma: no cover - tiny shim
    if hasattr(kem_obj, "decap_secret"):
        return kem_obj.decap_secret(ct)  # type: ignore[attr-defined]
    if hasattr(kem_obj, "decapsulate"):
        return kem_obj.decapsulate(ct)  # type: ignore[attr-defined]
    raise RuntimeError("oqs.KEM has neither decap_secret nor decapsulate")


# --------------------------------------------------------------------------------------
# Public API (uniform)
# --------------------------------------------------------------------------------------
sizes = _sizes.copy()


def is_available() -> bool:
    """
    Return True if a working Kyber768/ML-KEM-768 implementation is available.
    True when python-oqs exposes a compatible mechanism, or when the explicit
    ANIMICA_UNSAFE_PQ_FAKE=1 dev-only fallback is enabled.
    """
    return _OQS_OK or _DEV_FAKE_OK


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    """
    Generate a Kyber768 keypair.

    Notes:
      • With python-oqs, RNG is internal; the optional seed is ignored.
      • In DEV-ONLY fallback, we derive (sk, pk) deterministically from seed or OS RNG.
    """
    if _OQS_OK and _OQS_MECH:
        with oqs.KEM(_OQS_MECH) as kem:  # type: ignore[arg-type]
            pk = kem.generate_keypair()
            sk = kem.export_secret_key()
            return (sk, pk)

    if _DEV_FAKE_OK:
        if seed is None:
            seed = os.urandom(32)
        sk = _sha3_256(b"animica-dev-fake-kyber-sk|" + seed)
        pk = _sha3_256(b"animica-dev-fake-kyber-pk|" + sk)
        return (sk, pk)

    raise NotImplementedError(
        "Kyber768 unavailable. Install python-oqs/liboqs or set ANIMICA_UNSAFE_PQ_FAKE=1 (DEV-ONLY)"
    )


def encapsulate(pk: bytes) -> Tuple[bytes, bytes]:
    """
    Encapsulate to a public key, returning (ciphertext, shared_secret).
    """
    if _OQS_OK and _OQS_MECH:
        with oqs.KEM(_OQS_MECH) as kem:  # type: ignore[arg-type]
            return _oqs_encapsulate(kem, pk)

    if _DEV_FAKE_OK:
        # ct = H("ct"|pk|e)[:32] || e ; ss = H("ss"|pk|e)
        e = os.urandom(16)
        ct = _sha3_256(b"animica-dev-fake-kyber-ct|" + pk + e)[:32] + e
        ss = _sha3_256(b"animica-dev-fake-kyber-ss|" + pk + e)
        return (ct, ss)

    raise NotImplementedError(
        "Kyber768 unavailable. Install python-oqs/liboqs or enable ANIMICA_UNSAFE_PQ_FAKE=1 for local dev."
    )


def decapsulate(sk: bytes, ct: bytes) -> bytes:
    """
    Decapsulate a ciphertext with the given secret key, returning shared_secret.
    """
    if _OQS_OK and _OQS_MECH:
        with oqs.KEM(_OQS_MECH, secret_key=sk) as kem:  # type: ignore[arg-type]
            return _oqs_decapsulate(kem, ct)

    if _DEV_FAKE_OK:
        # Recover e from the tail of ct (fake format), derive pk from sk, recompute ss.
        if len(ct) < 16:
            return b""
        e = ct[-16:]
        pk = _sha3_256(b"animica-dev-fake-kyber-pk|" + sk)
        ss = _sha3_256(b"animica-dev-fake-kyber-ss|" + pk + e)
        return ss

    raise NotImplementedError(
        "Kyber768 unavailable. Install python-oqs/liboqs or enable ANIMICA_UNSAFE_PQ_FAKE=1 for local dev."
    )


# --------------------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("[kyber768] available:", is_available(), "sizes:", sizes, "mech:", _OQS_MECH)
    try:
        sk_a, pk_a = keypair()
        ct, ss_b = encapsulate(pk_a)
        ss_a = decapsulate(sk_a, ct)
        print("shared_secret match:", ss_a == ss_b, "len:", len(ss_a))
        # Basic negative test
        if len(ct) > 0:
            ct_bad = ct[:-1] + bytes([ct[-1] ^ 0x01])
            ss_bad = decapsulate(sk_a, ct_bad)
            print("decap with bad ct equal to good ss:", ss_bad == ss_b)
    except NotImplementedError as e:
        print(str(e))
