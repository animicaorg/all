# Data Availability (DA)

This module provides **blob posting, commitment, erasure encoding, namespaced proofs**, and a **sampling verifier** that lets full nodes and light clients reason about the availability of block data with high confidence.

Animica stores arbitrary user/data-plane payloads as **blobs**. Each blob is:
1) **Erasure-encoded** with Reed–Solomon to tolerate withholding;
2) **Namespaced** and laid out as fixed-size shares;
3) **Committed** via a **Namespaced Merkle Tree (NMT)** root;
4) Referenced from the block by a compact **blob descriptor** that includes `(namespace, size, commitment)`.

The block header carries a **DA root** that commits to all blob descriptors included in the block. Light clients can request random samples with **NMT inclusion/range proofs** to verify that data was widely distributed when the block was accepted.

---

## Architecture

        +----------------------------+

Tx/Contract |  POST blob (ns, bytes)     |
or Services +–––––––+———––+
|
v
[erasure/encoder.py]
|  RS(k,n) shards
v
[nmt/codec.py] → shares (ns||len||data)
|
v
[nmt/tree.py] ──► NMT root (commitment)
|
v
[blob/store.py]  (content-addressed, GC-safe)
|
v
BlobDescriptor(ns, size, commitment)
|
v
Block builder packs descriptors into the block;
header.da_root = MerkleRoot( descriptors[] in canonical order )
|
v
Full nodes serve GET /da/blob, /da/proof via FastAPI
|
v
Light clients sample shares + verify NMT proofs
against header.da_root → availability confidence

Key packages (see `da/`):
- `erasure/`: RS parameters, partitioner, encoder/decoder, layout math.
- `nmt/`: namespace types, NMT nodes, proofs, indices, verification.
- `blob/`: chunker, commitment, index, store, GC/retention, receipts.
- `sampling/`: DAS sampler, probability math, light-client verification.
- `retrieval/`: FastAPI service to post/get/prove, with rate-limits & cache.
- `adapters/`: wiring to `core/` and RPC/P2P (gossip & mounting).
- `protocol/`: DA wire structs & codecs for P2P dissemination.

Schemas live in `da/schemas/*` and match CDDL/JSON-Schema used on the wire.

---

## Trust & Threat Model

**Goals**
- Make it **expensive to include undeliverable data** in a block.
- Allow **light clients** to verify availability with probabilistic guarantees.
- Bound per-blob influence via **namespaces** and **erasure parameters**.

**Assumptions**
- At least one honest party with bandwidth serves shares for recently accepted blocks.
- Hash functions (SHA3-256/512) are collision-resistant; NMT rules provide proper **namespace isolation** (no cross-namespace ambiguity).
- Erasure coding parameters `(k, n)` are chosen so that with the configured sample counts, the probability of accepting an unavailable blob is below target `p_fail`.

**Adversary**
- A colluding set of block producers and storage peers may **withhold** some shares.
- An attacker may try to **forge proofs**. Proof verification binds to the header’s `da_root` and per-blob **commitment**, so forgery reduces to breaking the hash/NMT.

**Mitigations**
- **Erasure Coding**: Each blob is expanded to `n` shards; any `k` suffice to recover. Withholding needs ≥ `n - k + 1` shards to be hidden.
- **Sampling (DAS)**: Clients request random shares. The number of samples per blob (or per block window) is sized by `da/erasure/availability_math.py` to keep `p_fail` ≤ target (e.g. 2^-40).
- **Namespace proofs**: NMT **range proofs** ensure a server cannot mix content across namespaces.
- **Header binding**: Blocks embed a **Merkle root over blob descriptors**; each descriptor includes the per-blob **NMT root**, so all sampling proofs must reconcile with that header commitment.
- **DoS controls**: Retrieval API applies auth/rate limits; P2P has dedupe/backoff; store writes are **GC-safe** and content-addressed.

---

## How DA ties into headers & blocks

### Blob Descriptor
For each posted blob the block carries:

BlobDescriptor = {
ns: NamespaceId,
size: u64,                       # original unencoded size (bytes)
commitment: NmtRoot (32 bytes),  # NMT(root of namespaced shares)
}

### Header field
The block header contains:
- `da_root`: a **Merkle root** over the ordered list of `BlobDescriptor` canonical encodings (sorted by `(ns, commitment, size)` for determinism). This ensures:
  - Header → (da_root) → descriptors[] → per-blob (ns, size, commitment) → NMT leaves.
  - Light clients only need `header.da_root` plus a Merkle branch to a descriptor to anchor subsequent NMT proofs.

> The precise CDDL for descriptors and `da_root` is in `spec/blob_format.cddl` and `da/schemas/*.cddl`. The canonical map/array ordering follows Animica’s deterministic CBOR rules.

### Validation at block import
`da/adapters/core_chain.py`:
1. Recomputes each blob’s `commitment` if blobs are local or revalidates proofs if only descriptors are present.
2. Rebuilds `da_root` from descriptors; must match the header.
3. Optionally runs **light sampling** (configurable) during block building or gossip precheck.

---

## Namespaced Merkle Trees (NMT)

- **Leaves**: `leaf = ns || length || share_bytes` where `ns` is fixed-width `NamespaceId`.
- **Internal nodes** track a namespace **range** `[min_ns, max_ns]` for their subtree.
- **Inclusion proof** shows that a specific leaf (share) with namespace `ns` is in the tree rooted at `commitment`.
- **Namespace-range proof** shows that a range for `ns` is present and unambiguous (prevents cross-namespace smuggling).

