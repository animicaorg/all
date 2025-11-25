# STORAGE_V0 Proofs — Proof-of-Replication onboarding & PoSt heartbeats

**Status:** Draft, production-intent  
**Scope:** Verifier-facing specification for *Storage* useful-work proofs.  
**Variants:**  
- **PoRep (onboarding):** one-time commitment that a provider replicated specific content.  
- **PoSt heartbeat (liveness):** periodic proof that replicas remain online/available.

**Consumes:** CBOR envelope per `proofs/schemas/proof_envelope.cddl`, body per `proofs/schemas/storage.cddl`.  
**Produces:** `ProofMetrics.Storage` used by PoIES scorer and compact **receipt** (for `proofsRoot`).

> PoIES acceptance remains \( S = -\ln u + \sum \psi \ge \Theta \).  
> This document defines how a `STORAGE_V0` proof contributes via non-negative terms \( \psi_{\text{S}} \) for **replicated bytes**, **redundancy**, and **availability QoS**, subject to caps/diversity rules in `consensus/caps.py`.

---

## 1) Goals & model

**Goals**
- Let storage providers earn by *pinning* user blobs (committed via DA NMT roots) and proving continued availability.
- Provide chain-verifiable receipts at modest cost (no heavy SNARKs; light crypto + sampling).

**Threat model**
- Provider may claim to store data but discard it (“lazy”), return corrupted bytes, or be offline.
- Periodic *audit challenges* (namespace/position queries) plus optional retrieval tickets discourage lying.
- PoRep binds identity + content; PoSt ties to current head/time and sample queries.

---

## 2) Blob commitments & DA linkage

Content addressed by **Namespaced Merkle Tree (NMT)** root from DA module (`da/nmt/commit.py`).  
For a blob:

- `commitment` = `NmtRoot` (32-byte)  
- `namespace` = `uint` per `da/constants.py`  
- Size & erasure params recorded in DA store.  
- Storage proofs **must** reference an existing, accepted commitment (via DA adapters).

---

## 3) Body — CBOR layouts (informative)

Canonical encoding by `proofs/cbor.py`. Schema: `proofs/schemas/storage.cddl`.

### 3.1 PoRep (onboarding)

StoragePoRepV0 = {
“domain”:        “STORAGE_V0/POREP”,
“chainId”:       uint,
“height”:        uint,                 # anchor height
“commitment”:    bstr .size 32,        # DA NMT root
“namespace”:     uint,
“size_bytes”:    uint,
“provider”: {
“id”:          tstr,                 # provider identity (must match AICF registry if used)
“pubkey”:      bstr,                 # Ed25519/PQ hybrid allowed per policy
“nonce”:       bstr .size 16
},
“bind_sig”:      bstr,                 # signature over BIND_POREP (see §4)
“redundancy”:    uint,                 # declared replica count (>=1)
“expiry_epoch”?: uint                  # optional planned retention end
}

### 3.2 PoSt heartbeat (liveness)

StoragePoStV0 = {
“domain”:        “STORAGE_V0/POST”,
“chainId”:       uint,
“height”:        uint,                 # anchor height for challenge
“commitment”:    bstr .size 32,
“namespace”:     uint,

Challenge & responses

“challenge”: {
“seed”:        bstr .size 32,        # derived from randomness beacon (height-Δ) + commitment
“epoch”:       uint,                 # logical audit epoch (policy-defined window)
“samples”:     uint                  # number of sampled leaves/shards
},
“responses”: [
{
“leaf_index”: uint,
“leaf”:       bstr,                # (namespace||len||data) or a digest depending on policy
“nmt_branch”: [bstr, …],        # inclusion path
“erasure_ok”?: bool               # if using RS spot-checks
},
…
],

Provider binding & QoS

“provider”: {
“id”:          tstr,
“pubkey”:      bstr,
“nonce”:       bstr .size 16
},
“bind_sig”:      bstr,                 # signature over BIND_POST (see §4)

“qos”?: {
“respond_ms”:  uint,                 # response latency observed by auditor
“uptime_bps”?: uint                  # optional: bytes/s served during window (if measured)
},

Optional retrieval ticket proving fetchability through network edge

“retrieval”: {
“ticket_id”:   bstr .size 32,
“status”:      uint,                 # 0=unknown,1=ok,2=timeout,3=corrupt
“bytes”?:      uint
}?
}

**Envelope fields:**  
`type_id = 0x04` (Storage V0), `body = cbor(StoragePoRepV0 | StoragePoStV0)`,  
`nullifier = H("PROOF_NULLIFIER/STORAGE_V0" || body)[:32]`.

All hashes are **SHA3-256** unless noted.

---

## 4) Deterministic bindings (anti-replay)

### 4.1 PoRep bind

BIND_POREP = H(
“STORAGE_V0/POREP/BIND” ||
commitment || u64(size_bytes) || u32(namespace) ||
provider.nonce || u32(redundancy)
)
bind_sig = Sign_provider(BIND_POREP)

### 4.2 PoSt bind

SEED = H(“STORAGE_CHALLENGE” || beacon(height - Δ) || commitment)
BIND_POST = H(
“STORAGE_V0/POST/BIND” ||
commitment || u32(namespace) || u64(challenge.epoch) ||
SEED || provider.nonce
)
bind_sig = Sign_provider(BIND_POST)

Δ is the **challenge delay** (policy: typically 1–2 blocks) to prevent precomputation with future randomness.

---

## 5) Verification rules

### 5.1 Common
- Verify envelope domain & `type_id`.
- Validate `nullifier` matches body.
- Resolve **DA commitment** in the node’s DA store:
  - Check commitment exists and matches `(namespace, size_bytes)` (PoRep) or `(namespace)` (PoSt).
