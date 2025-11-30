from __future__ import annotations

"""
pq.py.algs — pluggable algorithm backends

This package selects concrete implementations for:
  • Signature: Dilithium3, SPHINCS+-SHAKE-128s
  • KEM: Kyber768

Preference order:
  1) oqs_backend (if liboqs is available at runtime)
  2) native thin wrappers in this package (e.g. dilithium3.py, kyber768.py)
  3) pure_python_fallbacks (educational; may raise NotImplementedError for perf/safety)

Higher layers (pq.py.sign / pq.py.verify / pq.py.kem) call select_sig/select_kem here.
Those layers handle domain separation and input sanitation; backends only do raw crypto ops.

All backends expose a uniform minimal surface:

Signature backend:
  - keypair(seed: bytes|None) -> (sk: bytes, pk: bytes)
  - sign(sk: bytes, msg: bytes) -> sig: bytes
  - verify(pk: bytes, msg: bytes, sig: bytes) -> bool
  - sizes: dict with keys {"pk","sk","sig"} (ints)

KEM backend:
  - keypair(seed: bytes|None) -> (pk: bytes, sk: bytes)
  - encapsulate(pk: bytes) -> (ct: bytes, ss: bytes)
  - decapsulate(sk: bytes, ct: bytes) -> ss: bytes
  - sizes: dict with keys {"pk","sk","ct","ss"} (ints)
"""

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Protocol, Tuple

# Optional dependencies: oqs backend is best-effort
try:
    from . import oqs_backend as _oqs

    HAS_OQS: bool = _oqs.is_available()
except Exception:
    _oqs = None  # type: ignore
    HAS_OQS = False

# Educational/reference fallbacks (may be partial)
# Preferred native wrappers (thin shims; may themselves use ctypes/WASM under the hood)
from . import dilithium3 as _dilithium3
from . import kyber768 as _kyber
from . import pure_python_fallbacks as _edu
from . import sphincs_shake_128s as _sphincs

# --------------------------------------------------------------------------------------
# Protocols (interfaces)
# --------------------------------------------------------------------------------------


class SigBackend(Protocol):
    sizes: Dict[str, int]

    def keypair(self, seed: Optional[bytes] = None) -> Tuple[bytes, bytes]: ...
    def sign(self, sk: bytes, msg: bytes) -> bytes: ...
    def verify(self, pk: bytes, msg: bytes, sig: bytes) -> bool: ...


class KemBackend(Protocol):
    sizes: Dict[str, int]

    def keypair(self, seed: Optional[bytes] = None) -> Tuple[bytes, bytes]: ...
    def encapsulate(self, pk: bytes) -> Tuple[bytes, bytes]: ...
    def decapsulate(self, sk: bytes, ct: bytes) -> bytes: ...


@dataclass(frozen=True)
class SelectedSig:
    name: Literal["dilithium3", "sphincs_shake_128s"]
    backend: SigBackend
    source: Literal["oqs", "native", "fallback"]


@dataclass(frozen=True)
class SelectedKem:
    name: Literal["kyber768"]
    backend: KemBackend
    source: Literal["oqs", "native", "fallback"]


# --------------------------------------------------------------------------------------
# Selection logic
# --------------------------------------------------------------------------------------


def _pick_sig(name: str) -> SelectedSig:
    lname = name.lower().replace("-", "_")
    # 1) oqs
    if HAS_OQS:
        if lname == "dilithium3" and _oqs.has_sig("dilithium3"):
            return SelectedSig("dilithium3", _oqs.dilithium3, "oqs")
        if lname in ("sphincs_shake_128s", "sphincs+_shake_128s", "sphincs"):
            if _oqs.has_sig("sphincs_shake_128s"):
                return SelectedSig("sphincs_shake_128s", _oqs.sphincs_shake_128s, "oqs")
    # 2) native thin wrappers
    if lname == "dilithium3" and _dilithium3.is_available():
        return SelectedSig("dilithium3", _dilithium3, "native")
    if (
        lname in ("sphincs_shake_128s", "sphincs+_shake_128s", "sphincs")
        and _sphincs.is_available()
    ):
        return SelectedSig("sphincs_shake_128s", _sphincs, "native")
    # 3) educational fallbacks
    if lname == "dilithium3" and _edu.has_sig("dilithium3"):
        return SelectedSig("dilithium3", _edu.dilithium3, "fallback")
    if lname in (
        "sphincs_shake_128s",
        "sphincs+_shake_128s",
        "sphincs",
    ) and _edu.has_sig("sphincs_shake_128s"):
        return SelectedSig("sphincs_shake_128s", _edu.sphincs_shake_128s, "fallback")

    raise NotImplementedError(f"Signature algorithm not available: {name}")


def _pick_kem(name: str) -> SelectedKem:
    lname = name.lower()
    if lname not in ("kyber768", "kyber-768", "kyber"):
        raise NotImplementedError(f"KEM not supported: {name}")

    # 1) oqs
    if HAS_OQS and _oqs.has_kem("kyber768"):
        return SelectedKem("kyber768", _oqs.kyber768, "oqs")
    # 2) native
    if _kyber.is_available():
        return SelectedKem("kyber768", _kyber, "native")
    # 3) fallback
    if _edu.has_kem("kyber768"):
        return SelectedKem("kyber768", _edu.kyber768, "fallback")

    raise NotImplementedError("Kyber768 backend not available")


# Public helpers (used by pq.py.{keygen,sign,verify,kem,handshake})
def select_sig(name: str) -> SelectedSig:
    """
    Pick backend for the given signature scheme, returning (name, backend, source).
    """
    return _pick_sig(name)


def select_kem(name: str) -> SelectedKem:
    """
    Pick backend for the given KEM, returning (name, backend, source).
    """
    return _pick_kem(name)


# Convenience: quick capability report
def capability_report() -> Dict[str, Dict[str, str]]:
    sigs: Dict[str, Dict[str, str]] = {}
    for n in ("dilithium3", "sphincs_shake_128s"):
        try:
            sel = _pick_sig(n)
            sigs[n] = {
                "source": sel.source,
                **{k: str(v) for k, v in sel.backend.sizes.items()},
            }
        except Exception as e:
            sigs[n] = {"error": type(e).__name__}
    kems: Dict[str, Dict[str, str]] = {}
    for n in ("kyber768",):
        try:
            sel = _pick_kem(n)
            kems[n] = {
                "source": sel.source,
                **{k: str(v) for k, v in sel.backend.sizes.items()},
            }
        except Exception as e:
            kems[n] = {"error": type(e).__name__}
    return {"sig": sigs, "kem": kems}


__all__ = [
    "HAS_OQS",
    "select_sig",
    "select_kem",
    "capability_report",
    "SigBackend",
    "KemBackend",
    "SelectedSig",
    "SelectedKem",
]
