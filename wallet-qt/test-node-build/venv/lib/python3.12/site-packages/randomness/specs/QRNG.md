# QRNG Mixing (Optional) — Rules & Attestation Expectations

**Status:** Informational / non-consensus  
**Module:** `randomness/qrng/*` and `randomness/beacon/*` (mixing path)  
**Version tag:** `animica:rand:qrng:v1`

This note specifies how **optional** quantum RNG (QRNG) bytes may be *mixed into* the beacon stream for consumers that opt in. The canonical, consensus beacon remains driven by **commit→reveal→VDF**. QRNG mixing **must never** reduce entropy nor introduce consensus divergence.

---

## 1) Goals & non-goals

### Goals
- Allow nodes to add entropy from one or more QRNG sources.
- Be bias-resistant: if **either** the consensus input (VDF output) *or* at least one QRNG source is unpredictable, the **mixed beacon** is unpredictable.
- Deterministic and auditable: the same input set yields the same mixed output.
- Attestation-aware: sources can include vendor/device attestations; nodes can enforce policies.

### Non-goals
- QRNG mixing is **not** consensus-critical. Blocks and fork-choice **do not** depend on QRNG inputs.
- Light-client validity does not require QRNG proofs. Clients may ignore QRNG and still track the consensus beacon.

---

## 2) Where mixing sits in the round

Round `r` lifecycle (consensus path):

aggregate_reveals(r)  -> A_r
derive_vdf_input(A_r) -> X_r
verify_vdf(X_r)       -> V_r           # 32 bytes (or domain-lengthed)

Optional QRNG mixing (local, policy-gated):

collect_qrng_sources(r, cutoff) -> S_r  # zero or more sources with attestations
mix(V_r, S_r)                    -> M_r # 32 bytes

Consumers that **enable** QRNG read `M_r` (mixed beacon). Others read `V_r` (consensus beacon). Both are published; see §8.

---

## 3) Inputs & canonicalization

Each QRNG source `i` supplies:
- `provider_id`: stable identifier (bytes; e.g., DER-encoded pubkey hash or bech32m-encoded ID).
- `payload`: raw QRNG bytes (bounded length).
- `attestation`: optional opaque bytes (cert chain, device report, timestamp, signature).
- `received_at`: local receipt time (for cutoff enforcement).

**Inclusion policy** (local, deterministic):
- Only include sources that arrive **at or before** `qrng_mix_cutoff(r)` (configurable; typically `reveal_close(r) + Δ`).
- Enforce **size** limits: `0 < len(payload) ≤ MAX_BYTES_PER_SOURCE`.
- Deduplicate by `(provider_id, round=r)` – keep the **first** valid arrival.
- Cap by `MAX_SOURCES_PER_ROUND` after **stable ordering**: sort by `provider_id` (bytewise asc), then by `H(attestation)`.

**Attestation policy** (local):
- If `QRNG_ATTEST_REQUIRED=true`, a source **MUST** pass `verify_attestation(attestation, provider_id, r)`.
- Otherwise (dev/test), attestation may be ignored; still recorded in the transcript.

**Cutoff determinism.** The cutoff function is purely round-driven (not wall-clock). See `randomness/beacon/schedule.py`.

---

## 4) Transcript & domain separation

We bind all inputs to an explicit domain:

DOMAIN_EXTRACT = “animica:rand:qrng:extract:v1”
DOMAIN_LEAF    = “animica:rand:qrng:leaf:v1”
DOMAIN_MERKLE  = “animica:rand:qrng:merkle:v1”
DOMAIN_MIX     = “animica:rand:qrng:mix:v1”

Hashes use SHA3 (256/512) with these domains **prepended**.

Per-source digests:

attn_hash_i = SHA3-256(DOMAIN_LEAF || r || provider_id || attestation)
payl_hash_i = SHA3-256(DOMAIN_LEAF || r || provider_id || payload)

Extraction (to smooth non-uniform QRNG bytes into a uniform chunk):

q_i_64 = SHA3-512(DOMAIN_EXTRACT || r || provider_id || payload || attn_hash_i)
q_i    = first_32_bytes(q_i_64)

Leaf hash (for audit Merkle):

leaf_i = SHA3-256(DOMAIN_LEAF || r || provider_id || payl_hash_i || attn_hash_i || q_i)

Canonical Merkle root over ordered `leaf_i`:

qrng_root = MerkleRoot(leaves=leaf_i…, domain=DOMAIN_MERKLE)

---

## 5) Aggregation & final mixing

Aggregate the extracted chunks with XOR (order independent):

Q = 0^32
for each q_i in ordered S_r:
Q = Q XOR q_i

Produce the **mixed** beacon:

M_r = SHA3-256(DOMAIN_MIX || r || V_r || Q || qrng_root)

If `S_r` is empty, define `Q = 0^32` and `qrng_root = SHA3-256(DOMAIN_MERKLE || "empty")`. Then `M_r` deterministically reduces to `H(DOMAIN_MIX || r || V_r || 0^32 || qrng_root_empty)`.

**Security intuition.** If `V_r` is unpredictable (honest majority in commit→reveal, VDF sound), `M_r` is unpredictable. If `V_r` is adversarially predicted but **at least one** QRNG source is honest and secret until after cutoff, `M_r` is still unpredictable. Extraction ensures robustness against biased/non-uniform QRNG bytes.

---

## 6) Attestation expectations

