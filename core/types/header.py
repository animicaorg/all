from __future__ import annotations

"""
Animica core/types/header.py
===========================

Canonical block-header representation matching spec/header_format.cddl.

Fields (stable, consensus-critical):
  - v:                small schema version (uint, default 1)
  - chainId:          uint (matches spec/chains.json; animica mainnet = 1)
  - height:           uint block height (genesis = 0)
  - parentHash:       bstr .size 32  (sha3_256 of parent header CBOR)
  - timestamp:        uint seconds since UNIX epoch (block producer clock; bounded in consensus)
  - stateRoot:        bstr .size 32  (post-state root after executing txs)
  - txsRoot:          bstr .size 32  (Merkle root of transactions, canonical order)
  - receiptsRoot:     bstr .size 32  (Merkle root of receipts/logs)
  - proofsRoot:       bstr .size 32  (Merkle root of PoIES proof receipts)
  - daRoot:           bstr .size 32  (Data Availability NMT root)
  - mixSeed:          bstr .size 32  (entropy mix for u-draw & lotteries, derived per-epoch)
  - poiesPolicyRoot:  bstr .size 32  (Merkle root of PoIES policy tree in effect)
  - pqAlgPolicyRoot:  bstr .size 32  (Merkle root of PQ algorithm-policy tree in effect)
  - thetaMicro:       uint (Θ) acceptance threshold in micro-nats (fixed-point)
  - nonce:            uint (producer-chosen nonce used in u-draw domain; 0 for headers in mempool)
  - extra:            bstr (opaque, bounded; non-consensus hints/notes; can be empty)

Notes:
- Hashing: headerHash = sha3_256(CBOR(Header)) — full header including nonce.
- Mining "sign-bytes": the PoW/PoIES u-draw domain excludes the `nonce` but includes a domain tag.
  Use Header.signing_preimage(domain_tag: bytes) to obtain canonical bytes for mining.
- This module does NOT implement policy/time-window checks — see consensus/validator.py.

"""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from core.utils.bytes import expect_len
from core.utils.hash import sha3_256
from core.encoding.cbor import cbor_dumps, cbor_loads

HASH32_LEN = 32
HEADER_VERSION = 1