All rules are implemented in `da/nmt/*` and specified by `da/schemas/nmt.cddl`.

---

## Erasure Coding

- **Parameters**: Chosen profiles in `erasure/params.py` specify `(k, n)`, shard size, and padding rules. By default we target byte-aligned shares that pack well in networking frames.
- **Encoding**: `erasure/encoder.py` splits the blob into fixed-size chunks, RS-encodes to `n` shards, and maps shards to **namespaced leaves**.
- **Decoding**: `erasure/decoder.py` reconstructs with any `k` valid shares and verifies NMT proofs.

Availability math lives in `erasure/availability_math.py` and helps pick sample counts per blob or per-window to achieve a target failure probability.

---

## Retrieval API (FastAPI)

Mounted by `da/retrieval/api.py` (also available as an adapter in the main RPC process):

- `POST /da/blob`
  - Body: binary or envelope (namespace, bytes, optional mime)
  - Returns: `{ commitment, size, ns }` and a **receipt** (binds chainId & policy roots)
- `GET /da/blob/{commitment}`
  - Returns raw bytes (range-GET supported) or an envelope with metadata.
- `GET /da/proof?commitment=0x…&samples=…`
  - Returns an **AvailabilityProof** (see `da/schemas/availability_proof.cddl`) containing sampled indices, share bytes (optional), and NMT branches anchored to the **blob commitment** with a Merkle path back to `header.da_root`.

Security:
- Optional API tokens & CORS (see `retrieval/auth.py` and `retrieval/rate_limit.py`).
- LRU caches in `retrieval/cache.py` for hot proofs/shards.

---

## P2P & Light Clients

- **Gossip**: `da/adapters/p2p_gossip.py` publishes new **commitments** and serves **sample responses** on DA-specific topics (see `da/adapters/p2p_topics.py` and `da/protocol/*`).
- **Light verification**: `da/sampling/light_client.py`
  1) Fetches the `BlobDescriptor` Merkle branch to anchor in `header.da_root`;
  2) Randomly samples indices within the erasure layout;
  3) Verifies NMT inclusion/range branches for each sample;
  4) Applies probability math to decide if availability is above threshold.

---

## Receipts & Accounting

`blob/receipt.py` returns a **post receipt** that includes:
- `commitment`, `size`, `ns`;
- A signature or policy binding (chainId / alg-policy root) so contracts or off-chain tools can later **attest** which network parameters were in force when the blob was pinned.

---

## CLI Quickstart

All CLIs are importable as `python -m` modules.

```bash
# Put a blob; prints commitment, size, ns
python -m da.cli.put_blob --ns 24 path/to/file.bin

# Get a blob by its commitment
python -m da.cli.get_blob --commit 0x<commitment> > out.bin

# Simulate data availability sampling for a blob
python -m da.cli.sim_sample --commit 0x<commitment> --p-fail 2^-40 --min-samples 60

# Inspect an NMT root or decode a descriptor
python -m da.cli.inspect_root --root 0x<commitment>


⸻

Integration with Core / RPC / Execution
	•	Block building: da/adapters/core_chain.py assists miners when packing blobs into candidate blocks. The miner commits blobs locally, includes descriptors in the block, and recomputes da_root.
	•	RPC: da/adapters/rpc_mount.py mounts the Retrieval API under the main FastAPI app so external tools (SDKs, wallet, studio) can post/get data without bespoke endpoints.
	•	Contracts: The capabilities and execution modules include syscalls that allow contracts to pin blobs (by delegating to DA) and later reference their commitment. On-chain state keeps only compact pointers (commitments), not the data.

⸻

Canonical encoding & determinism
	•	All public structs (blob envelopes, descriptors, proofs) are CBOR per da/schemas/*.cddl and Animica’s canonical CBOR rules (deterministic map ordering & integer encodings).
	•	Hash & Merkle operations use explicit domain tags (see da/utils/hash.py) to enforce strong domain separation.

⸻

Testing & Vectors
	•	Unit tests cover NMT build/verify, RS recovery, commitment determinism, sampling bounds, API round-trips, and P2P dedupe. See da/tests/*.
	•	Test vectors in da/test_vectors/*.json include leaves/build/proofs and availability simulations; they are cross-checked against spec/test_vectors/*.

⸻

Operational notes
	•	Parameters: Network-specific profiles (namespaces, (k,n), shard size, max blob size) are configured in da/config.py and referenced from spec/params.yaml.
	•	Retention: blob/gc.py applies pin/unpin + refcount policies; producers should pin until a safe confirmation depth and unpin stale artifacts to bound storage.
	•	Metrics: da/metrics.py exports Prometheus counters: post/get/proof rates, sampler stats, and bytes/sec.

⸻

Rationale & pointers
	•	Namespaced commitment enables independent inclusion proofs per namespace and efficient range queries.
	•	Merklized descriptor set in the header keeps the header compact while letting light clients verify a blob’s presence without fetching the whole block.
	•	Sampling gives probabilistic assurances without everyone downloading all data.

For deeper details, see:
	•	da/specs/*.md (NMT, ERASURE, DAS)
	•	spec/blob_format.cddl (wire-level)
	•	da/schemas/* (exact CBOR/JSON layouts)

