from __future__ import annotations

from __future__ import annotations

"""
verify.py — Uniform verification API for Animica PQ signatures.

This module now delegates to pq.py.sign.verify_detached/verify_attached so that
signing and verification share the exact same backend resolution and argument
conventions (important for liboqs keyword/positional drift between releases).
"""

from typing import Optional, Union

from pq.py.sign import (
    Signature,
    SignedMessage,
    PrehashKind,
    build_sign_bytes,
    verify_attached as _verify_attached,
    verify_detached as _verify_detached,
)

__all__ = [
    "verify",
    "verify_detached",
    "verify_attached",
    "build_sign_bytes",
]


def verify(alg_id: int, pubkey: bytes, signature: bytes, message: bytes) -> bool:
    """Raw verify by numeric algorithm id over a precomputed message.

    Matches the call convention used by rpc/state_service.py
    (``verify(alg_id, pubkey, signature, sign_bytes)``): resolves ``alg_id`` to a
    PQ scheme via the registry and verifies the detached signature against the
    already-canonicalized message bytes. Returns False on any failure (never
    raises), so callers can treat it as a boolean predicate.
    """
    try:
        import importlib

        from pq.py.registry import ALG_NAMES_BY_ID

        name = ALG_NAMES_BY_ID.get(int(alg_id))
        if not name:
            return False
        mod = importlib.import_module(f"pq.py.algs.{name}")
        return bool(mod.verify(bytes(pubkey), bytes(message), bytes(signature)))
    except Exception:
        return False


def verify_detached(
    msg: bytes,
    sig: Signature,
    pk: bytes,
    *,
    domain: Optional[Union[str, bytes]] = None,
    chain_id: Optional[int] = None,
    fork_id: Optional[int] = None,
    context: bytes = b"",
    prehash: Optional[PrehashKind] = None,
    strict_domain: bool = True,
    strict_prehash: bool = True,
    strict_alg: bool = True,
) -> bool:
    return _verify_detached(
        msg,
        sig,
        pk,
        domain=domain,
        chain_id=chain_id,
        fork_id=fork_id,
        context=context,
        prehash=prehash,
        strict_domain=strict_domain,
        strict_prehash=strict_prehash,
        strict_alg=strict_alg,
    )


def verify_attached(
    signed: SignedMessage,
    pk: bytes,
    *,
    domain: Optional[Union[str, bytes]] = None,
    chain_id: Optional[int] = None,
    fork_id: Optional[int] = None,
    context: bytes = b"",
    prehash: Optional[PrehashKind] = None,
    strict_domain: bool = True,
    strict_prehash: bool = True,
    strict_alg: bool = True,
) -> bool:
    return _verify_attached(
        signed,
        pk,
        domain=domain,
        chain_id=chain_id,
        fork_id=fork_id,
        context=context,
        prehash=prehash,
        strict_domain=strict_domain,
        strict_prehash=strict_prehash,
        strict_alg=strict_alg,
    )
