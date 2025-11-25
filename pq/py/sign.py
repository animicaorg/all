from __future__ import annotations

"""
sign.py — Uniform, domain-separated signing API for Animica PQ cryptography.

Design goals
------------
- One function to sign bytes with any supported PQ signature algorithm.
- Strong domain separation (explicit "what am I signing?" context).
- Canonical "SignBytes" prehash (SHA3-512 over length-delimited fields).
- Minimal, portable envelope (alg_id + signature bytes), ready for CBOR/JSON.
- Zero surprises: the exact same prehashing is used by verification.

Public API
----------
- sign_detached(msg, alg, sk, *, domain="generic", chain_id=None, context=b"", prehash="sha3-512")
      -> Signature
- sign_attached(msg, alg, sk, **kwargs)
      -> SignedMessage (includes original msg + detached envelope)
- build_sign_bytes(msg, *, domain, chain_id, alg_id, context=b"", prehash="sha3-512")
      -> bytes  (what gets signed)

Where `alg` can be an int alg_id or a canonical name:
  "dilithium3", "sphincs_shake_128s" (see pq/py/registry.py).

Backends
--------
This module dispatches to:
- pq.py.algs.dilithium3.sign(secret_key: bytes, message: bytes) -> bytes
- pq.py.algs.sphincs_shake_128s.sign(secret_key: bytes, message: bytes) -> bytes

Both receive the canonical SignBytes (prehash) as message.

Security notes
--------------
- Domain separation is *mandatory*. Use specific domains like:
    "tx/sign", "header/proposer", "p2p/identity", "da/receipt", etc.
  These should align with spec/domains.yaml at the repo root.
- The canonical SignBytes includes (domain, chain_id?, alg_id, context?, msg).
  All fields are length-delimited to avoid ambiguity.
- We prehash with SHA3-512 to a fixed 64-byte digest, then sign that digest.
  This aligns behavior across algorithms and makes signatures size-predictable.

"""

from dataclasses import dataclass
from typing import Optional, Union, Literal, Tuple

from pq.py.utils.hash import sha3_256, sha3_512
from pq.py.registry import (
    ALG_ID,
    ALG_NAME,
    is_known_alg_id,
    is_sig_alg_id,
)

# --------------------------------------------------------------------------------------
# Small helpers: varint, field encoding, alg normalization
# --------------------------------------------------------------------------------------

def _uvarint(n: int) -> bytes:
    """LEB128 uvarint (little endian base-128) for compact, unambiguous ints."""
    if n < 0:
        raise ValueError("uvarint expects non-negative int")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

def _len_bytes(b: bytes) -> bytes:
    """Length prefix for a bytes field (uvarint length || bytes)."""
    return _uvarint(len(b)) + b

def _norm_domain(domain: Union[str, bytes]) -> bytes:
    if isinstance(domain, bytes):
        return domain
    if isinstance(domain, str):
        d = domain.strip()
        if not d:
            raise ValueError("domain must be non-empty")
        return d.encode("utf-8")
    raise TypeError("domain must be str|bytes")

def _normalize_alg(alg: Union[int, str]) -> Tuple[int, str]:
    if isinstance(alg, int):
        if not is_known_alg_id(alg) or not is_sig_alg_id(alg):
            raise ValueError(f"Unknown or non-signature alg_id: 0x{alg:02x}")
        return alg, ALG_NAME[alg]
    if isinstance(alg, str):
        name = alg.strip().lower()
        if name not in ALG_ID:
            raise ValueError(f"Unknown algorithm name: {alg!r}")
        alg_id = ALG_ID[name]
        if not is_sig_alg_id(alg_id):
            raise ValueError(f"Algorithm {name!r} is not a signature algorithm")
        return alg_id, name
    raise TypeError("alg must be int (alg_id) or str (name)")

# --------------------------------------------------------------------------------------
# Canonical SignBytes
# --------------------------------------------------------------------------------------

PrehashKind = Literal["sha3-512", "sha3-256"]

def build_sign_bytes(
    msg: bytes,
    *,
    domain: Union[str, bytes],
    chain_id: Optional[int],
    alg_id: int,
    context: bytes = b"",
    prehash: PrehashKind = "sha3-512",
) -> bytes:
    """
    Construct canonical SignBytes for Animica PQ signatures.

    Layout before prehash:
      TAG        = "animica:sign/v1"
      DOMAIN     = domain (bytes)
      CHAIN_ID?  = if provided (uvarint)
      ALG_ID     = uvarint(alg_id)
      CONTEXT    = freeform domain-specific bytes (e.g., tx-kind, header fields)
      MESSAGE    = the original message bytes

      sign_bytes_raw =
          len(TAG)||TAG
        ||len(DOMAIN)||DOMAIN
        ||len(CHAIN_ID_enc)?? (0 length if None)
        ||len(ALG_ID_enc)||ALG_ID_enc
        ||len(CONTEXT)||CONTEXT
        ||len(MESSAGE)||MESSAGE

    We then compute PH = SHA3-512(sign_bytes_raw) (or SHA3-256 if selected),
    and *that* digest is what gets signed by the PQ algorithm.
    """
    tag = b"animica:sign/v1"
    domain_b = _norm_domain(domain)
    if chain_id is None:
        chain_enc = b""
    else:
        chain_enc = _uvarint(chain_id)

    alg_enc = _uvarint(alg_id)

    raw = (
        _len_bytes(tag)
        + _len_bytes(domain_b)
        + _len_bytes(chain_enc)
        + _len_bytes(alg_enc)
        + _len_bytes(context)
        + _len_bytes(msg)
    )

    if prehash == "sha3-512":
        return sha3_512(raw)
    elif prehash == "sha3-256":
        return sha3_256(raw)
    else:
        raise ValueError(f"Unsupported prehash: {prehash}")

