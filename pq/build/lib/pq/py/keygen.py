from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple, Union

from pq.py.address import address_from_pubkey  # type: ignore
from pq.py.registry import (  # type: ignore
    normalize_alg_name,
    name_of,
    id_of,
    DILITHIUM3_ID,
    SPHINCS_SHAKE_128S_ID,
)

ML_DSA_65_ID = 0x1003


@dataclass(frozen=True)
class KeyPair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes
    address: str


def _normalize_alg(alg: Union[int, str, Any]) -> Tuple[int, str]:
    if isinstance(alg, int):
        if alg in (DILITHIUM3_ID, SPHINCS_SHAKE_128S_ID, ML_DSA_65_ID):
            return alg, name_of(alg)
        raise NotImplementedError(f"Unknown alg id: 0x{alg:04x}")

    if isinstance(alg, str):
        name = normalize_alg_name(alg)
        if name in ("dilithium3", "sphincs_shake_128s", "ml_dsa_65"):
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

    Uses the vendored pure-Python implementation for signature keygen.
    This avoids mixed backend key material across environments.
    """
    alg_id, alg_name = _normalize_alg(alg)

    if alg_name == "ml_dsa_65":
        # Real FIPS 204 ML-DSA-65 (vendored jack4818/dilithium-py)
        from pq.py.algs import ml_dsa_65 as ml_dsa_backend

        sk_b, pk_b = ml_dsa_backend.keypair()
        if pk_b == sk_b:
            raise RuntimeError("PQ keygen produced sk==pk (this is invalid / fake)")
    elif alg_name == "dilithium3":
        # Legacy commitment stub kept for verifying historical blocks only.
        # New wallets MUST use ml_dsa_65; refuse to create new dilithium3 keys
        # unless explicitly opted in via ANIMICA_ALLOW_LEGACY_STUB_KEYGEN=1.
        import os as _os
        if _os.environ.get("ANIMICA_ALLOW_LEGACY_STUB_KEYGEN") != "1":
            raise NotImplementedError(
                "dilithium3 (0x1001) is a deprecated commitment stub. Use "
                "ml_dsa_65 (0x1003) for new wallets. To create a legacy stub "
                "key anyway, set ANIMICA_ALLOW_LEGACY_STUB_KEYGEN=1."
            )
        from animica import pq as animica_pq

        pk_b, sk_b = animica_pq.sig_keygen()
    elif alg_name == "sphincs_shake_128s":
        import os as _os
        if _os.environ.get("ANIMICA_ALLOW_LEGACY_STUB_KEYGEN") != "1":
            raise NotImplementedError(
                "sphincs_shake_128s (0x1002) is a deprecated commitment stub. "
                "Use ml_dsa_65 (0x1003) for new wallets. To create a legacy "
                "stub key anyway, set ANIMICA_ALLOW_LEGACY_STUB_KEYGEN=1."
            )
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
