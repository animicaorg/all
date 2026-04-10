from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

try:
    from pq.py import verify as pq_verify  # type: ignore
except Exception:  # pragma: no cover
    pq_verify = None  # type: ignore


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None
    scheme_id: int
    pub_fingerprint: str
    sign_hash: str


@dataclass(frozen=True)
class SchemeSpec:
    pubkey_len: int
    sig_len: int
    verifier: Callable[[bytes, bytes, bytes], bool]


def _fp(pubkey: bytes) -> str:
    return sha256(pubkey).hexdigest()[:16]


def _verify_detached(msg: bytes, sig: bytes, pk: bytes) -> bool:
    if pq_verify is None:
        return False
    return bool(pq_verify.verify_detached(msg, sig, pk))  # type: ignore[attr-defined]


SCHEMES: dict[int, SchemeSpec] = {
    0x1001: SchemeSpec(pubkey_len=1952, sig_len=3293, verifier=_verify_detached),
    0x2001: SchemeSpec(pubkey_len=64, sig_len=49856, verifier=_verify_detached),
}


def verify(*, alg_id: int, msg: bytes, signature: bytes, pubkey: bytes, sign_hash: bytes) -> VerifyResult:
    spec = SCHEMES.get(int(alg_id))
    if spec is None:
        return VerifyResult(False, "invalid_signature", int(alg_id), _fp(pubkey), sign_hash.hex()[:24])
    if len(pubkey) != spec.pubkey_len or len(signature) != spec.sig_len:
        return VerifyResult(False, "invalid_signature", int(alg_id), _fp(pubkey), sign_hash.hex()[:24])
    ok = spec.verifier(msg, signature, pubkey)
    return VerifyResult(bool(ok), None if ok else "invalid_signature", int(alg_id), _fp(pubkey), sign_hash.hex()[:24])