# --------------------------------------------------------------------------------------
# Signature & SignedMessage envelopes
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Signature:
    alg_id: int
    alg_name: str
    domain: str
    prehash: PrehashKind
    sig: bytes

    def __repr__(self) -> str:
        return (
            f"Signature(alg={self.alg_name}/0x{self.alg_id:02x}, "
            f"domain={self.domain!r}, prehash={self.prehash}, sig[:8]={self.sig[:8].hex()}…)"
        )

@dataclass(frozen=True)
class SignedMessage:
    message: bytes
    signature: Signature

# --------------------------------------------------------------------------------------
# Backend dispatcher
# --------------------------------------------------------------------------------------

def _backend_sign(alg_name: str, sk: bytes, msg: bytes) -> bytes:
    """
    Call the algorithm-specific signer. `msg` is already canonical SignBytes
    (a fixed-length digest), so backends should treat it as an opaque byte string.
    """
    try:
        if alg_name == "dilithium3":
            from pq.py.algs import dilithium3 as backend
        elif alg_name == "sphincs_shake_128s":
            from pq.py.algs import sphincs_shake_128s as backend
        else:
            raise NotImplementedError(f"Signature backend not wired for {alg_name}")
    except Exception as e:
        raise NotImplementedError(
            f"Signature backend for {alg_name} not available. "
            f"Install/build PQ backend (e.g., liboqs) and ensure wrappers are importable. ({e})"
        ) from e

    if not hasattr(backend, "sign"):
        raise NotImplementedError(f"Backend {backend.__name__} lacks .sign(secret_key, message)")
    return backend.sign(secret_key=sk, message=msg)  # type: ignore[arg-type]

# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

def sign_detached(
    msg: bytes,
    alg: Union[int, str],
    sk: bytes,
    *,
    domain: Union[str, bytes] = b"generic",
    chain_id: Optional[int] = None,
    context: bytes = b"",
    prehash: PrehashKind = "sha3-512",
) -> Signature:
    """
    Produce a detached signature envelope with strong domain separation.
    """
    alg_id, alg_name = _normalize_alg(alg)
    ph = build_sign_bytes(
        msg,
        domain=domain,
        chain_id=chain_id,
        alg_id=alg_id,
        context=context,
        prehash=prehash,
    )
    sig = _backend_sign(alg_name, sk, ph)
    domain_str = domain.decode("utf-8", "replace") if isinstance(domain, (bytes, bytearray)) else str(domain)
    return Signature(
        alg_id=alg_id,
        alg_name=alg_name,
        domain=domain_str,
        prehash=prehash,
        sig=sig,
    )

def sign_attached(
    msg: bytes,
    alg: Union[int, str],
    sk: bytes,
    *,
    domain: Union[str, bytes] = b"generic",
    chain_id: Optional[int] = None,
    context: bytes = b"",
    prehash: PrehashKind = "sha3-512",
) -> SignedMessage:
    """
    Return the original message plus a detached signature envelope.
    """
    return SignedMessage(
        message=msg,
        signature=sign_detached(
            msg,
            alg,
            sk,
            domain=domain,
            chain_id=chain_id,
            context=context,
            prehash=prehash,
        ),
    )

# --------------------------------------------------------------------------------------
# CLI smoke (python -m pq.py.sign <alg> <hex:sk> <hex:msg> [domain] [chain_id])
# --------------------------------------------------------------------------------------

def _parse_hex_arg(s: str) -> bytes:
    if not s.startswith("hex:"):
        raise ValueError("expected hex:…")
    return bytes.fromhex(s[4:].replace("_", "").replace(" ", ""))

def _main() -> None:
    import sys
    args = sys.argv[1:]
    if len(args) < 3 or args[0] in ("-h", "--help"):
        print(
            "Usage: python -m pq.py.sign <alg> <hex:sk> <hex:msg> [domain] [chain_id]\n"
            "  alg     = dilithium3 | sphincs_shake_128s | <alg_id int>\n"
            "  hex:sk  = secret key hex (backend-specific length)\n"
            "  hex:msg = message bytes hex (will be domain-prehashed before signing)\n"
            "  domain  = optional domain string (default 'generic')\n"
            "  chain_id= optional integer chain id (default none)\n"
        )
        sys.exit(0)

    alg_raw = args[0]
    alg_val: Union[int, str] = int(alg_raw) if alg_raw.isdigit() else alg_raw
    sk = _parse_hex_arg(args[1])
    msg = _parse_hex_arg(args[2])
    domain = args[3] if len(args) > 3 else "generic"
    chain_id = int(args[4]) if len(args) > 4 else None

    try:
        sig = sign_detached(msg, alg_val, sk, domain=domain, chain_id=chain_id)
    except Exception as e:
        print("sign failed:", e)
        sys.exit(2)

    print("alg:", sig.alg_name, f"(0x{sig.alg_id:02x})")
    print("domain:", sig.domain)
    print("prehash:", sig.prehash)
    print("sig:", sig.sig.hex())

if __name__ == "__main__":
    _main()
