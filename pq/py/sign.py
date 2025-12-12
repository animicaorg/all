from __future__ import annotations

"""
sign.py — Domain-tag signing API aligned with spec/domains.yaml.

Spec summary:
- DomainTag = b"ANM|" + chainId_ascii + b"|" + path_ascii + b"|v{version}"
- SignBytes = DomainTag || CanonicalEncode(payload)

For transactions, payload is canonical CBOR(body) produced by python/animica/tx/signing.py.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Tuple, Union

from pq.py.registry import ALG_ID, ALG_NAME, is_known_alg_id, is_sig_alg_id
from pq.py.utils.hash import sha3_256, sha3_512

PrehashKind = Literal["none", "sha3-512", "sha3-256"]

_PREFIX = b"ANM|"


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


def _alg_family_for_domain(alg_name: str) -> str:
    # spec/domains.yaml uses "sphincs" (not the full variant name)
    if alg_name == "dilithium3":
        return "dilithium3"
    if alg_name.startswith("sphincs"):
        return "sphincs"
    # fallback: keep whatever the registry says (better than guessing wrong)
    return alg_name


def _normalize_domain_path(domain: Union[str, bytes], *, alg_name: str) -> bytes:
    """
    Accept either:
      - full path like "sig|dilithium3|tx"
      - shorthand like "tx" or "header" (expanded per algorithm family)
    """
    if isinstance(domain, (bytes, bytearray)):
        d = bytes(domain).strip()
    elif isinstance(domain, str):
        d = domain.strip().encode("ascii", "strict")
    else:
        raise TypeError("domain must be str|bytes")

    if not d:
        raise ValueError("domain must be non-empty")

    # already a full tag?
    if d.startswith(_PREFIX):
        # caller provided a full DomainTag already
        return d

    # already a path with pipes?
    if b"|" in d:
        return d

    # shorthand expansions
    fam = _alg_family_for_domain(alg_name)
    if d == b"tx":
        return f"sig|{fam}|tx".encode("ascii")
    if d == b"header":
        return f"sig|{fam}|header".encode("ascii")

    # default: treat as a path segment
    return d


def build_domain_tag(
    *,
    chain_id: int,
    domain: Union[str, bytes],
    alg_name: str,
    version: int = 1,
) -> bytes:
    """
    Build DomainTag per spec/domains.yaml:
      b"ANM|" + b"animica:{chain_id}" + b"|" + domain_path + b"|v{version}"
    """
    path = _normalize_domain_path(domain, alg_name=alg_name)

    # If caller passed a full DomainTag, return as-is
    if path.startswith(_PREFIX):
        return path

    chain_ascii = f"animica:{int(chain_id)}".encode("ascii")
    ver_ascii = f"v{int(version)}".encode("ascii")

    return _PREFIX + chain_ascii + b"|" + path + b"|" + ver_ascii


def build_sign_bytes(
    msg: bytes,
    *,
    domain: Union[str, bytes],
    chain_id: Optional[int],
    alg_id: int,
    context: bytes = b"",
    prehash: PrehashKind = "none",
) -> bytes:
    """
    Construct canonical SignBytes = DomainTag || context? || msg.

    - DomainTag is chain-bound (replay-safe) and versioned.
    - `context` (if non-empty) is appended as: u32be(len(context))||context.
      (Not currently used by tx signing, but kept deterministic.)
    """
    if not isinstance(msg, (bytes, bytearray, memoryview)):
        raise TypeError("msg must be bytes-like")

    if chain_id is None:
        raise ValueError("chain_id is required for Animica domain-tag signing")

    alg_name = ALG_NAME[alg_id]
    domain_tag = build_domain_tag(chain_id=int(chain_id), domain=domain, alg_name=alg_name, version=1)

    payload = domain_tag
    if context:
        if not isinstance(context, (bytes, bytearray, memoryview)):
            raise TypeError("context must be bytes-like")
        n = len(context)
        payload += n.to_bytes(4, "big") + bytes(context)

    payload += bytes(msg)

    if prehash == "none":
        return payload
    if prehash == "sha3-512":
        return sha3_512(payload)
    if prehash == "sha3-256":
        return sha3_256(payload)

    raise ValueError(f"Unsupported prehash: {prehash}")


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


def _backend_sign(alg_name: str, sk: bytes, msg: bytes) -> bytes:
    try:
        if alg_name == "dilithium3":
            from pq.py.algs import dilithium3 as backend
        elif alg_name.startswith("sphincs"):
            from pq.py.algs import sphincs_shake_128s as backend
        else:
            raise NotImplementedError(f"Signature backend not wired for {alg_name}")
    except Exception as e:
        raise NotImplementedError(
            f"Signature backend for {alg_name} not available. ({e})"
        ) from e

    if not hasattr(backend, "sign"):
        raise NotImplementedError(f"Backend {backend.__name__} lacks .sign(secret_key, message)")

    return backend.sign(sk, msg)  # type: ignore[arg-type]


def sign_detached(
    msg: bytes,
    alg: Union[int, str],
    sk: bytes,
    *,
    domain: Union[str, bytes] = b"tx",
    chain_id: Optional[int] = None,
    context: bytes = b"",
    prehash: PrehashKind = "none",
) -> Signature:
    alg_id, alg_name = _normalize_alg(alg)
    sign_bytes = build_sign_bytes(
        bytes(msg),
        domain=domain,
        chain_id=chain_id,
        alg_id=alg_id,
        context=context,
        prehash=prehash,
    )
    sig_bytes = _backend_sign(alg_name, sk, sign_bytes)

    # store the *normalized path* string for verification
    dom_norm = _normalize_domain_path(domain, alg_name=alg_name)
    dom_str = dom_norm.decode("ascii", "replace")

    return Signature(
        alg_id=alg_id,
        alg_name=alg_name,
        domain=dom_str,
        prehash=prehash,
        sig=sig_bytes,
    )


def sign_attached(
    msg: bytes,
    alg: Union[int, str],
    sk: bytes,
    *,
    domain: Union[str, bytes] = b"tx",
    chain_id: Optional[int] = None,
    context: bytes = b"",
    prehash: PrehashKind = "none",
) -> SignedMessage:
    return SignedMessage(
        message=bytes(msg),
        signature=sign_detached(
            bytes(msg),
            alg,
            sk,
            domain=domain,
            chain_id=chain_id,
            context=context,
            prehash=prehash,
        ),
    )
