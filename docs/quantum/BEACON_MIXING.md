# Quantum/BEACON_MIXING — Mixing QRNG with the VDF Beacon Safely

**Status:** Stable (v1)  
**Audience:** protocol engineers, beacon operators, light-client implementers  
**Related:** `randomness/specs/BEACON.md`, `randomness/specs/VDF.md`, `randomness/qrng/*`, `capabilities/adapters/randomness.py`

This note specifies how to **optionally** mix **QRNG** samples into the **VDF-backed beacon** without compromising determinism or liveness.

---

## 1) Goals & Threat Model

**Goals**
- Hedge the beacon against implementation bugs or unforeseen weaknesses in either source.
- Preserve determinism and light-client verifiability.
- Make mixing **non-blocking**: absence of QRNG must not stall the beacon.

**Adversary Model**
- Network adversary can DoS/delay QRNG delivery but cannot forge valid attestation chains.
- At least one entropy source remains unpredictable to the adversary at time of beacon finalization:
  - The **VDF** chain (assuming setup & modulus are sound) **or**
  - The **QRNG** device (assuming correct hardware & attestation).

> Mixing is **hedging**: if **either** source is good, the output remains good.

---

## 2) Timing & Transcript

Round lifecycle (see `randomness/README.md`):

1. **Commit–Reveal** aggregation closes → input `I_round` derived.
2. **VDF** proof `π_vdf` evaluated off-chain; **verification is consensus**.
3. (Optional) **QRNG** sample(s) `S_i` fetched within a **mixing window** `[t_vdf_start, t_mix_deadline]`:
   - Each sample is accompanied by an **attestation bundle** `Att_i`.
   - Samples are content-addressed via DA: `cid_i = H(S_i)`.
4. **Finalize**: construct `BeaconOut` including the transcript & mix result.

**Determinism**: The transcript (hashes of all inputs, identities, and choices) fully determines the beacon.

---

## 3) Interface Summary

- **Inputs**:
  - `I_round`: VDF input (derived from commit–reveal + previous beacon).
  - `V`: VDF output bytes (or a hash thereof).
  - `Q = { (cid_i, S_i, Att_i, provider_i) }` received before `t_mix_deadline`.
- **Outputs**:
  - `B`: final beacon bytes.
  - `Transcript` fields stored & hashed in state (for light proofs).

---

## 4) Mixing Function

We adopt a **hedged extractor** that is safe even if the QRNG is biased or correlated. Using domain-separated SHA3-256:

R = concat(
DS(“mix/v1”),
u64_be(round_id),
DS(provider_list_hash),
H(S_1) || … || H(S_m),     # identities of samples
V                           # VDF output (or H(VDF_output))
)

B = SHA3-256(R)                   # final beacon bytes (32)

Where:
- `DS(tag)` is a fixed, length-prefixed domain-separation string.
- `provider_list_hash = SHA3-256(concat(sorted(provider_ids)))`.
- `H` is `SHA3-256`.
- If no QRNG samples pass validation (`m = 0`), then

B = SHA3-256( DS(“mix/v1”) || u64_be(round_id) || DS(“no-qrng”) || V )

**Why not XOR directly?**  
XOR is fine when sources are **independent and uniform**; the hedged hash above remains safe when **one source is biased** and keeps the output length fixed. It also simplifies proofs for light clients.

---

## 5) Validation & Selection Rules

Before a sample contributes to the transcript:

1. **Attestation verify**
 - Validate `Att_i` (device cert chain, freshness, policy flags).  
 - Bind `S_i` to the device identity and time window.
2. **Size & format**: bounded length (e.g., 32–4096 bytes), explicit version.
3. **DA anchoring**: either (a) the block includes a reference to `cid_i` via DA, or (b) the light proof exposes `H(S_i)` with policy-approved inclusion.
4. **Deduplication**: keep at most **one** sample per provider per round (first-valid).
5. **Weighting**: for v1, we **do not weight**; all valid samples only contribute via their **hashes** (the bytes of `S_i` are not concatenated directly).

**Result**: the set `Q*` of **accepted** samples (possibly empty). The mixing function uses only `H(S_i)` and provider identities, not the raw bytes, for compactness and to discourage fishing attacks.

---

## 6) Consensus & Light-Client Verification

### 6.1 Full Node (consensus)
- Recompute `V` from `I_round` and verify `π_vdf`.
- Validate `Q*` under policy.
- Compute `B` as per §4.
- Persist `BeaconOut` + `TranscriptHash` in the randomness store:

TranscriptHash = SHA3-256( DS(“transcript/v1”) ||
round_id || I_round || H(V) ||
merkle_root_of([ provider_i || H(S_i) || H(Att_i) ]) )

