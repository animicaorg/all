from __future__ import annotations

"""
verify.py — Verification aligned with spec/domains.yaml domain-tag SignBytes.

Verifies the same bytes produced by pq/py/sign.py:
  SignBytes = DomainTag || context? || msg
"""

from typing import Optional

from pq.py.registry import ALG_NAME
from pq.py.sign import PrehashKind, Signature, build_sign_bytes


def _backend_verify(alg_name: str, pk: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if alg_name == "dilithium3":
            from pq.py.algs import dilithium3 as backend
        elif alg_name.startswith("sphincs"):
            from pq.py.algs import sphincs_shake_128s as backend
        else:
            raise NotImplementedError(f"Signature backend not wired for {alg_name}")
    except Exception:
        return False

    if not hasattr(backend, "verify"):
        # If backend doesn't expose verify, treat as failure rather than guessing.
        return False

    return bool(backend.verify(pk, msg, sig))  # type: ignore[arg-type]


def verify_detached(
    msg: bytes,
    sig: Signature,
    pk: bytes,
    *,
    domain: Optional[str] = None,
    chain_id: Optional[int] = None,
    context: bytes = b"",
    prehash: Optional[PrehashKind] = None,
    strict_domain: bool = True,
    strict_prehash: bool = True,
    strict_alg: bool = True,
) -> bool:
    if strict_alg and ALG_NAME.get(sig.alg_id) != sig.alg_name:
        return False

    dom = domain if domain is not None else sig.domain
    if strict_domain and domain is not None and domain != sig.domain:
        return False

    ph: PrehashKind = prehash if prehash is not None else sig.prehash
    if strict_prehash and prehash is not None and prehash != sig.prehash:
        return False

    try:
        sign_bytes = build_sign_bytes(
            bytes(msg),
            domain=dom,
            chain_id=chain_id,
            alg_id=sig.alg_id,
            context=context,
            prehash=ph,
        )
    except Exception:
        return False

    return _backend_verify(sig.alg_name, pk, sign_bytes, sig.sig)


def verify_attached(
    signed: "object",
    pk: bytes,
    **kwargs,
) -> bool:
    # duck-typed: expects `signed.message` and `signed.signature`
    try:
        msg = getattr(signed, "message")
        sig = getattr(signed, "signature")
    except Exception:
        return False
    return verify_detached(msg, sig, pk, **kwargs)
