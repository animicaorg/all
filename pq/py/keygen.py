from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, Union

# NOTE: We intentionally depend on the official python package `oqs` for real PQ.
# This avoids "fake" PQ keys that verify locally but fail on the node.
import oqs  # type: ignore

from pq.py.address import address_from_pubkey  # type: ignore


DILITHIUM3_ID = 0x1001  # 4097


@dataclass(frozen=True)
class KeyPair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes
    address: str


def _enabled_mechs() -> list[str]:
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
        if alg == DILITHIUM3_ID:
            return DILITHIUM3_ID, "dilithium3"
        raise NotImplementedError(f"Unknown alg id: 0x{alg:04x}")

    if isinstance(alg, str):
        name = alg.lower().strip()
        if name in ("dilithium3", "ml-dsa-65", "mldsa65"):
            return DILITHIUM3_ID, "dilithium3"
        raise NotImplementedError(f"Unknown alg name: {alg}")

    # object with alg_id / name
    if hasattr(alg, "alg_id"):
        return _normalize_alg(int(getattr(alg, "alg_id")))
    if hasattr(alg, "name"):
        return _normalize_alg(str(getattr(alg, "name")))

    raise NotImplementedError(f"Unsupported alg descriptor: {type(alg)}")


def keygen_sig(alg: Union[int, str, Any]) -> KeyPair:
    """
    Generate a real PQ signature keypair using liboqs-python.

    IMPORTANT: This MUST produce a real secret key (Dilithium3/ML-DSA-65 sk_len ~ 4032),
    not a fake dev fallback where sk==pk.
    """
    alg_id, alg_name = _normalize_alg(alg)
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
        raise RuntimeError(f"PQ keygen produced suspicious sizes pk={len(pk_b)} sk={len(sk_b)}")

    addr = address_from_pubkey(pk_b, alg_id)

    return KeyPair(
        alg_id=alg_id,
        alg_name=alg_name,
        public_key=pk_b,
        secret_key=sk_b,
        address=addr,
    )
