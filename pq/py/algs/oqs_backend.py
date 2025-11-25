from __future__ import annotations
"""
Optional liboqs ctypes backend for Animica PQ primitives.

What this module does
---------------------
- Dynamically loads the liboqs C library via ctypes (if present).
- Exposes a *uniform*, tiny wrapper for:
    • Signatures: Dilithium3, SPHINCS+-SHAKE-128s
    • KEM: Kyber768 (a.k.a. ML-KEM-768)
- Reports sizes (pk/sk/ct/ss/sig) directly from liboqs structs.
- Falls back gracefully: if liboqs isn't available, `is_available()` returns False
  and constructing `OQSBackend()` raises a clear RuntimeError.

Why this exists when `python-oqs` also exists?
----------------------------------------------
We prefer `python-oqs` when available (see the high-level wrappers in the sibling
modules). This backend is a *secondary* path that can be used to avoid Python
package/runtime issues or to exercise a lower-level ABI from a single, static
liboqs shared object. Nothing in this repo *requires* it at runtime.

Safety notes
------------
- This module is *only* a loader + FFI surface; it does not implement crypto.
- If you do not have liboqs installed (e.g., via your package manager or from
  source), `is_available()` will be False.
"""

import os
import ctypes
from ctypes import (
    c_char_p,
    c_int,
    c_size_t,
    c_uint8,
    c_void_p,
    POINTER,
    byref,
    create_string_buffer,
)
from ctypes.util import find_library
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, List


# --------------------------------------------------------------------------------------------------
# Attempt to load liboqs
# --------------------------------------------------------------------------------------------------

def _load_liboqs() -> Optional[ctypes.CDLL]:
    # Allow manual override (useful in CI or non-standard paths)
    override = os.environ.get("LIBOQS_PATH")
    if override and os.path.exists(override):
        try:
            return ctypes.CDLL(override)
        except OSError:
            return None

    # Try typical names as well as generic lookup
    candidates: List[str] = []
    probe = find_library("oqs")
    if probe:
        candidates.append(probe)
    # Common SONAMEs on Linux/macOS
    candidates += ["liboqs.so", "liboqs.dylib", "oqs.dll"]

    for name in candidates:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


_LIB = _load_liboqs()
_HAVE = _LIB is not None

OQS_SUCCESS = 0


def is_available() -> bool:
    """Return True if liboqs was successfully loaded."""
    return _HAVE


# --------------------------------------------------------------------------------------------------
# Minimal struct views (prefix-only) to read size fields from opaque liboqs objects.
# We purposefully model only the fields we read. The remaining function pointers and
# members in the real C structs follow those fields and need not be declared here.
# --------------------------------------------------------------------------------------------------

class _OQS_SIG(ctypes.Structure):
    _fields_ = [
        ("method_name", c_char_p),
        ("alg_version", c_char_p),
        ("claimed_nist_level", c_size_t),   # uint32 in practice; size_t is conservative
        ("is_euf_cma", c_int),
        ("length_public_key", c_size_t),
        ("length_secret_key", c_size_t),
        ("length_signature", c_size_t),
        # (function pointers follow in the real struct; we don't need them)
    ]


class _OQS_KEM(ctypes.Structure):
    _fields_ = [
        ("method_name", c_char_p),
        ("alg_version", c_char_p),
        ("claimed_nist_level", c_size_t),
        ("ind_cca", c_int),
        ("length_public_key", c_size_t),
        ("length_secret_key", c_size_t),
        ("length_ciphertext", c_size_t),
        ("length_shared_secret", c_size_t),
        # (function pointers follow; we don't need them)
    ]


