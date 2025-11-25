# PQ ROTATION — RISKS
_Cryptographic & ecosystem risks when rotating post-quantum primitives (sig/kex)_

**Version:** 1.0  
**Status:** Active (living document)  
**Scope:** Changes to default signature (`pq.sigDefault`), accepted signature sets, and KEM/key-exchange (`pq.kex`), including dual-stack/parallel phases, deprecations, and emergency fallbacks.

Related: `PQ_POLICY.md`, `risk/UPGRADE_RISK_CHECKLIST.md`, `risk/PARAMS_BOUNDARIES.md`, `diagrams/UPGRADE_FLOW.mmd`.

---

## 1) Threat Model & Objectives

**Assets:** transaction validity & replay semantics, account control, consensus safety, wallet funds, cross-chain bridges, exchange custody, long-term verifiability of historical signatures.

**Adversaries:** capable of (a) breaking specific PQ schemes/parameters, (b) exploiting migration windows (downgrade, confusion, replay), (c) grinding RNG/side-channels to extract keys, (d) fragmenting the ecosystem via incompatible encodings.

**Objectives:** preserve liveness and security while migrating; avoid permanent balkanization; ensure old data remains verifiable (archival proofs).

---

## 2) Risk Classes

1. **Cryptanalytic Break / Parameter Weakness**  
   - Example: concrete attack reducing Dilithium-3 security, or a practical key-recovery on a KEM.  
   - **Impact:** signature forgeries, session compromise, chain reorg incentives via invalidation games.

2. **Encoding & Interop Drift**  
   - Ambiguity in public key / signature encodings (DER vs raw; base64 vs hex; endianness).  
   - **Impact:** transaction rejection, discrepant hashes, silent malleability.

3. **Downgrade & Confusion Attacks**  
   - Dual-stack period allows forcing peers/wallets to use the weaker/legacy scheme.  
   - **Impact:** attacker routes traffic through vulnerable path; later forges.

4. **Replay & Cross-Domain Reuse**  
   - Reusing keys/signatures across chains/apps; same address HRP with different sig rules.  
   - **Impact:** valid-elsewhere replay or accept-on-wrong-curve bugs.

5. **Side-Channel & RNG Failures**  
   - Non-constant-time code; low-entropy seeds; VM determinism gaps.  
   - **Impact:** key leakage, biased signatures enabling forgeries.

6. **Operational Fragmentation**  
   - Exchanges, custodians, and wallets adopt at different times.  
   - **Impact:** asset freezes, stuck funds, incompatible transaction flows.

7. **Size/Cost Regression**  
   - Larger sigs/pubs inflate tx size & fees; mempool policy breakpoints.  
   - **Impact:** DoS via size spikes, fee-market distortion.

---

## 3) Rotation Phases & Specific Risks

1. **Announce → Parallel (Dual-Stack)**
   - Accept {old,new}; default **sign** = new, **verify** = both.
   - **Risks:** downgrade; inconsistent SDK defaults; signature selection heuristics.
   - **Mitigations:**  
     - Network parameter: `pq.rotation.parallel_enabled=true` with max duration (≤ 180d).  
     - Node enforces `prefer=new` for local signing APIs.  
     - Explorer/wallet badges show scheme used; warn on legacy.

2. **Default Flip**
   - Mempool/miners prefer new scheme; fee tables updated for size.  
   - **Risks:** fee mispricing; policy rejects; miners black-box filter.  
   - **Mitigations:** mempool tests; fee weight calibrated (`vm.gas.price_*`, size weights).

3. **Deprecation Window**
   - Old scheme accepted with warnings; cut-off height/time announced.  
   - **Risks:** stranded keys at custodians; cold-storage latency.  
   - **Mitigations:** steward outreach, exchange readiness checklist, grace exceptions via allowlist (time-boxed).

4. **Sunset**
   - Reject old scheme in consensus & mempool; archival verification path retained off-chain.  
   - **Risks:** historical data unverifiable on light clients.  
   - **Mitigations:** bundle KATs, historical verifier module, explorer proof adapters.

---

## 4) Scheme-Specific Considerations

- **Signatures:** `ed25519`, `secp256k1`, `dilithium3`, `sphincs+`  
  - **dilithium3 → sphincs+ fallback:** larger sizes (SPHINCS+) stress fee/MTU; verify CPU heavy.  
  - **Key formats:** require explicit `algorithm` and `encoding` in signatures; forbid implicit inference.

- **KEM (KEX):** `kyber-768`, `ntru-hps-509`  
  - **Kyber → NTRU rotation:** ciphertext & pk sizes differ; handshake MTUs for WS/RPC must allow ≥ 2× current ceiling.  
  - **KDF binding:** KEM shared-secret must feed a named KDF with transcript hash (context binding) to defeat reflection.

