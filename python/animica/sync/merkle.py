from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple


Hash = bytes


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def merkle_root(leaves: Sequence[Hash]) -> Hash:
    if not leaves:
        return b""
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level: List[Hash] = []
        for i in range(0, len(level), 2):
            next_level.append(sha256(level[i] + level[i + 1]))
        level = next_level
    return level[0]


@dataclass(frozen=True)
class ProofStep:
    sibling: Hash
    direction: str  # "left" or "right"


Proof = Tuple[ProofStep, ...]


def merkle_proof(leaves: Sequence[Hash], index: int) -> Proof:
    if index < 0 or index >= len(leaves):
        raise IndexError("leaf index out of range")
    proof: List[ProofStep] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sibling_index = idx ^ 1
        direction = "left" if sibling_index < idx else "right"
        proof.append(ProofStep(level[sibling_index], direction))
        idx //= 2
        next_level: List[Hash] = []
        for i in range(0, len(level), 2):
            next_level.append(sha256(level[i] + level[i + 1]))
        level = next_level
    return tuple(proof)


def verify_proof(leaf: Hash, proof: Iterable[ProofStep], root: Hash) -> bool:
    value = leaf
    for step in proof:
        if step.direction == "left":
            value = sha256(step.sibling + value)
        else:
            value = sha256(value + step.sibling)
    return value == root
