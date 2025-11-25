# Randomness Security — Bias Resistance, Liveness, Monitoring (v1)

This document complements:
- `randomness/specs/COMMIT_REVEAL.md` — binding rules & aggregation
- `randomness/specs/VDF.md` — Wesolowski verification
- `randomness/specs/BEACON.md` — full round transcript & construction
- `randomness/specs/LIGHT_CLIENT.md` — verification for light nodes
- `docs/randomness/BEACON_API.md` — contract-facing API & light proofs

It focuses on **threats**, **parameters**, and **operational monitoring** required to deliver an unbiased and live beacon.

---

## 1) Threat Model

**Goals**
- **Unbiasability:** No adversary should steer the beacon more than negligibly.
- **Liveness:** Rounds finalize within configured deadlines, producing a usable beacon.
- **Auditability:** Anyone can verify the transcript with light proofs.

**Adversary**
- Controls a fraction of participants able to commit/reveal.
- May have faster hardware (but not fundamentally breaking the VDF).
- Network manipulation within normal bounds (delays, withholding, bursts).
- Attempts **last-revealer bias**, **grinding**, or **withholding**.
- May run **DoS** against commit/reveal endpoints or miners producing VDF proofs.

---

## 2) Bias Surfaces & Mitigations

### 2.1 Commit–Reveal: Withholding & Last-Revealer Bias
**Surface.** A participant observes partial reveals and decides whether to publish their own to influence the aggregate.

**Mitigations**
1. **Windowing:** Fixed `commit_window` followed by fixed `reveal_window`. No reveals accepted outside window.
2. **Binding:** Commitments are domain-separated and include `(address | salt | payload)`; rejects if reveal mismatches.
3. **Aggregation:** Use a **commutative, order-independent** fold (e.g., `H(xor_fold(reveals))` or `H(hash-chain)`):
   - Let `A = H(reveal_1 ⊕ reveal_2 ⊕ ... ⊕ reveal_m)`.
   - Changing a single reveal flips `A` unpredictably unless the adversary **precomputed** alternatives.
4. **Stake/Rate Limits (optional):** Per-identity caps and optional bonding/slashing for missed or malformed reveals.
5. **No Early Peek:** Commitments **don’t leak** reveal content (preimage-resistant hash with domain tag).
6. **Reveal Quorum (optional):** Require `m ≥ m_min` reveals to finalize; if not met, fallback (see 3. Liveness).

**Residual Risk.** If an adversary controls the majority of potential revealers, they can still bias. Economic and access controls (allowlists for specific roles, reputation, or staking) reduce this risk.

---

### 2.2 Grinding via VDF Input Selection
**Surface.** Pick or tweak the VDF input to hunt for favorable outputs.

**Mitigations**
- **Deterministic seed_x:**  
  `seed_x = H("rand/seed_x" | round_id | prev_beacon | A | params_hash)`  
  where `A` is the aggregated reveals; **no per-operator choice** exists.
- **Params pinning:** `params_hash` binds all VDF parameters (modulus/id, T, soundness bits) to the transcript.
- **Header binding:** The final `(round_id, beacon)` is **committed in the header** (root or direct field) so reorgs can’t substitute a different seed once finalized.

---

### 2.3 Fast-Prover Advantage (VDF Undersizing)
**Surface.** If the delay parameter `T` is too small, a faster prover with optimized hardware can try **on-the-fly** many candidate aggregates (e.g., by coordinating multiple reveal sets) to steer the beacon.

**Mitigations**
- **Choose T conservatively:** Target wall-clock verification time on commodity machines (e.g., < 100 ms) while **prover time** is orders of magnitude larger (seconds). Tune using `docs/randomness/VDF_PARAMS.md` and real benchmarking.
- **Soundness `l` bits:** Enforce `l ≥ 128` challenge bits for negligible proof forgery risk.
- **Audit schedule:** Periodically re-evaluate T as hardware improves; rotate `params_hash`.

---

### 2.4 Replay / Cross-Network Contamination
**Surface.** Reusing beacons or proofs from another network/parameter set.

**Mitigations**
- **Domain separation** with network `chain_id` and `params_hash` everywhere.
- **Light proof enforcement** checks `params_hash` and header link before accepting a beacon.

---

### 2.5 Endpoint DoS
**Surface.** Flood commits/reveals or VDF proof submission, delaying finalization.

**Mitigations**
- **Token buckets & per-IP/per-key rate limits** (see `randomness/rpc/methods.py` + gateway config).
- **Separate ingress paths**: Commit/Reveal served by horizontally scalable stateless edge; proof submission may be protected/privileged (e.g., miners).
- **Back-pressure** and **bounded DB queues** with eviction policy for spam commits (keep newest or highest-reputation first).

---

## 3) Liveness Strategy

### 3.1 Round Deadlines
- **Commit window:** `ΔC` blocks.
- **Reveal window:** `ΔR` blocks after commit close.
- **VDF window:** proof must arrive within `ΔV` blocks (or immediate if produced by miners).

### 3.2 Finalization Rules
1. If `reveals ≥ m_min`, aggregate and attempt VDF proof verification.
2. If **no VDF** by `ΔV`:
   - **Fallback A:** Use deterministic **timeout VDF input** with `prev_beacon` only (still unbiased; no fresh entropy).
   - **Fallback B:** **Carry forward** `prev_beacon` (same as “sticky beacon”). Contracts must be prepared for same-beacon repeats.