@dataclass(frozen=True)
class Header:
    v: int
    chainId: int
    height: int
    parentHash: bytes
    timestamp: int

    stateRoot: bytes
    txsRoot: bytes
    receiptsRoot: bytes
    proofsRoot: bytes
    daRoot: bytes

    mixSeed: bytes

    poiesPolicyRoot: bytes
    pqAlgPolicyRoot: bytes

    thetaMicro: int
    nonce: int

    extra: bytes = b""

    # ---- construction helpers ----

    @staticmethod
    def genesis(
        *,
        chain_id: int,
        timestamp: int,
        state_root: bytes,
        txs_root: bytes,
        receipts_root: bytes,
        proofs_root: bytes,
        da_root: bytes,
        mix_seed: bytes,
        poies_policy_root: bytes,
        pq_alg_policy_root: bytes,
        theta_micro: int,
        extra: bytes = b"",
    ) -> "Header":
        """Build a deterministic genesis header (height=0, parentHash=0x00..00, nonce=0)."""
        zero = b"\x00" * HASH32_LEN
        h = Header(
            v=HEADER_VERSION,
            chainId=chain_id,
            height=0,
            parentHash=zero,
            timestamp=timestamp,
            stateRoot=state_root,
            txsRoot=txs_root,
            receiptsRoot=receipts_root,
            proofsRoot=proofs_root,
            daRoot=da_root,
            mixSeed=mix_seed,
            poiesPolicyRoot=poies_policy_root,
            pqAlgPolicyRoot=pq_alg_policy_root,
            thetaMicro=theta_micro,
            nonce=0,
            extra=extra,
        )
        h._validate_sizes()
        return h

    def build_child(
        self,
        *,
        timestamp: int,
        state_root: bytes,
        txs_root: bytes,
        receipts_root: bytes,
        proofs_root: bytes,
        da_root: bytes,
        mix_seed: Optional[bytes] = None,
        poies_policy_root: Optional[bytes] = None,
        pq_alg_policy_root: Optional[bytes] = None,
        theta_micro: Optional[int] = None,
        nonce: int = 0,
        extra: bytes = b"",
    ) -> "Header":
        """
        Build a *template* for the next block referencing this header as parent.
        Policy roots / Θ / mixSeed default to inheriting current values unless provided.
        """
        child = Header(
            v=self.v,
            chainId=self.chainId,
            height=self.height + 1,
            parentHash=self.hash(),
            timestamp=timestamp,
            stateRoot=state_root,
            txsRoot=txs_root,
            receiptsRoot=receipts_root,
            proofsRoot=proofs_root,
            daRoot=da_root,
            mixSeed=mix_seed if mix_seed is not None else self.mixSeed,
            poiesPolicyRoot=poies_policy_root if poies_policy_root is not None else self.poiesPolicyRoot,
            pqAlgPolicyRoot=pq_alg_policy_root if pq_alg_policy_root is not None else self.pqAlgPolicyRoot,
            thetaMicro=theta_micro if theta_micro is not None else self.thetaMicro,
            nonce=nonce,
            extra=extra,
        )
        child._validate_sizes()
        return child

    # ---- invariants & sizes ----

    def _validate_sizes(self) -> None:
        expect_len(self.parentHash, HASH32_LEN, name="Header.parentHash")
        expect_len(self.stateRoot, HASH32_LEN, name="Header.stateRoot")
        expect_len(self.txsRoot, HASH32_LEN, name="Header.txsRoot")
        expect_len(self.receiptsRoot, HASH32_LEN, name="Header.receiptsRoot")
        expect_len(self.proofsRoot, HASH32_LEN, name="Header.proofsRoot")
        expect_len(self.daRoot, HASH32_LEN, name="Header.daRoot")
        expect_len(self.mixSeed, HASH32_LEN, name="Header.mixSeed")
        expect_len(self.poiesPolicyRoot, HASH32_LEN, name="Header.poiesPolicyRoot")
        expect_len(self.pqAlgPolicyRoot, HASH32_LEN, name="Header.pqAlgPolicyRoot")
        if not isinstance(self.extra, (bytes, bytearray)):
            raise TypeError("Header.extra must be bytes")

    # ---- hashing, CBOR, and sign-bytes ----

    def to_obj(self) -> Mapping[str, Any]:
        """
        Canonical map for CBOR encoding. Key names and ordering must remain stable.
        """
        return {
            "v": int(self.v),
            "chainId": int(self.chainId),
            "height": int(self.height),
            "parentHash": bytes(self.parentHash),
            "timestamp": int(self.timestamp),
            "stateRoot": bytes(self.stateRoot),
            "txsRoot": bytes(self.txsRoot),
            "receiptsRoot": bytes(self.receiptsRoot),
            "proofsRoot": bytes(self.proofsRoot),
            "daRoot": bytes(self.daRoot),
            "mixSeed": bytes(self.mixSeed),
            "poiesPolicyRoot": bytes(self.poiesPolicyRoot),
            "pqAlgPolicyRoot": bytes(self.pqAlgPolicyRoot),
            "thetaMicro": int(self.thetaMicro),
            "nonce": int(self.nonce),
            "extra": bytes(self.extra),
        }

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "Header":
        h = Header(
            v=int(o["v"]),
            chainId=int(o["chainId"]),
            height=int(o["height"]),
            parentHash=bytes(o["parentHash"]),
            timestamp=int(o["timestamp"]),
            stateRoot=bytes(o["stateRoot"]),
            txsRoot=bytes(o["txsRoot"]),
            receiptsRoot=bytes(o["receiptsRoot"]),
            proofsRoot=bytes(o["proofsRoot"]),
            daRoot=bytes(o["daRoot"]),
            mixSeed=bytes(o["mixSeed"]),
            poiesPolicyRoot=bytes(o["poiesPolicyRoot"]),
            pqAlgPolicyRoot=bytes(o["pqAlgPolicyRoot"]),
            thetaMicro=int(o["thetaMicro"]),
            nonce=int(o["nonce"]),
            extra=bytes(o.get("extra", b"")),
        )
        h._validate_sizes()
        if h.v != HEADER_VERSION:
            # Allow forward-compat readers to decide; here we enforce equality for now.
            raise ValueError(f"Unsupported header version {h.v}, expected {HEADER_VERSION}")
        return h

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    @staticmethod
    def from_cbor(b: bytes) -> "Header":
        return Header.from_obj(cbor_loads(b))

    def hash(self) -> bytes:
        """Consensus header hash (block id): sha3_256(CBOR(header))."""
        return sha3_256(self.to_cbor())

    def signing_preimage(self, domain_tag: bytes) -> bytes:
        """
        Preimage used for PoW u-draw / miner nonce domain:

            preimage = CBOR({
               v, chainId, height, parentHash, timestamp,
               stateRoot, txsRoot, receiptsRoot, proofsRoot, daRoot,
               mixSeed, poiesPolicyRoot, pqAlgPolicyRoot, thetaMicro,
               extra,                                     # included
               domainTag: bstr                            # additional tag
            })

        The `nonce` is *excluded* so miners can vary it externally. `domain_tag` must
        match spec/domains.yaml (e.g., "mining.preimage" personalization).
        """
        obj = {
            "v": int(self.v),
            "chainId": int(self.chainId),
            "height": int(self.height),
            "parentHash": bytes(self.parentHash),
            "timestamp": int(self.timestamp),
            "stateRoot": bytes(self.stateRoot),
            "txsRoot": bytes(self.txsRoot),
            "receiptsRoot": bytes(self.receiptsRoot),
            "proofsRoot": bytes(self.proofsRoot),
            "daRoot": bytes(self.daRoot),
            "mixSeed": bytes(self.mixSeed),
            "poiesPolicyRoot": bytes(self.poiesPolicyRoot),
            "pqAlgPolicyRoot": bytes(self.pqAlgPolicyRoot),
            "thetaMicro": int(self.thetaMicro),
            "extra": bytes(self.extra),
            "domainTag": bytes(domain_tag),
        }
        return cbor_dumps(obj)

    # ---- convenience ----

    def with_nonce(self, nonce: int) -> "Header":
        """Return a copy with a different nonce."""
        return replace(self, nonce=nonce)

    def pretty(self) -> str:
        """Human-oriented single-line summary (non-consensus)."""
        def h(x: bytes) -> str:
            return x.hex()[:8]
        return (
            f"Header(v={self.v} cid={self.chainId} h={self.height} t={self.timestamp} "
            f"parent={h(self.parentHash)} state={h(self.stateRoot)} txs={h(self.txsRoot)} "
            f"rcpt={h(self.receiptsRoot)} proofs={h(self.proofsRoot)} da={h(self.daRoot)} "
            f"mix={h(self.mixSeed)} Θμ={self.thetaMicro} nonce={self.nonce})"
        )


# Dev self-check
if __name__ == "__main__":  # pragma: no cover
    z = b"\x00" * 32
    hdr = Header.genesis(
        chain_id=1,
        timestamp=1_700_000_000,
        state_root=z,
        txs_root=z,
        receipts_root=z,
        proofs_root=z,
        da_root=z,
        mix_seed=(b"\x42" * 32),
        poies_policy_root=(b"\x11" * 32),
        pq_alg_policy_root=(b"\x22" * 32),
        theta_micro=1_000_000,  # ~1.0 in micro-nats (example)
        extra=b"animica-devnet",
    )
    pre = hdr.signing_preimage(b"mining.preimage")
    assert isinstance(pre, (bytes, bytearray))
    h = hdr.hash()
    print("genesis header ok", h.hex()[:16], "…", hdr.pretty())
