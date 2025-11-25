# Light Client — Header Sync, DA Checks, Beacon Verification

This document specifies the **minimum verification logic** a light client must implement to follow the Animica chain without storing full blocks or blobs. It defines data structures, sync flows, and security assumptions for:

- **Header synchronization** and fork-choice (PoIES-weighted via Θ/Γ commitments).
- **Data Availability (DA) light checks** using Namespaced Merkle Trees (NMT) and sampling proofs.
- **Randomness beacon** verification (commit→reveal→VDF) from finalized headers.

> See also:
> - [BLOCK_FORMAT.md](./BLOCK_FORMAT.md)
> - [MERKLE_NMT.md](./MERKLE_NMT.md)
> - [DA_ERASURE.md](./DA_ERASURE.md)
> - [FORK_CHOICE.md](./FORK_CHOICE.md)
> - [DIFFICULTY_RETARGET.md](./DIFFICULTY_RETARGET.md)
> - `randomness/specs/*` for VDF and beacon details.

---

## 1. Threat Model & Goals

A light client aims to verify that:

1. **Headers are valid** under consensus rules (hash links, roots, Θ updates, policy pins) with minimal state.
2. **Blocks are *available*** with high probability using **DAS proofs** (probabilistic, parameterized failure bound).
3. **Beacon outputs** included in headers are valid (commit–reveal aggregated + VDF proof verified).

Adversary controls network scheduling and may try to:
- Withhold blob data while serving seemingly valid headers.
- Serve an alternative fork with less aggregate work (PoIES weight).
- Forge beacon outputs (requires breaking VDF or commit–reveal binding).

The light client **does not** execute transactions or validate full proof bodies; it relies on header-level commitments and light proofs.

---

## 2. Minimal State

Light clients maintain:

- `Head`: best-known header pointer (hash, height, Θ, cumulative-work proxy).
- `ChainParams`: stable constants (chainId, PoIES policy roots, gas/limits) — see [CHAIN_PARAMS.md](./CHAIN_PARAMS.md).
- `VK Pins`: optional set of pinned verifier keys for ZK/DA light proof checks (hashes only).
- `DA Sampling Window`: rolling summary to track sampling coverage across recent blocks (optional).

Persisted fields per header:
- `hash`, `parentHash`, `height`, `stateRoot`, `txsRoot`, `receiptsRoot`, `proofsRoot`, `daRoot`, `beaconRoot` (if present),
- `theta` (Θ), `mixSeed`, `policyRoots` (PoIES/alg-policy),
- `workScore` (monotone accumulator; see §3.5).

---

## 3. Header Synchronization

### 3.1 Header Object (view)
A light client decodes headers per [BLOCK_FORMAT.md](./BLOCK_FORMAT.md). Only fields relevant to light checks must be retained.

### 3.2 Basic Validation
For a received header `H` with parent `P` already validated:
1. **Linking**: `H.parentHash == hash(P)`.
2. **Height**: `H.height == P.height + 1`.
3. **Roots well-formed**: `txsRoot/receiptsRoot/proofsRoot/daRoot` length & domain tags per [ENCODING.md](./ENCODING.md).
4. **Policy pins**: `H.policyRoots` must match the currently active policy roots (or expected upgrade slots).
5. **Θ update**: recompute `Θ(H)` using [DIFFICULTY_RETARGET.md](./DIFFICULTY_RETARGET.md) and compare.
6. **Nonce/mix domain**: nonce binding & mixSeed domain separation checks (header-format domain tags).

> These checks are *stateless* besides `P` and chain params.

### 3.3 Light PoIES Weight
Headers carry enough information to compute a **work-like score**:

share_target = f(Θ)                  // micro-threshold representation
work(H)      = g(SumPsi, Θ)         // monotone mapping, see FORK_CHOICE
cumWork(H)   = cumWork(P) + work(H)

The light client need not recompute Σψ; it uses the advertised `Θ` and canonical formula (or an equivalent accumulator committed in the header). The exact function is defined in [FORK_CHOICE.md](./FORK_CHOICE.md).

