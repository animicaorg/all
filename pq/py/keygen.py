from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, Union

# NOTE: We prefer the official python package `oqs` when available, but the
# node must continue working without it (pure-Python backend). Import lazily and
# fall back to the vendored implementation when liboqs/oqs is absent.
try:  # pragma: no cover - import resolution depends on environment
    import oqs  # type: ignore

    _HAS_OQS = True
except Exception:  # pragma: no cover - handled by runtime fallback
    oqs = None  # type: ignore
    _HAS_OQS = False

from pq.py.address import address_from_pubkey  # type: ignore
from pq.py.registry import (  # type: ignore
    normalize_alg_name,
    name_of,
    id_of,
    DILITHIUM3_ID,
    SPHINCS_SHAKE_128S_ID,
)


@dataclass(frozen=True)
class KeyPair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes
    address: str


def _enabled_mechs() -> list[str]:
    if not _HAS_OQS or oqs is None:
        return []

    for fn in ("get_enabled_sig_mechanisms", "get_enabled_mechanisms"):
        if hasattr(oqs, fn):
            try:
                return list(getattr(oqs, fn)())
            except Exception:
                continue
    return []


def _pick_sig_mech(alg_name: str) -> str:
    alg = alg_name.lower().strip()
    mechs = _enabled_mechs()

    # liboqs 0.15+ uses ML-DSA-* names; older uses Dilithium*.
    if alg in ("dilithium3", "ml-dsa-65", "mldsa65"):
        if "ML-DSA-65" in mechs:
            return "ML-DSA-65"
        if "Dilithium3" in mechs:
            return "Dilithium3"
        # If listing is unavailable, try the modern name first:
        return "ML-DSA-65"

    raise NotImplementedError(f"Unsupported signature alg: {alg_name}")


def _normalize_alg(alg: Union[int, str, Any]) -> Tuple[int, str]:
    if isinstance(alg, int):
        if alg in (DILITHIUM3_ID, SPHINCS_SHAKE_128S_ID):
            return alg, name_of(alg)
        raise NotImplementedError(f"Unknown alg id: 0x{alg:04x}")

    if isinstance(alg, str):
        name = normalize_alg_name(alg)
        if name in ("dilithium3", "sphincs_shake_128s"):
            return id_of(name), name
        raise NotImplementedError(f"Unknown alg name: {alg}")

    # object with alg_id / name
    if hasattr(alg, "alg_id"):
        return _normalize_alg(int(getattr(alg, "alg_id")))
    if hasattr(alg, "name"):
        return _normalize_alg(str(getattr(alg, "name")))

    raise NotImplementedError(f"Unsupported alg descriptor: {type(alg)}")


def keygen_sig(alg: Union[int, str, Any]) -> KeyPair:
    """
    Generate a PQ signature keypair.

    Prefers liboqs (if available); otherwise falls back to the vendored
    pure-Python Dilithium3 implementation via ``animica.pq``. This keeps
    deterministic, strict key material even inside minimal containers.
    """
    alg_id, alg_name = _normalize_alg(alg)

    if alg_name == "dilithium3":
        # Fast path: liboqs
        if _HAS_OQS and oqs is not None:
            mech = _pick_sig_mech(alg_name)

            s = oqs.Signature(mech)
            pk = s.generate_keypair()
            sk = s.export_secret_key()

            # Refuse broken "fake" keys that can happen in fallback paths.
            if not isinstance(pk, (bytes, bytearray)) or not isinstance(sk, (bytes, bytearray)):
                raise RuntimeError("oqs returned non-bytes key material")
            pk_b = bytes(pk)
            sk_b = bytes(sk)

            # Strong sanity checks:
            if pk_b == sk_b:
                raise RuntimeError("PQ keygen produced sk==pk (this is invalid / fake)")
            if len(sk_b) <= len(pk_b):
                raise RuntimeError(
                    f"PQ keygen produced suspicious sizes pk={len(pk_b)} sk={len(sk_b)}"
                )
        else:
            # Pure-Python fallback (vendored Dilithium3)
            from animica import pq as animica_pq

            pk_b, sk_b = animica_pq.sig_keygen()
    elif alg_name == "sphincs_shake_128s":
        from pq.py.algs import sphincs_shake_128s as sphincs_backend

        sk_b, pk_b = sphincs_backend.keypair()
        if pk_b == sk_b:
            raise RuntimeError("PQ keygen produced sk==pk (this is invalid / fake)")
    else:
        raise NotImplementedError(f"Unsupported signature alg: {alg_name}")

    addr = address_from_pubkey(pk_b, alg_id)

    return KeyPair(
        alg_id=alg_id,
        alg_name=alg_name,
        public_key=pk_b,
        secret_key=sk_b,
        address=addr,
    )
