from __future__ import annotations
"""
Pure-Python *educational* fallbacks for PQ primitives (DO NOT USE IN PRODUCTION).

This module exists so Animica devnets/tests can run without native PQ libs
(`python-oqs` or a shared `liboqs`). The constructions below are NOT secure.
They merely mimic the *shape* (key/sig/ct/ss lengths, API) of:

  • Dilithium3 (signature)
  • SPHINCS+-SHAKE-128s (signature)
  • Kyber/ML-KEM-768 (KEM)

Hard gate: You must explicitly opt-in by setting:
    ANIMICA_ALLOW_PQ_PURE_FALLBACK=1

If the env var is absent, every call raises NotImplementedError so that insecure
fallbacks never slip into real deployments.

Design sketch (intentionally trivial & forgeable):
  Signatures:
    - sk: random bytes of the target secret-key length
    - pk = H("pk"|sk) truncated/expanded to target pk length
    - sig = XOF("sig"|pk|msg) to the algorithm's signature length
    - verify(msg, sig, pk): recompute XOF("sig"|pk|msg) and compare
  KEM:
    - sk: random bytes, pk = H("pk"|sk) to target pk length
    - encaps(pk): pick eph=R32; ct = "ct"|eph|H(pk|eph) padded/cropped to ct_len
                  ss = H("ss"|pk|eph) to 32 bytes (ML-KEM shared-secret length)
    - decaps(sk, ct): derive pk' = H("pk"|sk); parse eph from ct; ss = H("ss"|pk'|eph)

Hash/XOF:
  - SHA3-256 / SHA3-512 for binding
  - SHAKE-256 to length for pk/sig/ct material

Again: these functions are **for tests/devnets only**. They provide determinism,
length-compatibility, and simple round-trip semantics—but zero cryptographic security.
"""

import os
import hmac
import hashlib
from dataclasses import dataclass
from typing import Tuple

# Local utility wrappers (sha3_256/512) if available in repo; fall back to hashlib.
try:
    from ..utils.hash import sha3_256 as _sha3_256, sha3_512 as _sha3_512
except Exception:  # pragma: no cover
    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

    def _sha3_512(data: bytes) -> bytes:
        return hashlib.sha3_512(data).digest()


# --------------------------------------------------------------------------------------
# Feature gate
# --------------------------------------------------------------------------------------

def _check_allowed() -> None:
    if os.environ.get("ANIMICA_ALLOW_PQ_PURE_FALLBACK") != "1":
        raise NotImplementedError(
            "Pure-Python PQ fallbacks are disabled. "
            "Set ANIMICA_ALLOW_PQ_PURE_FALLBACK=1 to enable *insecure* educational fallbacks."
        )


# --------------------------------------------------------------------------------------
# Length tables (approximate canonical sizes for these algs)
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SigLens:
    pk: int
    sk: int
    sig: int

@dataclass(frozen=True)
class KemLens:
    pk: int
    sk: int
    ct: int
    ss: int

DILITHIUM3 = SigLens(pk=1952, sk=4000, sig=3293)          # typical liboqs sizes
SPHINCS_SHAKE_128S = SigLens(pk=32, sk=64, sig=7856)       # "simple" variant
ML_KEM_768 = KemLens(pk=1184, sk=2400, ct=1088, ss=32)     # Kyber/ML-KEM-768

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _xof_shake256(tag: bytes, *parts: bytes, out_len: int) -> bytes:
    """SHAKE-256-based XOF with domain tag."""
    sh = hashlib.shake_256()
    sh.update(tag)
    for p in parts:
        sh.update(len(p).to_bytes(8, "big"))
        sh.update(p)
    return sh.digest(out_len)

def _h(tag: bytes, *parts: bytes, out_len: int) -> bytes:
    """SHA3-512 → truncate/expand via SHAKE-256 to out_len."""
    h = hashlib.sha3_512()
    h.update(tag)
    for p in parts:
        h.update(len(p).to_bytes(8, "big"))
        h.update(p)
    digest = h.digest()
    if out_len <= len(digest):
        return digest[:out_len]
    # expand deterministically with SHAKE-256
    return _xof_shake256(b"exp", digest, out_len=out_len)

def _ct_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)

# --------------------------------------------------------------------------------------
# RNG
# --------------------------------------------------------------------------------------

def _rand(n: int) -> bytes:
    return os.urandom(n)

# --------------------------------------------------------------------------------------
# Signature fallbacks
# --------------------------------------------------------------------------------------

def fallback_sig_keypair(alg: str) -> Tuple[bytes, bytes]:
    """
    Return (sk, pk) for the given signature algorithm.
    """
    _check_allowed()
    alg_l = alg.lower()
    if "dilithium3" in alg_l:
        lens = DILITHIUM3
    elif "sphincs" in alg_l:
        lens = SPHINCS_SHAKE_128S
    else:
        raise ValueError(f"Unknown signature alg for fallback: {alg}")

    sk = _rand(lens.sk)
    pk = _h(b"pk", sk, out_len=lens.pk)
    return sk, pk

def fallback_sig_sign(alg: str, msg: bytes, sk: bytes) -> bytes:
    """
    Produce a *forgeable* deterministic signature with correct length.
    sig = XOF("sig" | pk | msg) where pk = H("pk"|sk).
    """
    _check_allowed()
    alg_l = alg.lower()
    if "dilithium3" in alg_l:
        lens = DILITHIUM3
    elif "sphincs" in alg_l:
        lens = SPHINCS_SHAKE_128S
    else:
        raise ValueError(f"Unknown signature alg for fallback: {alg}")

    pk = _h(b"pk", sk, out_len=lens.pk)
    sig = _xof_shake256(b"sig", pk, msg, out_len=lens.sig)
    return sig

