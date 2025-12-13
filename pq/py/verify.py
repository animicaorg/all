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
    "verify_detached",
    "verify_attached",
    "build_sign_bytes",
]


def verify_detached(
    msg: bytes,
    sig: Signature,
    pk: bytes,
    *,
    domain: Optional[Union[str, bytes]] = None,
    chain_id: Optional[int] = None,
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
        context=context,
        prehash=prehash,
        strict_domain=strict_domain,
        strict_prehash=strict_prehash,
        strict_alg=strict_alg,
    )
