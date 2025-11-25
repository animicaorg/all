from __future__ import annotations

"""
Animica core/types/block.py
==========================

Canonical Block container:
  - header:  core.types.header.Header
  - txs:     list[core.types.tx.Tx]
  - proofs:  list[ProofRef] (HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef)
  - receipts (optional): list[core.types.receipt.Receipt] (same order as txs)

Roots in Header must match:
  - txsRoot       = Merkle(tx.hash() for tx in txs)
  - receiptsRoot  = Merkle(receipt.hash() for receipt in receipts) or zero32 if not present
  - proofsRoot    = Merkle(sha3_256(proof.to_cbor()) for proof in proofs) or zero32 if empty

Block id/hash = header.hash()  (consensus identifier)

This module provides:
  - Block dataclass with canonical CBOR (to_cbor / from_cbor)
  - Root (re)computation and header consistency verification
  - Constructors from components with automatic root checks
"""

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union, cast

from core.utils.hash import sha3_256, ZERO32
from core.utils.merkle import merkle_root
from core.encoding.cbor import cbor_dumps, cbor_loads
from core.types.header import Header
from core.types.tx import Tx
from core.types.receipt import Receipt
from core.types.proof import (
    HashShare,
    AIProofRef,
    QuantumProofRef,
    StorageHeartbeat,
    VDFProofRef,
)

# Union of all proof envelope types included in a block
ProofLike = Union[HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef]


@dataclass(frozen=True)
class Block:
    header: Header
    txs: Tuple[Tx, ...]
    proofs: Tuple[ProofLike, ...]
    receipts: Optional[Tuple[Receipt, ...]] = None

    # ------------ hashes & roots ------------

    def id(self) -> bytes:
        """Consensus block identifier (alias of header.hash())."""
        return self.header.hash()

    def txs_root(self) -> bytes:
        if not self.txs:
            return ZERO32
        leaves = [tx.hash() for tx in self.txs]
        return merkle_root(leaves)

    def receipts_root(self) -> bytes:
        if not self.receipts:
            return ZERO32
        leaves = [rcpt.hash() for rcpt in self.receipts]
        return merkle_root(leaves) if leaves else ZERO32

    def proofs_root(self) -> bytes:
        if not self.proofs:
            return ZERO32
        # Canonical leaf material for proofs is their CBOR envelope bytes.
        leaves = [sha3_256(_proof_to_cbor(p)) for p in self.proofs]
        return merkle_root(leaves) if leaves else ZERO32

    # ------------ consistency checks ------------

    def verify_against_header(self) -> None:
        """
        Recompute roots and ensure they match header; check receipts length if present.
        Raises ValueError on mismatch.
        """
        # Receipts length check (if present)
        if self.receipts is not None and len(self.receipts) != len(self.txs):
            raise ValueError(
                f"receipts length {len(self.receipts)} != txs length {len(self.txs)}"
            )

        txr = self.txs_root()
        rcr = self.receipts_root()
        pfr = self.proofs_root()

        if txr != self.header.txsRoot:
            raise ValueError(
                f"txsRoot mismatch: computed {txr.hex()} header {self.header.txsRoot.hex()}"
            )
        if rcr != self.header.receiptsRoot:
            raise ValueError(
                f"receiptsRoot mismatch: computed {rcr.hex()} header {self.header.receiptsRoot.hex()}"
            )
        if pfr != self.header.proofsRoot:
            raise ValueError(
                f"proofsRoot mismatch: computed {pfr.hex()} header {self.header.proofsRoot.hex()}"
            )

    # ------------ canonical object & CBOR ------------

    def to_obj(self) -> Mapping[str, Any]:
        """
        Canonical map for CBOR encoding.
        Use nested objects for readability (stable key set/order).
        """
        o: Mapping[str, Any] = {
            "header": self.header.to_obj(),
            "txs": [tx.to_obj() for tx in self.txs],
            "proofs": [_proof_to_obj(p) for p in self.proofs],
            # receipts omitted if None
        }
        if self.receipts is not None:
            o = {**o, "receipts": [r.to_obj() for r in self.receipts]}
        return o

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "Block":
        header = Header.from_obj(cast(Mapping[str, Any], o["header"]))
        txs = tuple(Tx.from_obj(x) for x in cast(Sequence[Mapping[str, Any]], o["txs"]))
        proofs = tuple(_proof_from_obj(x) for x in cast(Sequence[Mapping[str, Any]], o["proofs"]))
        receipts_field = cast(Optional[Sequence[Mapping[str, Any]]], o.get("receipts"))
        receipts = tuple(Receipt.from_obj(x) for x in receipts_field) if receipts_field is not None else None

        blk = Block(header=header, txs=txs, proofs=proofs, receipts=receipts)
        blk.verify_against_header()
        return blk

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    @staticmethod
    def from_cbor(b: bytes) -> "Block":
        return Block.from_obj(cbor_loads(b))

    # ------------ constructors ------------

    @staticmethod
    def from_components(
        *,
        header: Header,
        txs: Iterable[Tx],
        proofs: Iterable[ProofLike],
        receipts: Optional[Iterable[Receipt]] = None,
        verify: bool = True,
    ) -> "Block":
        """
        Build a Block from parts. If `verify` is True (default), recompute roots and
        assert equality with header before returning.
        """
        blk = Block(
            header=header,
            txs=tuple(txs),
            proofs=tuple(proofs),
            receipts=(tuple(receipts) if receipts is not None else None),
        )
        if verify:
            blk.verify_against_header()
        return blk

    # ------------ size helpers (non-consensus) ------------

    def encoded_size(self) -> int:
        """Approximate size in bytes of CBOR-encoded block."""
        return len(self.to_cbor())

    def counts(self) -> Mapping[str, int]:
        """Small summary counts (non-consensus)."""
        return {
            "txs": len(self.txs),
            "proofs": len(self.proofs),
            "receipts": 0 if self.receipts is None else len(self.receipts),
        }


