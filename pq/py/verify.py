from __future__ import annotations

"""
verify.py — Uniform verification API for Animica PQ signatures.

Goals
-----
- One call to verify any supported PQ signature (Dilithium3, SPHINCS+ SHAKE-128s).
- Strong, explicit domain separation: the same canonical SignBytes as in sign.py.
- Safe defaults (strict checks) with opt-out toggles for tooling.
- Friendly CLI for smoke tests.

Public API
----------
- verify_detached(msg, sig, pk, *, domain=None, chain_id=None, context=b"", prehash=None,
                  strict_domain=True, strict_prehash=True, strict_alg=True) -> bool
- verify_attached(signed: SignedMessage, pk, **kwargs) -> bool
- build_sign_bytes(...) is re-exported from pq.py.sign for convenience.

Notes
-----
- The `Signature` envelope (from pq.py.sign) records `alg_id`, `alg_name`, `domain`, `prehash`.
  We recompute canonical SignBytes with those values by default.
- You MAY override `domain`/`prehash` by passing kwargs and setting strict_* to False,
  but production code should keep strict checks enabled.
"""

from dataclasses import dataclass
from typing import Optional, Union, Tuple, Literal

from pq.py.registry import (
    ALG_NAME,
    is_known_alg_id,
    is_sig_alg_id,
)
from pq.py.sign import (
    Signature,
    SignedMessage,
    build_sign_bytes,
    PrehashKind,
)

__all__ = [
    "verify_detached",
    "verify_attached",
    "build_sign_bytes",
]

# --------------------------------------------------------------------------------------
# Backend dispatcher
# --------------------------------------------------------------------------------------


def _backend_verify(alg_name: str, pk: bytes, msg: bytes, sig: bytes) -> bool:
    """
    Call the algorithm-specific verifier.
    `msg` is already the canonical SignBytes digest (fixed-length), as in sign.py.
    """
    try:
        if alg_name == "dilithium3":
            from pq.py.algs import dilithium3 as backend
        elif alg_name == "sphincs_shake_128s":
            from pq.py.algs import sphincs_shake_128s as backend
        else:
            raise NotImplementedError(f"Verification backend not wired for {alg_name}")
    except Exception as e:  # pragma: no cover - defensive
        raise NotImplementedError(
            f"Verification backend for {alg_name} not available. "
            f"Install/build PQ backend (e.g., liboqs) and ensure wrappers are importable. ({e})"
        ) from e

    if not hasattr(backend, "verify"):
        raise NotImplementedError(f"Backend {backend.__name__} lacks .verify(public_key, message, signature)")
    return bool(backend.verify(public_key=pk, message=msg, signature=sig))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Verify API
# --------------------------------------------------------------------------------------


def _check_alg(sig: Signature) -> Tuple[int, str]:
    alg_id = sig.alg_id
    if not is_known_alg_id(alg_id) or not is_sig_alg_id(alg_id):
        raise ValueError(f"Unknown or non-signature alg_id in envelope: 0x{alg_id:02x}")
    return alg_id, sig.alg_name


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
    """
    Verify a detached Signature for `msg` against public key `pk`.

    Parameters
    ----------
    msg : bytes
        The original message bytes prior to domain-prehashing.
    sig : Signature
        Detached signature envelope from pq.py.sign.sign_detached.
    pk : bytes
        Public key for the signature algorithm.
    domain : Optional[str|bytes]
        Domain override. If None, uses sig.domain. If provided and strict_domain=True,
        must match sig.domain exactly (string-wise).
    chain_id : Optional[int]
        Chain id used by the signer. If the verifier passes a different value from the
        signer’s, the prehash will differ and verification will (correctly) fail.
    context : bytes
        Additional context bytes used during signing (must match to verify).
    prehash : Optional[PrehashKind]
        Override for prehash algorithm. If None, uses sig.prehash. If provided and
        strict_prehash=True, must match sig.prehash exactly.
    strict_domain : bool
        If True (default), reject verification when domain override differs from sig.
    strict_prehash : bool
        If True (default), reject verification when prehash override differs from sig.
    strict_alg : bool
        If True (default), reject verification when alg_id is unknown or mismatched.
    """
    if not isinstance(sig, Signature):
        raise TypeError("sig must be pq.py.sign.Signature")

    # Alg checks
    if strict_alg:
        alg_id, alg_name = _check_alg(sig)
    else:
        alg_id, alg_name = sig.alg_id, sig.alg_name

    # Domain checks
    domain_effective: Union[str, bytes]
    if domain is None:
        domain_effective = sig.domain
    else:
        if strict_domain and str(domain) != str(sig.domain):
            raise ValueError(f"domain mismatch: sig={sig.domain} verify={domain}")
        domain_effective = domain

    # Prehash checks
    prehash_effective: PrehashKind
    if prehash is None:
        prehash_effective = sig.prehash
    else:
        if strict_prehash and prehash != sig.prehash:
            raise ValueError(f"prehash mismatch: sig={sig.prehash} verify={prehash}")
        prehash_effective = prehash

    # Canonical SignBytes
    sign_bytes = build_sign_bytes(
        msg,
        domain=domain_effective,
        chain_id=chain_id,
        alg_id=alg_id,
        context=context,
        prehash=prehash_effective,
    )

    # Backend verify
    return _backend_verify(alg_name, pk, sign_bytes, sig.sig)


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
    if not isinstance(signed, SignedMessage):
        raise TypeError("signed must be pq.py.sign.SignedMessage")
    return verify_detached(
        signed.message,
        signed.signature,
        pk,
        domain=domain,
        chain_id=chain_id,
        context=context,
        prehash=prehash,
        strict_domain=strict_domain,
        strict_prehash=strict_prehash,
        strict_alg=strict_alg,
    )


# --------------------------------------------------------------------------------------
# CLI helper for smoke tests
# --------------------------------------------------------------------------------------

def _parse_hex_arg(s: str) -> bytes:
    if not s.startswith("hex:"):
        raise ValueError("expected hex:…")
    return bytes.fromhex(s[4:].replace("_", "").replace(" ", ""))


def _main() -> None:  # pragma: no cover
    import sys

    args = sys.argv[1:]
    if len(args) < 4 or args[0] in ("-h", "--help"):
        print(
            "Usage: python -m pq.py.verify <alg_id> <hex:pk> <hex:sig> <hex:msg> [domain] [chain_id]\n"
            "  alg_id  = integer alg id (e.g., 4097 for dilithium3)\n"
            "  hex:pk  = public key hex\n"
            "  hex:sig = signature hex (from pq.py.sign.sign_detached)\n"
            "  hex:msg = message bytes hex (will be domain-prehashed before verify)\n"
            "  domain  = optional domain string (default sig.domain)\n"
            "  chain_id= optional integer chain id (default none)\n"
        )
        sys.exit(0)

    alg_id = int(args[0], 0)
    pk = _parse_hex_arg(args[1])
    sig_bytes = _parse_hex_arg(args[2])
    msg = _parse_hex_arg(args[3])
    domain = args[4] if len(args) > 4 else "generic"
    chain_id = int(args[5]) if len(args) > 5 else None

    sig = Signature(alg_id=alg_id, alg_name=ALG_NAME.get(alg_id, ""), domain=domain, prehash="sha3-512", sig=sig_bytes)

    try:
        ok = verify_detached(msg, sig, pk, chain_id=chain_id, strict_domain=False)
    except Exception as e:
        print("verify failed:", e)
        sys.exit(2)

    print("ok:" if ok else "fail", ok)


if __name__ == "__main__":  # pragma: no cover
    _main()