def fallback_sig_verify(alg: str, msg: bytes, sig: bytes, pk: bytes) -> bool:
    """
    Verify by recomputing XOF("sig"|pk|msg) and constant-time compare.
    """
    _check_allowed()
    alg_l = alg.lower()
    if "dilithium3" in alg_l:
        lens = DILITHIUM3
    elif "sphincs" in alg_l:
        lens = SPHINCS_SHAKE_128S
    else:
        raise ValueError(f"Unknown signature alg for fallback: {alg}")

    expected = _xof_shake256(b"sig", pk, msg, out_len=lens.sig)
    return _ct_eq(expected, sig)

# --------------------------------------------------------------------------------------
# KEM fallbacks (ML-KEM-768 / Kyber768)
# --------------------------------------------------------------------------------------

def fallback_kem_keypair(alg: str) -> Tuple[bytes, bytes]:
    """
    Return (sk, pk) for KEM algorithm. pk = H("pk"|sk) to target pk length.
    """
    _check_allowed()
    lens = ML_KEM_768  # only 768 is supported in this fallback
    sk = _rand(lens.sk)
    pk = _h(b"pk", sk, out_len=lens.pk)
    return sk, pk

def fallback_kem_encapsulate(alg: str, pk: bytes) -> Tuple[bytes, bytes]:
    """
    Encapsulate to pk: produce (ct, ss).
    ct = "ct"|eph|H(pk|eph) cropped/padded to ct_len (contains eph for decapsulation)
    ss = H("ss"|pk|eph) to ML-KEM-768 shared secret length (32 bytes)
    """
    _check_allowed()
    lens = ML_KEM_768
    eph = _rand(32)
    ss = _h(b"ss", pk, eph, out_len=lens.ss)
    ct_full = b"ct" + eph + _h(b"ctb", pk, eph, out_len=64)
    # crop/expand to ct length deterministically
    if len(ct_full) >= lens.ct:
        ct = ct_full[:lens.ct]
    else:
        ct = ct_full + _xof_shake256(b"ctx", ct_full, out_len=lens.ct - len(ct_full))
    return ct, ss

def fallback_kem_decapsulate(alg: str, sk: bytes, ct: bytes) -> bytes:
    """
    Decapsulate by reconstructing pk' from sk, extracting eph from ct, then:
    ss = H("ss"|pk'|eph).
    """
    _check_allowed()
    lens = ML_KEM_768
    pk = _h(b"pk", sk, out_len=lens.pk)
    # Recover eph from ct (matches construction in encapsulate)
    if len(ct) < 2 + 32:
        # corrupted; return deterministic junk to keep tests deterministic
        return _h(b"ss-bad", pk, ct, out_len=lens.ss)
    eph = ct[2:34]  # after "ct"
    ss = _h(b"ss", pk, eph, out_len=lens.ss)
    return ss

# --------------------------------------------------------------------------------------
# Algorithm-specific convenience wrappers (mirroring higher-level modules)
# --------------------------------------------------------------------------------------

def dilithium3_keypair() -> Tuple[bytes, bytes]:
    return fallback_sig_keypair("dilithium3")

def dilithium3_sign(msg: bytes, sk: bytes) -> bytes:
    return fallback_sig_sign("dilithium3", msg, sk)

def dilithium3_verify(msg: bytes, sig: bytes, pk: bytes) -> bool:
    return fallback_sig_verify("dilithium3", msg, sig, pk)

def sphincs_shake_128s_keypair() -> Tuple[bytes, bytes]:
    return fallback_sig_keypair("sphincs-shake-128s")

def sphincs_shake_128s_sign(msg: bytes, sk: bytes) -> bytes:
    return fallback_sig_sign("sphincs-shake-128s", msg, sk)

def sphincs_shake_128s_verify(msg: bytes, sig: bytes, pk: bytes) -> bool:
    return fallback_sig_verify("sphincs-shake-128s", msg, sig, pk)

def kyber768_keypair() -> Tuple[bytes, bytes]:
    return fallback_kem_keypair("ml-kem-768")

def kyber768_encapsulate(pk: bytes) -> Tuple[bytes, bytes]:
    return fallback_kem_encapsulate("ml-kem-768", pk)

def kyber768_decapsulate(sk: bytes, ct: bytes) -> bytes:
    return fallback_kem_decapsulate("ml-kem-768", sk, ct)

# --------------------------------------------------------------------------------------
# Self-test (manual)
# --------------------------------------------------------------------------------------

if __name__ == "__main__":
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    sk, pk = dilithium3_keypair()
    m = b"hello animica"
    sig = dilithium3_sign(m, sk)
    print("[dilithium3] verify:", dilithium3_verify(m, sig, pk), "lens:", len(pk), len(sk), len(sig))

    sk2, pk2 = sphincs_shake_128s_keypair()
    sig2 = sphincs_shake_128s_sign(m, sk2)
    print("[sphincs] verify:", sphincs_shake_128s_verify(m, sig2, pk2), "lens:", len(pk2), len(sk2), len(sig2))

    ksk, kpk = kyber768_keypair()
    ct, ss_b = kyber768_encapsulate(kpk)
    ss_a = kyber768_decapsulate(ksk, ct)
    print("[kem-768] ss match:", ss_a == ss_b, "lens:", len(kpk), len(ksk), len(ct), len(ss_a))