### 3.4 Fork Choice
Pick the chain with **highest cumulative work**; ties break deterministically by header hash (lexicographic).

### 3.5 Locators & Sync
For efficient catch-up:
- Request **header locators** from peers (sparse set of ancestor hashes).
- Use `getheaders/headers` exchange per `p2p/sync/headers.py` spec.
- Apply per-peer token buckets and provenance checks (see `p2p/specs/SYNC.md`).

---

## 4. Data Availability (DA) Light Verification

### 4.1 DA Root
Each header commits to **DA root**: an NMT root over erasure-extended, namespaced shares (see [DA_ERASURE.md](./DA_ERASURE.md)).

### 4.2 Sampling Strategy
A light client can perform **on-demand sampling**:
- Choose `q` random leaf indices (uniform or stratified per row/col layout).
- Request **availability proof** for each sample from DA providers:
  - Inclusion branch in NMT,
  - Namespace range proof if required,
  - Erasure parameters `(k,n)`, share coordinates.

The client verifies:
1. Leaf serialization (namespace || len || data) matches [MERKLE_NMT.md](./MERKLE_NMT.md).
2. NMT inclusion/range proofs verify to `daRoot`.
3. Optionally, **multi-sampling** across providers to reduce equivocation risk.

### 4.3 Failure Probability
For blob matrix parameters `(k,n)` and a sampling plan of `q` independent samples, the **undetected withholding** probability `p_fail` is bounded per [DA_ERASURE.md](./DA_ERASURE.md). Clients target a policy-defined `p_fail` (e.g., `1e-12`) and adapt `q` according to:

q >= ceil( ln(p_fail) / ln(1 - f) )

where `f` is the fraction of corrupted/unavailable shares required to break recovery.

### 4.4 Liveness vs. Finality
- A client MAY mark a header **provisionally available** once a minimum `q_min` samples verify.
- For **display/finality UI**, require `q_strict >= q_min`, across multiple peers, before considering the block “available”.

---

## 5. Randomness Beacon Verification (Light)

Headers commit to a **beacon root** built from:
1. Aggregated **commit–reveal** (hash-then-xor or equivalent combiner),
2. **VDF input** derived from the aggregate and previous beacon,
3. **VDF proof** (Wesolowski) verified against the input/params.

The light client:
- Reconstructs the VDF input from header’s previous beacon and commitment aggregate (fields are included/hashed per [BEACON.md] in `randomness/specs`).
- Verifies **VDF proof** using public parameters (modulus, iterations).
- Checks the resulting beacon output matches the header’s committed `beaconRoot`.

> The commit/reveal window bookkeeping is non-consensus for light clients; only the VDF verification and hash bindings are required.

---

## 6. Interfaces (RPC / P2P)

### 6.1 JSON-RPC (minimal)
- `chain.getHead() -> Head`: returns the current canonical head header view.
- `chain.getBlockByNumber(number, {headersOnly: true}) -> Header`
- `da.getProof(commitment, samples[]) -> AvailabilityProof` (optional gateway)
- `rand.getBeacon(number|hash) -> BeaconOut` (compact light object)

### 6.2 P2P
- `HELLO`: advertise chain id, alg-policy root, best height/work.
- `INV/GETDATA (headers)`: header sync.
- `INV/GET (DA samples)`: DA sampling protocol (see `da/protocol/*`).

Apply rate limits and per-topic validators before accepting frames (see `p2p/gossip/validator.py`).

---

## 7. Data Structures (Light)