The `attestation` blob is vendor-specific. Nodes apply policy via `randomness/qrng/attest.py`. A minimal accepted structure:

- **Provider identity:** public key or certificate chain (X.509 or COSE), bound to `provider_id`.
- **Device statement:** model / serial / firmware measurement (TPM quote, TEE report, or vendor signature).
- **Freshness:** timestamp or nonce signed within an acceptance window around round `r`.
- **Scope binding:** statement includes `round=r` and `payl_hash_i`, ensuring payload-origin binding.

Verification requirements (when enabled):

1. Validate signature chain under configured trust anchors.
2. Check timestamp freshness and revocation (if available).
3. Confirm `provider_id` matches the attested key/identity.
4. Confirm the statement binds `payl_hash_i` for **this** round `r`.

**Failures**: if any step fails, discard the source (do not partially mix it).

**Dev/Test**: attestation may be omitted; `attn_hash_i` still binds whatever is present (possibly empty).

---

## 7) Limits & parameters (from `randomness/config.py`)

- `QRNG_ENABLE` (bool; default `false` mainnet, `true` devnet)
- `QRNG_MIX_CUTOFF_DELTA_BLOCKS` (blocks after `reveal_close(r)`)
- `QRNG_MAX_SOURCES_PER_ROUND` (e.g., 16)
- `QRNG_MAX_BYTES_PER_SOURCE` (e.g., 4096)
- `QRNG_ATTEST_REQUIRED` (bool)
- `QRNG_TRUST_ANCHORS` (set of roots / pinned keys)
- `QRNG_RECORD_RETENTION` (number of rounds to retain source transcripts)

Nodes **must** enforce the same local parameters to achieve the same `M_r`. Networks can publish a recommended profile.

---

## 8) Outputs & surfacing

For each round `r`, nodes **publish**:

- `V_r`: consensus beacon (commit→reveal→VDF).  
- `M_r`: mixed beacon (if `QRNG_ENABLE=true`), else equal to a deterministic function that reduces to `V_r` + domain tag as in §5.  
- `qrng_root`: Merkle root of included sources (or `empty` root).  
- `num_sources`: count in `S_r`.

**Light-client proof.** The standard light proof (hash chain + VDF proof) is unchanged and verifies `V_r`. Clients that care about QRNG mixing can additionally fetch `qrng_root` and the Merkle leaves to audit `M_r`.

---

## 9) Edge cases

- **No sources:** `Q=0^32`, `qrng_root=empty`, `M_r` still defined.
- **Duplicate provider:** only the **first** valid source per `(provider_id, r)` is used.
- **Oversize payload:** reject the source.
- **Late arrival (after cutoff):** ignore.
- **Attestation stale/invalid:** ignore source.
- **Conflicting payloads from same provider:** dedup rule chooses the earliest valid.

---

## 10) Security analysis (brief)

- **Bias resistance:** XOR + extraction prevents an adversary from biasing the output unless **all** inputs are adversarially known. Domain-separation prevents cross-protocol replay.
- **DoS surface:** Caps on sources/bytes and a cutoff limit resource use. Verification is linear in `|S_r|`.
- **Auditability:** `qrng_root` commits to each included source; leaves reveal per-source provenance and extraction chunks.
- **Consensus safety:** Since `V_r` remains the authoritative beacon for consensus, QRNG failure or divergence cannot cause reorgs.

---

## 11) Pseudocode (reference)

```python
def mix_qrng(round_id: bytes, V_r: bytes, sources: list[Source]) -> tuple[bytes, bytes]:
    # sources already filtered, attested, ordered
    leaves = []
    Q = b"\x00" * 32
    for s in sources:
        attn_hash = sha3_256(D_LEAF + round_id + s.provider_id + s.attestation)
        payl_hash = sha3_256(D_LEAF + round_id + s.provider_id + s.payload)
        q_i = sha3_512(D_EXTRACT + round_id + s.provider_id + s.payload + attn_hash)[:32]
        Q = xor32(Q, q_i)
        leaf = sha3_256(D_LEAF + round_id + s.provider_id + payl_hash + attn_hash + q_i)
        leaves.append(leaf)
    qrng_root = merkle_root(leaves, domain=D_MERKLE)
    M_r = sha3_256(D_MIX + round_id + V_r + Q + qrng_root)
    return M_r, qrng_root


⸻

12) Compatibility & rollout
	•	Devnet/Testnet: may enable QRNG by default with relaxed attestation.
	•	Mainnet: recommended to start with QRNG_ENABLE=false and flip via governance once common providers and policies are well-tested.
	•	Backwards compatibility: Consumers can always fall back to V_r. The mixed output includes the root to enable future audits.

⸻

13) Test vectors

See randomness/qrng/mixer.py and randomness/fixtures (if provided). Vectors should include:
	•	S_r = ∅ (empty set)
	•	Single-source with/without attestation
	•	Multi-source, reordering invariance
	•	Oversize payload rejection
	•	Late-arrival pruning at cutoff boundary

⸻

14) Rationale (selected)
	•	Extraction before XOR. XOR alone preserves any bias. SHA3-512 as an extractor compresses arbitrary-length inputs to a uniform 32-byte chunk, then XOR composes multiple independent sources in a commutative fashion.
	•	Merkle commitment. Enables compact proofs-of-inclusion of sources post hoc without bloating headers or touching consensus.
	•	Ordering & dedupe rules. Make local node behavior identical across implementations given the same arrival stream up to cutoff.

