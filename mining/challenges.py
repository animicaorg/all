from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict

try:
    from proofs.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


@dataclass(frozen=True)
class Challenge:
    proof_type: str
    epoch: int
    seed: bytes
    created_at: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "proofType": self.proof_type,
            "epoch": self.epoch,
            "seed": "0x" + self.seed.hex(),
            "createdAt": int(self.created_at),
        }


def derive_epoch(parent_height: int, *, epoch_len: int = 1000) -> int:
    return int(parent_height) // max(1, int(epoch_len))


def derive_challenge(
    *,
    chain_id: int,
    parent_hash: bytes,
    parent_height: int,
    proof_type: str,
    epoch_len: int = 1000,
) -> Challenge:
    epoch = derive_epoch(parent_height, epoch_len=epoch_len)
    payload = (
        str(chain_id).encode()
        + b"|"
        + parent_hash
        + b"|"
        + str(parent_height).encode()
        + b"|"
        + str(epoch).encode()
        + b"|"
        + proof_type.encode()
    )
    seed = sha3_256(payload)
    return Challenge(
        proof_type=proof_type,
        epoch=epoch,
        seed=seed,
        created_at=int(time.time()),
    )


__all__ = ["Challenge", "derive_challenge", "derive_epoch"]
