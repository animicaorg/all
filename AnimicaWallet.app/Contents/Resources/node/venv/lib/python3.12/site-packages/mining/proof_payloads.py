from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .challenges import Challenge

try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


@dataclass(frozen=True)
class ProofPayload:
    proof_type: str
    commitment: bytes
    witness: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proofType": self.proof_type,
            "commitment": "0x" + self.commitment.hex(),
            "witness": self.witness,
        }


def _commitment(challenge: Challenge, output_digest: bytes, metrics: Dict[str, Any]) -> bytes:
    payload = (
        challenge.seed
        + output_digest
        + str(sorted(metrics.items())).encode()
        + challenge.proof_type.encode()
    )
    return sha3_256(payload)


def build_payload(
    *,
    challenge: Challenge,
    output_digest: bytes,
    metrics: Dict[str, Any],
) -> ProofPayload:
    commit = _commitment(challenge, output_digest, metrics)
    witness = {
        "challenge": challenge.to_dict(),
        "outputDigest": "0x" + output_digest.hex(),
        "metrics": metrics,
    }
    return ProofPayload(
        proof_type=challenge.proof_type,
        commitment=commit,
        witness=witness,
    )


def verify_payload(payload: ProofPayload) -> bool:
    witness = payload.witness
    challenge_dict = witness.get("challenge") or {}
    seed_hex = challenge_dict.get("seed", "0x")
    if isinstance(seed_hex, str) and seed_hex.startswith("0x"):
        seed = bytes.fromhex(seed_hex[2:])
    else:
        return False
    output_hex = witness.get("outputDigest", "0x")
    if isinstance(output_hex, str) and output_hex.startswith("0x"):
        output_digest = bytes.fromhex(output_hex[2:])
    else:
        return False
    metrics = witness.get("metrics") or {}
    challenge = Challenge(
        proof_type=str(challenge_dict.get("proofType")),
        epoch=int(challenge_dict.get("epoch") or 0),
        seed=seed,
        created_at=int(challenge_dict.get("createdAt") or 0),
    )
    return _commitment(challenge, output_digest, metrics) == payload.commitment


__all__ = ["ProofPayload", "build_payload", "verify_payload"]
