# Data Availability (DA) — Specs, Pointers & Rationale

This document is the index for Animica’s DA subsystem: how blobs are committed, encoded, proven available, and referenced from block headers. It links to the detailed specs and explains the security model and key invariants at a glance.

---

## What DA does in Animica

- **Purpose:** Make large user data (contracts, calldata, artifacts) *available* to the network without forcing full nodes to download every byte at validation time.
- **Header binding:** Each block header contains a **DA root** (an NMT root) that commits to all blobs included in the block.
- **Light verification:** Nodes and light clients obtain probabilistic availability guarantees via *data availability sampling (DAS)* with succinct **namespace-range proofs**.
- **Determinism & portability:** All on-wire formats are **deterministic CBOR**, specified via CDDL and JSON Schema to ensure stable hashing, signatures, and reproducible proofs.

---

## Spec pointers

- **NMT design & proofs:** [`NMT.md`](./NMT.md)  
  Namespaced Merkle Tree structure, leaf/node encodings, namespace ordering rules, inclusion and namespace-range proofs, and verification algorithms.

- **Erasure coding & layout:** [`ERASURE.md`](./ERASURE.md)  
  Reed–Solomon profiles, shard sizes, padding/zero rules, extended matrix layout, and recovery constraints.

- **Data Availability Sampling:** [`DAS.md`](./DAS.md)  
  Sampling strategies, target failure probability \(p_\text{fail}\), proof composition, and light-client flow.

**Schemas (wire formats):**
- CBOR/JSON schemas live under `da/schemas/`:
  - `blob.cddl` — blob envelope & chunk layout  
  - `nmt.cddl` — NMT node/leaf encodings  
  - `availability_proof.cddl` — DAS proof envelope  
  - `retrieval_api.schema.json` — public REST surface for DA retrieval

---

## Trust & threat model

- **Adversary:** Up to a minority of peers may be Byzantine (withholding blobs, serving inconsistent ranges, or forging proofs).  
- **Goal:** Honest nodes/light clients achieve very high confidence that *most* blob bytes referenced by adopted headers are available for download within protocol time bounds.
- **Assumptions:**  
  - Hash and AEAD primitives are sound (SHA3-256/512, ChaCha20-Poly1305/AES-GCM when relevant).  
  - Merkle and NMT collision resistance holds.  
  - DAS parameters are set so that \( \Pr[\text{undetected unavailability}] \le p_\text{fail} \) for the chosen matrix dimensions and sample counts.  
- **Non-goals:** DA does not attest semantic correctness of data, only *availability* matching the header’s DA root.

---

## Components & flow (high level)

1. **Blob submission**  
   Client posts a blob: it is chunked, optionally **erasure-encoded** (RS \(k,n\)), turned into **namespaced leaves**, then committed into an **NMT** to produce the block’s **DA root**.

2. **Header binding**  
   The block header includes the **DA root** and namespace bounds. This root is signed/secured via the normal block validation flow.

3. **Retrieval & proofs**  
   - Full nodes store blobs (FS/SQLite) and serve **GET /da/blob/{commitment}** and **GET /da/proof** with inclusion/range proofs.  
   - Light clients **sample** random positions; for each sample they fetch leaf bytes + **proof branches** and verify against the header DA root.

4. **Availability decision**  
   If all requested samples verify and meet coverage thresholds, the client treats the blob set as *available* with failure probability bounded by the configured \(p_\text{fail}\).

---

## Invariants

- **Namespace ordering:** Leaves are ordered by their 32-bit namespace id; internal nodes carry a min/max namespace range. Range proofs must respect these bounds. *(See `NMT.md`.)*
- **Deterministic encoding:**  
  - `blob.cddl` defines the exact CBOR map ordering and integer widths.  
  - Leaf codec: `namespace || length || data` (byte-precise). *(See `nmt.cddl`.)*
- **Erasure layout:** RS encoding operates over fixed-size **shards**; padding rules are explicit and stable across implementations. *(See `ERASURE.md`.)*
- **Proof soundness:** A proof is valid iff every branch hash recomputes to the root **and** namespace interval constraints hold for all internal nodes. *(See `NMT.md` and `availability_proof.cddl`.)*

---

## Parameters (network-configurable)

Declared in `da/config.py` and network params:

- **Namespaces:** reserved ranges for system topics vs user blobs.
- **Erasure profiles:** tuples \((k,n)\), shard size in bytes, padding policy.
- **Sampling policy:** minimum samples per blob and per block; retry/backoff and timeouts.
- **Limits:** maximum blob size, per-block blob byte caps, API rate-limits.

---

## Client & API surfaces

- **Python client:** `da/retrieval/client.py`  
  Convenience helpers for post/get/proof round-trips.

- **FastAPI endpoints:** `da/retrieval/api.py`  
  - `POST /da/blob` — store a blob; returns commitment + receipt  
  - `GET /da/blob/{commitment}` — stream blob bytes  
  - `GET /da/proof?commitment=…&indices=…` — DAS proof for requested sample indices

- **Adapters:**  
  - `da/adapters/core_chain.py` — computes/validates DA root while building blocks  
  - `da/adapters/rpc_mount.py` — mounts DA endpoints into the main RPC app  
  - `da/adapters/p2p_*` — gossip commitments/samples on DA topics

---

## CLI snippets

- Put a blob and print its commitment:
  ```bash
  python -m da.cli.put_blob --ns 24 ./file.bin

	•	Get a blob by commitment:

python -m da.cli.get_blob --commit 0x<root> > out.bin


	•	Simulate sampling and report (p_\text{fail}):

python -m da.cli.sim_sample --commit 0x<root> --samples 640


	•	Inspect an NMT root & namespace ranges:

python -m da.cli.inspect_root --root 0x<root>



⸻

Interop & testing
	•	Test vectors: da/test_vectors/*.json
Canonical NMT leaves/build proofs; RS encode/decode; end-to-end DAS availability.
	•	Pytests: da/tests/… cover tree building, range proofs (negative cases), RS recovery, commitment determinism, sampling math, API round-trips, P2P gossip, and RPC mount.
	•	Benchmarks: da/bench/*.py for NMT build and RS throughput.

⸻

Rationale & choices
	•	NMT vs plain Merkle: Namespaces allow selective sub-tree proofs for application-specific ranges (e.g., contract code vs logs), reducing verification and bandwidth.
	•	Erasure coding: RS increases robustness against partial withholding while keeping verification simple (deterministic layout and shard sizes).
	•	Deterministic CBOR: Stable hashing/signing across languages/runtimes, enabling cross-implementation clients and light verifiers.

⸻

Reading order (suggested)
	1.	NMT.md (tree model & proofs)
	2.	ERASURE.md (encoding & layout)
	3.	DAS.md (sampling & probabilities)
	4.	Schemas in da/schemas/ for exact on-wire formats
	5.	Tests in da/tests/ to see edge cases and expected failures

⸻

Status

This spec targets the Gate for DA: post/retrieve blobs; compute DA root; verify availability with DAS; expose light-client proofs; and integrate with headers, P2P gossip, and RPC.