---

## 5) Encoding & Address Rules

- **Address format:** unchanged (bech32m `am1…`) but **pubkeyTypes** list in chain metadata must include the new algorithm.  
- **Signature object:**  
  ```json
  {
    "algorithm": "dilithium3",
    "encoding": "base64",
    "publicKey": "<...>",
    "sig": "<...>",
    "payloadHash": "sha256:<hex>"
  }
No heuristic detection. Reject if algorithm not in allowlist.

Canonical JSON for signbytes; forbid whitespace/dict order variance.

6) Downgrade Protections
Strict preference: SDKs sign with pq.sigDefault unless an explicit override is set.

Memorized policy: once an account broadcasts with new, SDK stores “stick-to-new” flag.

Mempool policy: soft-disfavor legacy (weight bump) during parallel, hard-reject at sunset.

Chain param: pq.rotation.downgrade_lock_after_first_new=true.

7) Side-Channel & RNG
Determinism: VM cryptography must be constant-time or explicitly sandboxed; no branching on secrets.

RNG: signers derive per-message randomness from H(msg, sk, ctx) (deterministic where the scheme allows) with domain separation.

Testing: KATs + randomized tests under sanitizers; CPU feature gates pinned.

8) Size, Fees, & Mempool
Track avg_sig_bytes, avg_pub_bytes, tx_weight.

Update fee estimator tables; expose a simulation report in the proposal appendix.

Bounds: see PARAMS_BOUNDARIES.md for vm.gas.* and mempool limits.

9) Third-Party Dependencies
Wallets, custodians, exchanges, indexers, bridges: must ACK support before default flip.

Provide test vectors, end-to-end demo txs, and a canary market (low-risk) to exercise flows.

10) Monitoring & Rollback Signals
Dashboards (publish):

New vs legacy signature share (% of txs).

Rejected-by-policy counts (reason-coded).

Bridge failures, exchange deposit/withdraw errors.

Verification p95 latency.

Abort triggers (examples):

Legacy share remains > X% within Y days of flip.

Verify failures > baseline + Δ.

Widespread exchange/custody failures (≥ N venues).

Abort via feature flag per UPGRADE_RISK_CHECKLIST.md.

11) Parameters (governance/registries)
Bounded in params_bounds.json, current in params_current.json.

bash
Copy code
pq.sigDefault                         # one of allowed sigs
pq.allowedSigs[]                      # set
pq.kex                                # one of allowed KEMs
pq.rotation.min_notice_days           # 7–90
pq.rotation.parallel_enabled          # bool
pq.rotation.parallel_max_days         # ≤ 180
pq.rotation.default_flip_height       # height or 0 if time-based
pq.rotation.sunset_height             # height (or timestamp variant)
pq.rotation.downgrade_lock_after_first_new  # bool
pq.encoding.max_sig_bytes             # soft cap used by fee/policy
pq.encoding.max_pub_bytes             # soft cap used by fee/policy
12) Checklists
Pre-announce

 Security review & references for new scheme/params.

 KATs and interop fixtures published.

 Wallet/SDK releases tagged with behind-flag support.

Parallel phase start

 Chain params set; explorers label; mempool policy updated.

 Provider & exchange ACKs recorded in minutes.

Default flip

 Fee tables recalibrated; CI includes new-scheme vectors only.

 Incident comms ready; abort switch armed.

Sunset

 Legacy reject height set; archival verifier documented.

 Transparency post with outcomes & metrics.

13) Example Timeline (Kyber → NTRU KEX, Dilithium3 unchanged)
T-30d: Notice; SDK/wallet releases in beta; exchanges begin testing.

T-21d: Parallel KEX enabled; routers prefer NTRU; bridges dual-support.

T-7d: Default flip; Kyber still accepted.

T+30d: Sunset Kyber accept; archival verify path available.

14) Documentation & Artifacts to Ship with Proposal
Rationale & security level comparisons; parameter selections.

Test vectors (keys, enc, dec, sign, verify) in machine-readable JSON.

Size and performance study; mempool/fee impact simulation.

Exchange/custodian readiness statements.

Rollback/abort plan and dates/heights.

15) Known Pitfalls & Anti-Patterns
Silent SDK defaults differing by platform.

Heuristic signature parsing (“try all”) → oracle for downgrade.

Mixing address HRP between networks while changing pubkeyTypes.

Forgetting archival verification after sunset.

Allowing parallel longer than 180 days (increases attack surface).

16) Change Log
1.0 (2025-10-31): Initial taxonomy, parameters, timelines, and checklists.