- Verify `provider.pubkey` format & signature over corresponding **BIND**.
- Optional: check provider `id` against allowlist/registry (AICF).

### 5.2 PoRep
- Succeeds if signatures and DA linkage validate.
- Emits metrics reflecting **bytes onboarded** and **declared redundancy**, subject to policy caps and **cooldown** (to avoid rapid churn gaming).

### 5.3 PoSt heartbeat
1. Recompute `SEED` from beacon & commitment.  
2. Reconstruct sample positions (deterministically from `SEED` & `challenge.samples`).  
3. For each response:
   - Verify **NMT inclusion** of `leaf` at `leaf_index` under `commitment` (`da/nmt/verify.py`).  
   - If `erasure_ok` is used, optionally cross-check shard parity consistency via RS spot-check (`da/erasure/decoder.py`).  
4. Compute **pass ratio** = proofs passing / `challenge.samples`.  
5. Apply minimum ratio threshold `r_min` (policy).  
6. If present, combine **retrieval ticket** status to boost/penalize availability score.

Failures raise `ProofError::{Schema,Binding,DACommitNotFound,InclusionFail,ThresholdFail}`.

---

## 6) Metrics emitted

Verifier (`proofs/storage.py`) produces:

ProofMetrics.Storage = {
size_bytes:      int,      # accounted bytes for this proof (may be capped)
redundancy:      float,    # effective redundancy (>=1), possibly clipped
pass_ratio:      float,    # 0..1 for PoSt; 1.0 for PoRep
qos_latency_ms?: int,      # from qos.respond_ms, if any
retrieval_ok?:   bool      # from retrieval.ticket.status == 1
}

- For **PoRep**, `pass_ratio = 1.0`.  
- For **PoSt**, `size_bytes` typically equals the full blob size, but *policy* may scale by `pass_ratio` or minimum sampling confidence (see §7).

---

## 7) Policy mapping → ψ_S

Consensus policy (`consensus/policy.py` + `consensus/caps.py`) turns metrics into score:

\[
\psi_{\text{S}} = \min\Big(
  w_{\text{S}} \cdot \underbrace{\text{bytes\_effective}}_{\text{size}\times g(\text{pass\_ratio}) \times h(\text{redundancy})}
  \cdot q_{\text{qos}} \cdot q_{\text{retrieval}},
  \ \text{cap}_{\text{S}} \Big)
\]

Where:

- \( g(r) = \begin{cases}
  0 & r < r_\text{min} \\
  r & \text{otherwise}
\end{cases} \) (or a smoother curve);  
- \( h(\text{redundancy}) = \min(\text{redundancy}, R_\text{cap}) \) (diminishing returns);  
- \( q_{\text{qos}} \in [w_\text{min},1] \) penalizes high latency;  
- \( q_{\text{retrieval}} \in \{ \beta, 1 \} \) with \(\beta < 1\) if last ticket failed.

**Sampling confidence:** Policy picks `samples` to achieve target audit failure probability \(p_\text{fail}\) given blob size/erasure params (see `da/sampling/probability.py`). ψ may be reduced if `samples` < recommended.

**Diversity:** Escort/fairness rules may limit total \(\Gamma\) from storage per-epoch.

---

## 8) Timing & windows

- **PoRep**: accepted once; future PoSts refer to the same commitment.  
- **PoSt epochs:** fixed duration (e.g., 10 minutes or N blocks). A proof is valid only for its `challenge.epoch`.  
- **Expiry:** optional `expiry_epoch` after which PoSts no longer contribute ψ unless renewed.

---

## 9) Privacy & bandwidth

- Responses include only *sampled leaf data* and NMT paths—not the full blob.  
- Providers should avoid leaking plain content in public proofs for private blobs; recommend namespacing and app-level encryption when needed.

---

## 10) Interop with DA & light clients

- Light verifiers can check availability with the **same samples** given a header’s `da_root` plus NMT branches, matching `da/sampling/light_client.py`.  
- On-chain receipts store: `(commitment, epoch, samples, pass_ratio)`.

---

## 11) Economics & AICF hooks (optional)

If the **AICF** module manages provider balances:

- PoRep may trigger an onboarding bonus subject to minimum retention.
- PoSt earns steady rewards per epoch; missed epochs reduce recent availability score.  
- SLA evaluators can trend `pass_ratio`, retrieval outcomes, and latency (see `aicf/sla/*`).

---

## 12) Test vectors & fixtures

- `da/test_vectors/availability.json` — sampling math & proofs.  
- `proofs/test_vectors/storage.json` — PoRep/PoSt success and failure cases.  
- `proofs/tests/test_storage.py` — end-to-end verifier for inclusion & thresholds.

---

## 13) Versioning

- `domain = "STORAGE_V0/(POREP|POST)"`, `type_id = 0x04`.  
- Future `STORAGE_V1` may add:
  - Verifiable *dedup-aware* accounting across identical commitments.
  - SNARK-wrapped inclusion paths for constant-size PoSts.
  - Contract-bound *retrieval receipts* with client signatures.

---

## 14) Parameter summary (per-network policy)

- `r_min` (min pass ratio), `samples` per size tier, target `p_fail`.  
- `Δ` (challenge delay), epoch duration.  
- Redundancy cap `R_cap`, ψ cap `cap_S`, weight `w_S`, QoS multipliers.  
- Allowlist of provider ids (optional).

---

### Notation

- `H(x)` = SHA3-256.  
- CBOR is canonical (sorted map keys).  
- Integers are unsigned unless stated; fixed-width encoders `u32`, `u64` shown for binding clarity (wire uses CBOR uint).

