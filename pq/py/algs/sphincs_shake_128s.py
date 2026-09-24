from __future__ import annotations

"""
Animica PQ: SPHINCS+ SHAKE-128s signature backend (pure-Python only).

This backend intentionally uses only the in-repo pure-Python implementation to
avoid cross-environment backend drift.
"""

import os
from typing import Dict, Optional, Tuple

from . import pure_python_fallbacks as _custom_fallbacks

# NOTE (11.1.0): this module used to set ANIMICA_ALLOW_PQ_PURE_FALLBACK=1 at IMPORT
# time. The CLI imports it transitively, so every `animica wallet new` on mainnet hit
# the fail-closed "unsafe flag is set" guard and refused to create a real ML-DSA-65
# wallet. 11.1.0 claimed the opt-in moved to call time but never wired it, so every
# node on 11.1.0-11.2.2 failed the startup PQ self-test on mainnet
# ("sphincs_shake_128s[2] reason=backend_missing") and restart-looped. 11.2.3 passes
# allow=True per call instead, leaving os.environ untouched.

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
    return _custom_fallbacks.fallback_sig_keypair("sphincs-shake-128s", allow=True)


def generate_keypair(seed: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    sk, pk = keypair(seed)
    return (pk, sk)


def sign(sk: bytes, msg: bytes, pk: bytes | None = None) -> bytes:
    return _custom_fallbacks.fallback_sig_sign("sphincs-shake-128s", msg, sk, pk, allow=True)


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    return _custom_fallbacks.fallback_sig_verify("sphincs-shake-128s", msg, sig, pk, allow=True)


if __name__ == "__main__":
    print("[sphincs_shake_128s] available:", is_available(), "sizes:", sizes)
    sk, pk = keypair()
    m = b"hello animica (sphincs)"
    s = sign(sk, m)
    print("verify(ok):", verify(pk, m, s))
    print("verify(bad):", verify(pk, m + b"x", s))