3. If **insufficient reveals** by reveal deadline:
   - Aggregate **empty set** (i.e., `A = H("rand/empty")`) and proceed → reproducible but no fresh participant entropy.
4. Finalize the round with the computed `beacon` and **record causes** (normal / timeout / carry-forward) in metadata for monitoring.

**Contract Guidance**
- For **lotteries/auctions**, use the **next round** or an **internal commit–reveal** to avoid last-block dependence.
- For **VRF-like sampling**, mix in **caller/account-specific salt** (see Beacon API).

---

## 4) Monitoring & Alarming

### 4.1 Metrics (Prometheus)
From `randomness/metrics.py`:
- `rand_commits_total{status}` — accepted/rejected/late
- `rand_reveals_total{status}` — accepted/rejected/late/bad-preimage
- `rand_reveals_quorum_ratio` — `reveals / expected_participants`
- `rand_vdf_verify_seconds` — histogram of **verify** time (not proving)
- `rand_round_duration_blocks` — commit→finalize span
- `rand_beacon_carry_forward_total` — count of fallback carry-forwards
- `rand_beacon_repetition_gauge` — consecutive rounds with identical beacon
- `rand_proof_fail_total{reason}` — invalid proof, wrong params hash, header bind fail

### 4.2 SLOs
- **Finalize within** `ΔC + ΔR + ΔV + ε` blocks for ≥ 99.9% of rounds.
- **Reveal quorum** (if configured) ≥ `m_min` for ≥ 95% of rounds (tune per network).
- **Verify time** p99 under target (e.g., 50–100 ms) on reference hardware.

### 4.3 Alerts
- **ALERT:BeaconStuck**: No finalized beacon for `> 2 × (ΔC+ΔR+ΔV)` wall-clock.
- **ALERT:HighCarryForwardRate**: More than `X%` of last `N` rounds carried forward.
- **ALERT:LowRevealQuorum**: Rolling average below threshold.
- **ALERT:VDFVerifyAnomalies**: Verification failures > 0 in last `N` rounds.
- **ALERT:BeaconRepetition**: ≥ `K` repeated beacons (investigate liveness).

### 4.4 Logging & Audit
Each round logs:
- `round_id`, `commit_count`, `reveal_count`, `aggregate_hash`, `params_hash`
- `vdf: {T, proof_size, verify_ms}`, `finalization_cause`
- `header_link: {height, rand_root/hash}`

Persist logs for **correlating** application-level incidents (e.g., lottery disputes).

---

## 5) Parameter Guidance

- **Soundness** `l ≥ 128` bits.
- **Delay** `T` tuned so **proving** takes **seconds** (or miner-integrated time budget) while **verification** is ≤ 100 ms on commodity CPUs.
- **Windows** sized to typical block-time variability with **buffers** for network jitter.
- **Retention**: Keep recent `k` rounds in-DB for contract reads and light proofs.

---

## 6) Light Client Security

- Must start from a **trusted checkpoint header hash**.
- Verify **header chain** linkage and **rand_root** inclusion (or inline field).
- Enforce `params_hash` match; reject on mismatch.
- Reject proofs where `(round_id, beacon)` does not match the included commitment path.

---

## 7) Operational Playbook

1. **Initial bring-up**
   - Bench `T` and set SLOs.
   - Configure rate limits on commit/reveal/proof endpoints.
2. **During incident: low reveals**
   - Check rate limits / gateway errors.
   - Communicate expected fallback (carry-forward) to dapp operators.
3. **During incident: proof missing**
   - Confirm miner availability and VDF queue health.
   - If repeated, consider temporarily **raising ΔV** or enabling **timeout beacon** path.
4. **After incident**
   - Rotate `params_hash` only if needed (post-announcement & client updates).
   - Backfill postmortem with metrics & remediation.

---

## 8) Security Checklist

- [ ] Domain separation everywhere (commit, seed_x, beacon, API).
- [ ] `params_hash` pinned & published.
- [ ] Windows enforced at admission (commit/reveal).
- [ ] Aggregation is commutative and order-independent.
- [ ] VDF verifier constant-time w.r.t. secrets; bounds-checked inputs.
- [ ] RPC endpoints rate-limited; DoS-resistant.
- [ ] Light proof includes header binding & inclusion.
- [ ] Metrics + alerts configured; dashboards reviewed weekly.
- [ ] Contracts docs warn against **same-round** beacon for adversarial settings.

---

## 9) Appendix — Example Aggregation

reveals: list[bytes32]

acc = bytes32_zero
for r in reveals_sorted_by_hash:   # order-independent via sorted hash; or XOR is commutative
acc = acc XOR r
A = H(b”rand/aggregate” | acc | round_id | params_hash)
seed_x = H(b”rand/seed_x” | round_id | prev_beacon | A | params_hash)

Then compute y = x^(2^T) and beacon = H(“rand/beacon”|round_id|params_hash|seed_x|y|prev_beacon)[:32]

**Note:** Sorting by the hash of reveals eliminates order dependence without leaking time bias; XOR is already commutative, sorting mainly stabilizes the transcript.

---

**Versioning**
- This doc is **v1**; any change to the transcript must update `params_hash` and public documentation.

