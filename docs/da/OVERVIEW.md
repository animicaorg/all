# Data Availability (DA): Blobs, Commitments, and NMT Roots

This document explains how Animica publishes **application data** (“blobs”) so that full nodes and light clients can verify **availability** without downloading the entire blob. It introduces blob envelopes, **namespaced Merkle trees (NMTs)**, commitment rules, and how the **DA root** is integrated into block headers and verified via sampling proofs.

> See module sources in `da/` and specs in `da/specs/*`. Wire/API schemas live in `da/schemas/*` and test vectors in `da/test_vectors/*`.

---

## Goals

- **Integrity & Namespacing:** Commit to many blobs in a block while preserving per-namespace inclusion proofs.
- **Availability:** Let nodes/light clients verify that posted blobs are *retrievable* with high probability via **Data Availability Sampling (DAS)**.
- **Compactness:** One **DA root** per block header captures all blob leaves for the block.
- **Interoperability:** Canonical encodings (CBOR + length-prefixes) and deterministic hashing (SHA3-based) ensure cross-impl reproducibility.

---

## Key Concepts

### Blobs
A **blob** is an opaque byte sequence tagged with a **namespace id** (uint32..uint64 range configured by network policy). Typical uses:
- Contract bytecode, large calldata, artifact bundles
- ZK proof artifacts, AI/quantum result manifests
- Off-chain content-addressed files (with receipts)

**Envelope (simplified):**
```text
BlobRef {
  namespace: u32|u64,
  mime: bytes?,
  size: u64,
  data: bytes                         # not stored in headers; goes to DA store
}

See da/schemas/blob.cddl for the full envelope and chunk layout.

Namespaces

Namespaces partition the leaf space so clients can request only the ranges they care about (e.g., contracts/*, zk/*). Namespace ranges are configured in da/constants.py and validated by da/nmt/namespace.py.

Namespaced Merkle Tree (NMT)

An NMT extends a standard Merkle tree so each internal node carries:
	•	min_ns: minimum namespace among leaves under this node
	•	max_ns: maximum namespace among leaves under this node
	•	digest: hash(left || right || min_ns || max_ns)

This allows namespace-range proofs (e.g., “all leaves for namespace 24 are present”).

Leaf serialization (canonical):

leaf = encode(namespace) || encode(varlen(data)) || data

Exact rules and CBOR details: da/nmt/codec.py and da/schemas/nmt.cddl.

⸻

Commitments & DA Root

Blob → Leaves

Blobs are chunked and may be erasure-coded (see below). Each chunk becomes a leaf tagged with the blob’s namespace.

Tree Build

All leaves for the block are appended in namespace-sorted order. The finalized tree root is the DA root.

da_root = NMT.build(leaves_sorted_by_ns).root

Included in the block header (see spec/header_format.cddl and da/adapters/core_chain.py).

Commit API

The DA module exposes:
	•	commit(blob) -> (commitment, size, namespace) (see da/blob/commitment.py)
	•	post/get/proof HTTP endpoints (da/retrieval/api.py) for blob publication and proof retrieval
	•	Client (da/retrieval/client.py) used by SDKs and services

Determinism: All hashes use SHA3-256/512 under domain tags (see da/utils/hash.py).

⸻

Erasure Coding (Optional but Recommended)

To tolerate withholding/corruption, blobs are split into k data shards and extended to n shards using Reed–Solomon (da/erasure/reedsolomon.py). The extended shard set is then namespaced and committed into the NMT.
	•	Parameters: da/erasure/params.py
	•	Partitioning: da/erasure/partitioner.py
	•	Extended layout / indices: da/erasure/layout.py and da/nmt/indices.py

Recovery property: Any k of n shards (with valid NMT proofs) suffice to reconstruct the original blob.

⸻

Proofs

Inclusion Proof

Prove a specific leaf (shard/chunk) is in the committed tree.

verify_inclusion(leaf, branch, da_root) -> bool

Namespace-Range Proof

Prove that all leaves for namespace ns are present (or that none exist). The proof spans a minimal set of subtrees whose namespace ranges cover exactly ns.

verify_namespace_range(ns, proof, da_root) -> bool

Verification is implemented in da/nmt/verify.py. Proof schemas live in da/schemas/availability_proof.cddl.

⸻

Data Availability Sampling (DAS)

Light clients achieve high confidence that all data is available by sampling random shares (indices) and verifying their inclusion and namespace coverage.
	1.	Sampling plan picks random (row, col) or linear indices (da/sampling/queries.py).
	2.	Fetch shares + NMT branches via retrieval API.
	3.	Verify inclusion + namespace-range proofs (da/sampling/verifier.py).
	4.	If enough random samples succeed, availability holds with probability 1 - p_fail.
	•	Math and bounds: da/erasure/availability_math.py, da/sampling/probability.py

Light client flow: da/sampling/light_client.py glues header’s da_root with sample verification.

⸻

Header Integration

Block headers include:
	•	da_root: NMT root for all (extended) leaves posted this block
	•	Optionally per-blob receipts (commitments) in block body and proofsRoot for cross-module links

Validation path:
	•	Full nodes build/validate the NMT during block assembly, persist leaves/shards in da/blob/store.py.
	•	Light clients trust only headers + stateless DAS proofs to accept availability.

See spec/blob_format.cddl, spec/header_format.cddl, and da/adapters/core_chain.py.

⸻

REST API (Retrieval Service)

da/retrieval/api.py (FastAPI):
	•	POST /da/blob
Body: blob envelope; returns commitment, namespace, size, receipt
	•	GET /da/blob/{commitment}
Returns the raw blob bytes (streamed)
	•	GET /da/proof?commitment=...&indices=...
Returns inclusion/namespace-range proofs for requested shares

Client Libraries
	•	Python SDK: sdk/python/omni_sdk/da/client.py
	•	TypeScript SDK: sdk/typescript/src/da/client.ts

⸻

Security Considerations
	•	Namespace correctness is enforced during build; invalid ranges cause rejection.
	•	Canonicalization (CBOR + deterministic hashing) prevents malleability.
	•	DoS controls: Request size limits, per-IP tokens, and caching (da/retrieval/rate_limit.py, da/retrieval/cache.py).
	•	Availability parameters (k, n, sample count) tune bandwidth vs confidence:
	•	Pick k/n and sampling targets per network policy; see da/specs/DAS.md.

⸻

Parameters & Limits
	•	Max blob size and shard size configured in da/constants.py.
	•	Namespace id width network-defined (32 or 64 bits).
	•	Erasure profile (k, n) per chain (da/erasure/params.py).
	•	Sampling policy: periodic, target p_fail (da/sampling/scheduler.py).

⸻

Quick Reference

Build a commitment (developer flow)

from da.blob.commitment import commit_blob
commitment, size, ns = commit_blob(namespace=24, data=b"...")

Verify namespace-range proof (client)

from da.nmt.verify import verify_namespace_range
ok = verify_namespace_range(ns=24, proof=proof_obj, root=da_root)

Simulate DAS

python -m da.cli.sim_sample --commit <hex> --samples 256


⸻

Test Vectors & Bench
	•	NMT: da/test_vectors/nmt.json
	•	Erasure: da/test_vectors/erasure.json
	•	Availability/Sampling: da/test_vectors/availability.json
	•	Benchmarks: da/bench/* (NMT build, RS encode/decode)

⸻

Further Reading
	•	da/specs/NMT.md — NMT design, encodings, proofs
	•	da/specs/ERASURE.md — RS layout & parameters
	•	da/specs/DAS.md — sampling protocol & math
	•	spec/blob_format.cddl, spec/header_format.cddl — consensus-facing encodings