```text
LightHeader {
  parentHash: Hash32
  number: u64
  stateRoot: Hash32
  txsRoot: Hash32
  receiptsRoot: Hash32
  proofsRoot: Hash32
  daRoot: Hash32
  beaconRoot: Hash32?      // optional until beacon enabled
  theta: u64               // difficulty/threshold encoding
  policyRoots: { poies: Hash32, algPolicy: Hash32 }
  mixSeed: Hash32
  // optional:
  workAccum: u128          // monotone accumulator (if present)
}

AvailabilityProof {
  samples: List<SampleProof>
  params: { k: u16, n: u16, shardSize: u32 }
}
SampleProof {
  index: u32
  namespace: Bytes
  leaf: Bytes
  branch: List<Hash32>     // NMT branch with namespace ranges
}

BeaconLight {
  input: Hash32
  output: Hash32
  vdfProof: Bytes
  params: { modulus: Bytes, iterations: u64 } // or profile id
}

All hashes are domain-separated per ENCODING.md.

⸻

8. Algorithms (Pseudo-code)

8.1 Header Accept

function acceptHeader(H, P, params):
  require H.parentHash == hash(P)
  require H.number == P.number + 1
  require wellFormedRoots(H)
  require H.policyRoots == activePolicyRoots(H.number, params)
  theta' = retarget(P.theta, window(P), params)
  require H.theta == theta'
  return true

8.2 Fork Choice

function score(H):
  return H.workAccum ? H.workAccum : impliedWork(H.theta)

function bestChain(candidateHeads):
  return argmax(score(H)) breaking ties by lex(hash(H))

8.3 DA Light Check

function checkDA(daRoot, plan, provider):
  proofs = provider.getDAProofs(plan.indices)
  for pr in proofs:
    require verifyNMT(pr.leaf, pr.branch, daRoot, pr.namespace)
  return true

8.4 Beacon Verify

function verifyBeacon(H, prevBeacon):
  input = deriveInput(prevBeacon.output, H.beaconAggregate)
  (ok, out) = vdfVerify(input, H.vdfProof, H.vdfParams)
  require ok and hash(out) == H.beaconRoot
  return true


⸻

9. Finality & UI Guidance
	•	Display as “synced”: header accepted + minimal DA sampling succeeded for the last k_ui blocks.
	•	Display as “finalized-ish”: chain tip has ≥ d_conf successors on the same fork and DA sampling passed for those blocks (configurable).
	•	Beacon ready: show randomness once header is accepted and VDF verifies.

⸻

10. Parameters & Tuning

Parameter	Purpose	Typical
d_conf	Confirmation depth for UI	8–24
q_min	Min DA samples per block	20–60
p_fail_target	Target probability of undetected DA withholding	1e-12
providers_min	Distinct DA providers per block	2–3
timeout_sample	Per-sample timeout	1–3 s
peer_headers_max	Max headers per response	2k–4k


⸻

11. Error Mapping

Light client implementations should surface canonical errors (see ERRORS.md):
	•	consensus/HeaderInvalid (5004) — any header linkage/Θ mismatch.
	•	da/InvalidProof (7002) — NMT proof failure.
	•	da/NotFound (7001) — provider can’t serve a requested sample.
	•	rand/VDFInvalid (9204) — beacon VDF verify failed.

⸻

12. Test Vectors & Compliance
	•	Headers: spec/test_vectors/headers.json — genesis + Θ/retarget schedules.
	•	DA: da/test_vectors/availability.json, da/test_vectors/nmt.json.
	•	Beacon: randomness/test_vectors/{vdf,beacon}.json.

A conforming light client MUST:
	1.	Reproduce accept/reject outcomes for headers.
	2.	Verify DA proofs for provided sample plans.
	3.	Verify VDF proofs and match beacon outputs.

⸻

13. Extensibility
	•	KZG-in-Header: future optional commitments for specific circuits; light clients may pin VK hashes.
	•	Batch DA sampling: multi-block aggregated queries to reduce RTT.
	•	Alt forks: policy-root upgrades at scheduled heights; clients must validate pins.

⸻

14. Security Notes
	•	Always domain-separate hash inputs.
	•	Avoid single-provider trust for DA; diversify providers.
	•	Randomness beacon relies on correct VDF parameters; pin the profile id or parameter hash in chain params.
	•	Apply strict rate limits and request caps to mitigate resource DoS.

⸻

15. References
	•	Kate et al., Constant-Size Commitments to Polynomials and Their Applications (KZG)
	•	Wesolowski, Efficient Verifiable Delay Functions
	•	Celestia/Erasure-coding literature for DAS sampling heuristics

