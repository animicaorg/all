from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .merkle import Proof, ProofStep, merkle_proof, merkle_root, verify_proof


@dataclass(frozen=True)
class PCPProofResult:
    leaf_hash: bytes
    root: bytes
    proof: Proof


def compute_pcp_root(hashes: Sequence[bytes]) -> bytes:
    return merkle_root(hashes)


def build_proof(hashes: Sequence[bytes], index: int) -> PCPProofResult:
    leaf = hashes[index]
    root = merkle_root(hashes)
    proof = merkle_proof(hashes, index)
    return PCPProofResult(leaf_hash=leaf, root=root, proof=proof)


def verify_pcp_proof(leaf_hash: bytes, proof: Iterable[ProofStep], root: bytes) -> bool:
    return verify_proof(leaf_hash, proof, root)


def hash_payload(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()