### 6.2 Light Client
- Must verify `π_vdf` (already in `randomness/specs/LIGHT_CLIENT.md`).
- Must verify `TranscriptHash` via a compact proof:
- Either reference a Merkle branch showing `(provider_i, H(S_i), H(Att_i))` was used.
- Or accept the **no-QRNG** path when no samples included.
- Does **not** need the raw `S_i` bytes; hashes suffice for the beacon derivation.

---

## 7) Failure Modes & Liveness

- **No QRNG available** → Proceed with VDF-only path. Liveness preserved.
- **Late QRNG** (arrives after `t_mix_deadline`) → Ignored for this round.
- **Invalid attestation** → Sample excluded; proceed with remaining (if any).
- **Conflicting samples** (same provider) → First valid included; others ignored.
- **DoS flood** → Cap `m_max` samples per round (policy); process O(m) with linear hashing.

---

## 8) Parameters (Policy)

Configurable in `randomness/config.py` and network policy:

- `t_mix_deadline` relative to `t_vdf_start` (e.g., +2s).
- `m_max` maximum accepted samples per round (e.g., 8).
- Allowed **providers** / attestation roots.
- Size bounds for `S_i` and `Att_i`.
- Hash function family (v1: SHA3-256).
- Whether to require DA anchoring per sample.

---

## 9) Multiple QRNG Providers

We encourage **diversity** of providers. Identity is included in the transcript hash to hedge co-located or correlated sources. In future versions, we may:
- Include **raw bytes** via a tree KDF (costlier for light proofs).
- Add **weighted** inclusion based on historical SLA.

For v1: identity + `H(S_i)` are sufficient to bind the transcript and avoid biasing tactics.

---

## 10) Pseudocode

```python
def finalize_beacon(round_id, I_round, vdf_output, vdf_proof, qrng_samples, policy):
  assert verify_vdf(I_round, vdf_output, vdf_proof)

  accepted = []
  for s in qrng_samples:
      if time_now() > policy.t_mix_deadline: break
      if not verify_attestation(s.att, policy): continue
      if not (policy.min_len <= len(s.bytes) <= policy.max_len): continue
      if seen_provider(s.provider): continue
      if policy.require_da and not da_has(H(s.bytes)): continue
      accept(s.provider, H_bytes=H(s.bytes), H_att=H(s.att))
      accepted.append(s)

  provider_list_hash = H(concat(sorted([a.provider for a in accepted])))
  header = DS("mix/v1") + u64_be(round_id) + DS(provider_list_hash)

  if not accepted:
      R = header + DS("no-qrng") + vdf_output_or_hash(vdf_output)
  else:
      R = header + concat([a.H_bytes for a in accepted]) + vdf_output_or_hash(vdf_output)

  B = SHA3_256(R)
  transcript_root = merkle_root([(a.provider, a.H_bytes, a.H_att) for a in accepted])

  return BeaconOut(
      round_id=round_id,
      beacon=B,
      vdf=H(vdf_output),
      transcript_hash=H(DS("transcript/v1")+u64_be(round_id)+H(I_round)+H(vdf_output)+transcript_root),
      providers=[a.provider for a in accepted]
  )


⸻

11) Security Notes
	•	Bias resistance: The VDF input is fixed before the mixing window. Providers cannot see B until V is fixed; QRNG inclusion is hash-only.
	•	No single point of failure: If either the VDF or at least one QRNG sample is good, B is good.
	•	Replay protection: Round id, provider ids, and sample hashes are bound into the transcript; reusing old samples in a new round changes B.
	•	Privacy: Raw QRNG bytes need not be public; hashes are sufficient for derivation & auditing.

⸻

12) Migration & Versioning
	•	v1: SHA3-256 hedged mixer, hash-only QRNG inclusion, DA-optional.
	•	Future: cSHAKE/XOF domain separation, tree-KDF with raw bytes (opt-in), provider weighting.

⸻

13) Test Vectors

Add vectors under randomness/test_vectors/beacon.json:
	•	Case A: VDF-only.
	•	Case B: Single QRNG (valid).
	•	Case C: Two QRNGs (one invalid → excluded).
	•	Case D: Late QRNG (ignored).

Each vector includes round_id, I_round, H(vdf_output), provider ids, H(S_i), and expected B.

⸻

14) References
	•	randomness/beacon/finalize.py — finalize & transcript hashing
	•	randomness/qrng/* — providers & attestation stubs
	•	randomness/specs/LIGHT_CLIENT.md — verification rules
	•	da/* — content-addressable storage for QRNG blobs

⸻

Changelog
	•	v1: Define hedged SHA3 mixer, transcript hashing, provider identity binding, policy knobs.
