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


@dataclass(frozen=True)
class KeyPair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes
    address: str


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

    Uses the vendored pure-Python implementation for signature keygen.
    This avoids mixed backend key material across environments.
    """
    alg_id, alg_name = _normalize_alg(alg)

    if alg_name == "dilithium3":
        # Pure-Python ML-DSA-65 (vendored Dilithium3)
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
