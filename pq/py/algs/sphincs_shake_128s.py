from __future__ import annotations

"""
Animica PQ: SPHINCS+ SHAKE-128s signature backend (pure-Python only).

This backend intentionally uses only the in-repo pure-Python implementation to
avoid cross-environment backend drift.
"""

import os
from typing import Dict, Optional, Tuple

from . import pure_python_fallbacks as _custom_fallbacks

# ANM-L03: do NOT self-enable the insecure pure-Python PQ fallback at import.
# SPHINCS+ (scheme id 2) is a forgeable stub, disabled by default in
# coretx.schemes.CANONICAL_SCHEME_SPECS. Using the pure fallback now requires an
# explicit ANIMICA_ALLOW_PQ_PURE_FALLBACK=1 (dev/test only), never on mainnet.

_sizes: Dict[str, int] = {
    "pk": _custom_fallbacks.SPHINCS_SHAKE_128S.pk,
    "sk": _custom_fallbacks.SPHINCS_SHAKE_128S.sk,
    "sig": _custom_fallbacks.SPHINCS_SHAKE_128S.sig,
}

sizes = _sizes.copy()


def is_available() -> bool:
    return True


def keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    # `seed` is accepted for API compatibility, but the pure-python fallback uses
    # os.urandom internally.
    return _custom_fallbacks.fallback_sig_keypair("sphincs-shake-128s")


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    sk, pk = keypair(seed)
    return (pk, sk)


def sign(sk: bytes, msg: bytes, pk: bytes | None = None) -> bytes:
    return _custom_fallbacks.fallback_sig_sign("sphincs-shake-128s", msg, sk, pk)


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    return _custom_fallbacks.fallback_sig_verify("sphincs-shake-128s", msg, sig, pk)


if __name__ == "__main__":
    print("[sphincs_shake_128s] available:", is_available(), "sizes:", sizes)
    sk, pk = keypair()
    m = b"hello animica (sphincs)"
    s = sign(sk, m)
    print("verify(ok):", verify(pk, m, s))
    print("verify(bad):", verify(pk, m + b"x", s))