# ---------- internal helpers for proof envelopes ----------

def _proof_to_obj(p: ProofLike) -> Mapping[str, Any]:
    # Each proof dataclass is expected to implement to_obj(); fall back to to_cbor + tag if needed.
    if hasattr(p, "to_obj"):
        return cast(Mapping[str, Any], getattr(p, "to_obj")())
    # Fallback: wrap raw CBOR with a minimal tagged envelope (should not happen in normal flow).
    return {"_rawCbor": _proof_to_cbor(p)}

def _proof_from_obj(o: Mapping[str, Any]) -> ProofLike:
    # Dispatch by a discriminant key present in each proof ref's object form.
    # We assume each to_obj includes a "type" or "kind" field (as defined in core/types/proof.py).
    kind = o.get("type") or o.get("kind")
    if kind == "hash":
        return HashShare.from_obj(o)
    if kind == "ai":
        return AIProofRef.from_obj(o)
    if kind == "quantum":
        return QuantumProofRef.from_obj(o)
    if kind == "storage":
        return StorageHeartbeat.from_obj(o)
    if kind == "vdf":
        return VDFProofRef.from_obj(o)
    # Fallback: raw CBOR path (dev-only)
    raw = cast(Optional[bytes], o.get("_rawCbor"))
    if raw is None:
        raise ValueError("Unknown proof object shape (missing kind/type)")
    # Try to decode by probing the 'type' inside the CBOR map again after load:
    inner = cast(Mapping[str, Any], cbor_loads(raw))
    return _proof_from_obj(inner)

def _proof_to_cbor(p: ProofLike) -> bytes:
    if hasattr(p, "to_cbor"):
        return cast(bytes, getattr(p, "to_cbor")())
    return cbor_dumps(_proof_to_obj(p))  # last-resort fallback


# ---------- dev self-test ----------

if __name__ == "__main__":  # pragma: no cover
    # Tiny smoke check: build an empty-ish block and see roots align.
    from core.types.header import Header
    from core.utils.hash import ZERO32

    hdr = Header.genesis(
        chain_id=1,
        timestamp=1_700_000_123,
        state_root=ZERO32,
        txs_root=ZERO32,
        receipts_root=ZERO32,
        proofs_root=ZERO32,
        da_root=ZERO32,
        mix_seed=b"\x42" * 32,
        poies_policy_root=b"\x11" * 32,
        pq_alg_policy_root=b"\x22" * 32,
        theta_micro=1_000_000,
        extra=b"",
    )

    blk = Block.from_components(header=hdr, txs=(), proofs=(), receipts=None, verify=True)
    print("block ok:", blk.id().hex()[:16], blk.counts(), "size=", blk.encoded_size())