if _HAVE:
    # Signature API
    _LIB.OQS_SIG_new.argtypes = [c_char_p]
    _LIB.OQS_SIG_new.restype = POINTER(_OQS_SIG)
    _LIB.OQS_SIG_free.argtypes = [POINTER(_OQS_SIG)]
    _LIB.OQS_SIG_free.restype = None

    _LIB.OQS_SIG_keypair.argtypes = [POINTER(_OQS_SIG), POINTER(c_uint8), POINTER(c_uint8)]
    _LIB.OQS_SIG_keypair.restype = c_int

    _LIB.OQS_SIG_sign.argtypes = [
        POINTER(_OQS_SIG),
        POINTER(c_uint8),
        POINTER(c_size_t),
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
    ]
    _LIB.OQS_SIG_sign.restype = c_int

    _LIB.OQS_SIG_verify.argtypes = [
        POINTER(_OQS_SIG),
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
    ]
    _LIB.OQS_SIG_verify.restype = c_int

    # KEM API
    _LIB.OQS_KEM_new.argtypes = [c_char_p]
    _LIB.OQS_KEM_new.restype = POINTER(_OQS_KEM)
    _LIB.OQS_KEM_free.argtypes = [POINTER(_OQS_KEM)]
    _LIB.OQS_KEM_free.restype = None

    _LIB.OQS_KEM_keypair.argtypes = [POINTER(_OQS_KEM), POINTER(c_uint8), POINTER(c_uint8)]
    _LIB.OQS_KEM_keypair.restype = c_int

    _LIB.OQS_KEM_encaps.argtypes = [
        POINTER(_OQS_KEM),
        POINTER(c_uint8),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_KEM_encaps.restype = c_int

    _LIB.OQS_KEM_decaps.argtypes = [
        POINTER(_OQS_KEM),
        POINTER(c_uint8),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_KEM_decaps.restype = c_int


# Canonical algorithm names as liboqs expects them.
# (These work for current liboqs; if you build with different variants, adjust here.)
ALG_DILITHIUM3 = b"Dilithium3"
ALG_SPHINCS_SHAKE_128S = b"SPHINCS+-SHAKE-128s-simple"
# Some liboqs builds use '-robust'; we try both at runtime.
ALG_SPHINCS_SHAKE_128S_ROBUST = b"SPHINCS+-SHAKE-128s-robust"

ALG_KYBER768 = b"Kyber768"          # Older builds
ALG_ML_KEM_768 = b"ML-KEM-768"      # Newer NIST alias


@dataclass(frozen=True)
class SigSizes:
    pk: int
    sk: int
    sig: int


@dataclass(frozen=True)
class KemSizes:
    pk: int
    sk: int
    ct: int
    ss: int


class OQSBackend:
    """
    Thin RAII wrapper over liboqs for the few algorithms we care about.
    """

    def __init__(self):
        if not _HAVE:
            raise RuntimeError("liboqs not found: install liboqs or set up python-oqs for higher-level wrappers.")

    # ------------- internals -------------
    def _sig_new(self, name: bytes) -> Tuple[POINTER(_OQS_SIG), SigSizes]:
        sig = _LIB.OQS_SIG_new(name)
        if not sig:
            raise RuntimeError(f"OQS_SIG_new failed (algorithm not enabled?): {name!r}")
        sizes = SigSizes(
            pk=int(sig.contents.length_public_key),
            sk=int(sig.contents.length_secret_key),
            sig=int(sig.contents.length_signature),
        )
        return sig, sizes

    def _kem_new(self, name: bytes) -> Tuple[POINTER(_OQS_KEM), KemSizes]:
        kem = _LIB.OQS_KEM_new(name)
        if not kem:
            raise RuntimeError(f"OQS_KEM_new failed (algorithm not enabled?): {name!r}")
        sizes = KemSizes(
            pk=int(kem.contents.length_public_key),
            sk=int(kem.contents.length_secret_key),
            ct=int(kem.contents.length_ciphertext),
            ss=int(kem.contents.length_shared_secret),
        )
        return kem, sizes

    # ------------- public: signatures -------------
    def sig_available_names(self) -> List[str]:
        names: List[bytes] = [ALG_DILITHIUM3, ALG_SPHINCS_SHAKE_128S]
        # Probe robust variant for SPHINCS+
        try:
            kem = _LIB.OQS_SIG_new(ALG_SPHINCS_SHAKE_128S_ROBUST)
            if kem:
                _LIB.OQS_SIG_free(kem)
                names.append(ALG_SPHINCS_SHAKE_128S_ROBUST)
        except Exception:
            pass
        return [n.decode("ascii") for n in names]

    def sig_keypair(self, alg: str) -> Tuple[bytes, bytes]:
        name = self._normalize_sig_alg(alg)
        sig, sizes = self._sig_new(name)
        try:
            pk_buf = (c_uint8 * sizes.pk)()
            sk_buf = (c_uint8 * sizes.sk)()
            rc = _LIB.OQS_SIG_keypair(sig, pk_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_SIG_keypair failed (rc={rc})")
            pk = bytes(pk_buf)
            sk = bytes(sk_buf)
            return (sk, pk)
        finally:
            _LIB.OQS_SIG_free(sig)

    def sig_sign(self, alg: str, msg: bytes, sk: bytes) -> bytes:
        name = self._normalize_sig_alg(alg)
        sig, sizes = self._sig_new(name)
        try:
            sig_out = (c_uint8 * sizes.sig)()
            sig_len = c_size_t(0)
            msg_buf = (c_uint8 * len(msg)).from_buffer_copy(msg)
            sk_buf = (c_uint8 * len(sk)).from_buffer_copy(sk)
            rc = _LIB.OQS_SIG_sign(sig, sig_out, byref(sig_len), msg_buf, c_size_t(len(msg)), sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_SIG_sign failed (rc={rc})")
            return bytes(sig_out)[: int(sig_len.value)]
        finally:
            _LIB.OQS_SIG_free(sig)

    def sig_verify(self, alg: str, msg: bytes, signature: bytes, pk: bytes) -> bool:
        name = self._normalize_sig_alg(alg)
        sig, _sizes = self._sig_new(name)
        try:
            msg_buf = (c_uint8 * len(msg)).from_buffer_copy(msg)
            sig_buf = (c_uint8 * len(signature)).from_buffer_copy(signature)
            pk_buf = (c_uint8 * len(pk)).from_buffer_copy(pk)
            rc = _LIB.OQS_SIG_verify(
                sig,
                msg_buf,
                c_size_t(len(msg)),
                sig_buf,
                c_size_t(len(signature)),
                pk_buf,
            )
            return rc == OQS_SUCCESS
        finally:
            _LIB.OQS_SIG_free(sig)

    # ------------- public: KEM -------------
    def kem_available_names(self) -> List[str]:
        names: List[bytes] = []
        for cand in (ALG_ML_KEM_768, ALG_KYBER768):
            try:
                kem = _LIB.OQS_KEM_new(cand)
                if kem:
                    names.append(cand)
                    _LIB.OQS_KEM_free(kem)
            except Exception:
                continue
        return [n.decode("ascii") for n in names]

    def kem_keypair(self, alg: str) -> Tuple[bytes, bytes]:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            pk_buf = (c_uint8 * sizes.pk)()
            sk_buf = (c_uint8 * sizes.sk)()
            rc = _LIB.OQS_KEM_keypair(kem, pk_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_keypair failed (rc={rc})")
            return (bytes(sk_buf), bytes(pk_buf))
        finally:
            _LIB.OQS_KEM_free(kem)

    def kem_encapsulate(self, alg: str, pk: bytes) -> Tuple[bytes, bytes]:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            ct_buf = (c_uint8 * sizes.ct)()
            ss_buf = (c_uint8 * sizes.ss)()
            pk_buf = (c_uint8 * len(pk)).from_buffer_copy(pk)
            rc = _LIB.OQS_KEM_encaps(kem, ct_buf, ss_buf, pk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_encaps failed (rc={rc})")
            return (bytes(ct_buf), bytes(ss_buf))
        finally:
            _LIB.OQS_KEM_free(kem)

    def kem_decapsulate(self, alg: str, sk: bytes, ct: bytes) -> bytes:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            ss_buf = (c_uint8 * sizes.ss)()
            sk_buf = (c_uint8 * len(sk)).from_buffer_copy(sk)
            ct_buf = (c_uint8 * len(ct)).from_buffer_copy(ct)
            rc = _LIB.OQS_KEM_decaps(kem, ss_buf, ct_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_decaps failed (rc={rc})")
            return bytes(ss_buf)
        finally:
            _LIB.OQS_KEM_free(kem)

    # ------------- helpers -------------
    @staticmethod
    def _normalize_sig_alg(alg: str) -> bytes:
        a = alg.lower().replace("_", "-")
        if "dilithium3" in a:
            return ALG_DILITHIUM3
        if "sphincs" in a:
            # Prefer 'simple' profile if available; robust will be probed at construction time
            # but both OQS_SIG_new calls will succeed if the build supports them.
            try:
                # Quick probe to prefer 'simple' when both exist
                if _LIB.OQS_SIG_new(ALG_SPHINCS_SHAKE_128S):
                    return ALG_SPHINCS_SHAKE_128S
            except Exception:
                pass
            return ALG_SPHINCS_SHAKE_128S_ROBUST
        raise ValueError(f"Unknown/unsupported signature alg: {alg}")

    @staticmethod
    def _normalize_kem_alg(alg: str) -> bytes:
        a = alg.lower().replace("_", "-")
        if "ml-kem-768" in a or "mlkem768" in a:
            return ALG_ML_KEM_768
        if "kyber768" in a or "kyber-768" in a:
            return ALG_KYBER768
        raise ValueError(f"Unknown/unsupported KEM alg: {alg}")


# --------------------------------------------------------------------------------------------------
# Manual smoke test
# --------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("[oqs_backend] available:", is_available())
    if not is_available():
        raise SystemExit(0)

    oqs = OQSBackend()

    # Signatures
    for alg in ("Dilithium3", "SPHINCS+-SHAKE-128s"):
        print(f" -- SIG alg: {alg}")
        sk, pk = oqs.sig_keypair(alg)
        msg = b"hello animica"
        sig = oqs.sig_sign(alg, msg, sk)
        ok = oqs.sig_verify(alg, msg, sig, pk)
        print("    sizes: pk", len(pk), "sk", len(sk), "sig", len(sig), "verify:", ok)

    # KEM
    for alg in ("ML-KEM-768", "Kyber768"):
        try:
            print(f" -- KEM alg: {alg}")
            sk, pk = oqs.kem_keypair(alg)
            ct, ss_b = oqs.kem_encapsulate(alg, pk)
            ss_a = oqs.kem_decapsulate(alg, sk, ct)
            print("    sizes: pk", len(pk), "sk", len(sk), "ct", len(ct), "ss", len(ss_a), "match:", ss_a == ss_b)
            break  # first that works is fine
        except Exception as e:
            print("    (skip) reason:", e)
            continue
