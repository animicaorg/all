
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union

import oqs  # type: ignore

from pq.py.sign import (  # type: ignore
    _enabled_mechs,
    _normalize_alg,
    _normalize_domain_path,
    _pick_sig_mech,
    _apply_prehash,
    build_sign_bytes,
)

DILITHIUM3_ID = 0x1001


def verify_detached(
    message: bytes,
    sig_env: Dict[str, Any],
    public_key: bytes,
    *,
    chain_id: Optional[int] = None,
    context: bytes = b"",
) -> bool:
    alg_id = int(sig_env.get("alg", 0))
    prehash = sig_env.get("prehash", "sha3-256")
    domain = str(sig_env.get("domain", "tx"))

    if chain_id is None:
        chain_id = int(sig_env.get("chain_id", 0))

    _, alg_name = _normalize_alg(alg_id)
    mech = _pick_sig_mech(alg_name)

    sign_bytes = build_sign_bytes(
        message,
        chain_id=int(chain_id),
        domain=domain,
        alg_name=alg_name,
        context=context,
    )
    to_verify = _apply_prehash(sign_bytes, prehash)

    s = oqs.Signature(mech)
    sig = sig_env.get("sig", b"")
    if isinstance(sig, str):
        # do not auto-decode hex/base64 here; caller should pass bytes
        return False
    return bool(s.verify(to_verify, bytes(sig), bytes(public_key)))
