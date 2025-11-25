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
    except Exception as e:
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
        Optional deterministic context bytes that were included at sign time.
    prehash : Optional[PrehashKind]
        Override of prehash function. If None, uses sig.prehash. If provided and
        strict_prehash=True, must equal sig.prehash.
    strict_domain / strict_prehash / strict_alg : bool
        Toggle safety checks that the envelope matches the verifier's expectations.

    Returns
    -------
    bool : True if signature is valid; False otherwise.
    """
    alg_id, alg_name = _check_alg(sig)

    # Safety checks / selection of parameters
    env_domain = sig.domain
    use_domain: Union[str, bytes] = env_domain if domain is None else domain
    if strict_domain and domain is not None:
        # normalize both to str for comparison
        lhs = env_domain
        rhs = domain.decode("utf-8", "replace") if isinstance(domain, (bytes, bytearray)) else str(domain)
        if lhs != rhs:
            raise ValueError(f"Domain mismatch: envelope={lhs!r} verifier={rhs!r}")

    env_prehash = sig.prehash
    use_prehash = env_prehash if prehash is None else prehash
    if strict_prehash and prehash is not None and prehash != env_prehash:
        raise ValueError(f"Prehash mismatch: envelope={env_prehash} verifier={prehash}")

    if strict_alg:
        # Ensure name maps back to id consistently
        expected_name = ALG_NAME.get(alg_id, "")
        if expected_name and expected_name != sig.alg_name:
            raise ValueError(f"Algorithm name/id mismatch: {sig.alg_name} vs 0x{alg_id:02x}")

    # Recompute canonical SignBytes and verify with backend
    ph = build_sign_bytes(
        msg,
        domain=use_domain,
        chain_id=chain_id,
        alg_id=alg_id,
        context=context,
        prehash=use_prehash,  # type: ignore[arg-type]
    )
    return _backend_verify(alg_name, pk, ph, sig.sig)


def verify_attached(
    signed: SignedMessage,
    pk: bytes,
    **kwargs,
) -> bool:
    """
    Verify a SignedMessage envelope.

    kwargs are passed to verify_detached (domain/chain_id/context/prehash/strict_*).
    """
    return verify_detached(signed.message, signed.signature, pk, **kwargs)


# --------------------------------------------------------------------------------------
# CLI smoke: python -m pq.py.verify <alg_id|name> <hex:pk> <hex:msg> <hex:sig> [domain] [chain_id]
# The CLI does not parse the full Signature envelope (for brevity); it assumes the caller
# provides alg & prehash info. Use the Python API for real workflows.
# --------------------------------------------------------------------------------------

def _parse_hex_arg(s: str) -> bytes:
    if not s.startswith("hex:"):
        raise ValueError("expected hex:…")
    return bytes.fromhex(s[4:].replace("_", "").replace(" ", ""))

def _main() -> None:
    import sys
    from pq.py.sign import Signature as Sig

    args = sys.argv[1:]
    if len(args) < 4 or args[0] in ("-h", "--help"):
        print(
            "Usage: python -m pq.py.verify <alg> <hex:pk> <hex:msg> <hex:sig> [domain] [chain_id]\n"
            "  alg     = dilithium3 | sphincs_shake_128s | <alg_id int>\n"
            "  hex:pk  = public key hex\n"
            "  hex:msg = original message bytes hex\n"
            "  hex:sig = raw signature bytes hex (detached)\n"
            "  domain  = optional domain string (default 'generic')\n"
            "  chain_id= optional integer chain id\n"
            "\n"
            "Note: CLI assumes prehash=sha3-512 and builds a temporary Signature envelope.\n"
        )
        sys.exit(0)

    alg_raw = args[0]
    alg_id: Optional[int] = int(alg_raw) if alg_raw.isdigit() else None
    if alg_id is None:
        # Map name → id by creating a tiny throwaway Signature via sign's helper
        # (We can piggyback on registry through verify path)
        from pq.py.registry import ALG_ID
        name = alg_raw.strip().lower()
        if name not in ALG_ID:
            print(f"Unknown algorithm name: {name}")
            sys.exit(2)
        alg_id = ALG_ID[name]
        alg_name = name
    else:
        if not is_known_alg_id(alg_id) or not is_sig_alg_id(alg_id):
            print(f"Unknown or non-signature alg_id: 0x{alg_id:02x}")
            sys.exit(2)
        alg_name = ALG_NAME[alg_id]

    pk = _parse_hex_arg(args[1])
    msg = _parse_hex_arg(args[2])
    sig_raw = _parse_hex_arg(args[3])
    domain = args[4] if len(args) > 4 else "generic"
    chain_id = int(args[5]) if len(args) > 5 else None

    env = Sig(
        alg_id=alg_id,
        alg_name=alg_name,
        domain=domain,
        prehash="sha3-512",
        sig=sig_raw,
    )

    ok = verify_detached(msg, env, pk, chain_id=chain_id)
    print("valid" if ok else "invalid")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    _main()
