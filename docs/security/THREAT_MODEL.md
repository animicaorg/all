# Animica Threat Model
_Adversaries, capabilities, mitigations, and residual risk across the Animica stack (node, P2P, PoIES, DA, VM(Py), AICF, Randomness, RPC/Studio Services, Wallets, SDKs, Website/Explorers, Installers)._  
This document is a living companion to the specs. It informs design reviews, code audits, and incident response.

---

## 1) Scope & Security Objectives

**In-scope assets**
- **Consensus state & safety**: canonical chain, fork-choice correctness, block finality latency bounds.
- **Liveness**: block production/propagation, DA availability sampling, P2P mesh forward progress.
- **Integrity**: transaction execution determinism, receipt/log correctness, proof verification soundness.
- **Economics**: fee/reward accounting, PoIES fairness, AICF payouts/slashing, anti-grind measures.
- **Keys & identities**: PQ key handling (wallets, node identity), update signing keys, CI artifacts.
- **User safety**: phishing-resistant UX (wallets), no secret leakage via RPC/website/services.

**Out of scope (non-goals)**
- Content moderation of user blobs or smart-contract business logic.
- Privacy guarantees beyond what the base protocols provide.
- Rich side-channel resistance on commodity hardware (we aim for practical hygiene).

---

## 2) Trust & Design Assumptions

- **Honest-majority of *work*** over time windows. PoIES accepts blocks where `S = −ln(u) + Σψ ≥ Θ`; caps/Γ/diversity tame concentration. Short windows may be adversarial.
- **TEEs/QPUs**: supply attestation; we trust *verification logic* and *root stores* but not providers. SLAs/slashing backstop.
- **DA**: light clients rely on random sampling + namespaces + erasure coding; withholding detectable with configured probability bounds.
- **Randomness**: commit–reveal + VDF is primary; optional QRNG mix does not replace VDF security.
- **ZK**: verifying key (VK) pinning & policy allowlist; no dynamic VKs without governance.
- **Wallets**: secrets stay client-side; Studio Services never hold keys (no server-side signing).
- **P2P**: authenticated channels (Kyber KEM → AEAD) but the network is byzantine; Sybils are expected.

---

## 3) Adversary Classes

1. **Opportunistic attacker**: spam/DoS, phishing, trivial reorg attempts, low-cost mempool games.
2. **Economically motivated cartel**: colluding miners/providers, fee/MEV extraction, targeted withholding, localized eclipse, mild bribery.
3. **Advanced/nation-state**: route-hijacking, wide-scale eclipse/Sybil, TEE supply-chain compromise, time skewing, targeted CI/infrastructure attacks.

---

## 4) Capabilities & Attack Surfaces

### 4.1 Consensus & PoIES
- **Grinding** on `u`-draw / nonce domain; **ψ manipulation** via over-weighted proof kinds.
- **Cap bypass** attempts (per-proof/per-type/Γ) and **nullifier reuse**.
- **Retarget abuse**: oscillations to gain luck (Θ schedule gaming).
- **Fork-choice games**: shallow reorgs, equivocation, tie-breaking manipulation.

### 4.2 Proof Systems (Hash/AI/Quantum/Storage/VDF)
- **AI/Quantum**: forged attestations, trap-circuit spoofing, QoS fabrications, replayed outputs.
- **Storage**: fake heartbeats, duplicate identities, late proofs.
- **VDF**: fake proofs, precomputation/parallelism claims (Wesolowski verification errors).

### 4.3 ZK Verification
- **Malleable inputs**: inconsistent transcript hashing across toolchains.
- **VK substitution**: using a different verifying key.
- **Curve/KZG pitfalls**: subgroup checks omitted, infinity or identity edge-cases.

### 4.4 Data Availability
- **Withholding**: publish header roots but not blobs.
- **NMT misuse**: namespace boundary violations, crafted leaves, malformed proofs.
- **Erasure tricks**: invalid shard layouts to game sampling.

### 4.5 Randomness (Commit–Reveal → VDF → QRNG)
- **Commit grinding & collusion**; selective reveals.
- **Eclipse/clock skew** to stretch windows.
- **VDF proof forgery** or parameter downgrade.
- **QRNG spoofing** (unattested bytes).

### 4.6 P2P & Networking
- **Sybil & eclipse** of miners/light clients.
- **Flooding/DoS**: headers/tx/shares/logs.
- **Handshake downgrade**: non-PQ, weak AEAD.
- **Compression bombs** and **resource exhaustion**.

### 4.7 Mempool & Fees
- **Spam/cheap flooding**; **priority inversion**; **replacement griefing** (RBF).
- **Underpriced opcodes/tx kinds**.
- **Fee oracle manipulation** (if any UI helpers).

### 4.8 Execution & VM(Py)
- **Nondeterminism** via imports, time, randomness misuse.
- **Gas accounting bugs**; **reentrancy-like** patterns via syscalls (guarded).
- **ABI confusion**; event/log malleability.

### 4.9 AICF (Providers/Economics)
- **Identity farming** (stake splitting), **lease theft**, **result replay**.
- **SLA evasion** (latency/QoS gaming), **payout inflation**.
- **Attestation supply-chain** compromise.

### 4.10 Wallets/Extensions/SDKs
- **Phishing/permission abuse**, **blind signing** prompts.
- **Mnemonic theft** (clipboard/extension APIs).
- **Address encoding confusion** (HRP/alg_id mix-ups).

### 4.11 RPC/Studio Services/Website/Explorer
- **CORS bypass**, **host permission leakage**, **SSRF** via deploy/verify endpoints.
- **Artifact poisoning**; **OpenRPC/ABI tampering**.
- **Static site/XSS** in MDX/blog; **CSP gaps**.

### 4.12 Build/Release/Installers/CI
- **Supply-chain**: dependencies, PyPI/NPM crates, Rust crates.
- **Code-signing key** misuse; notarization bypass; updater feed hijack.

---

## 5) Mitigations (By Layer)

### 5.1 Consensus & PoIES
- **Strict domains** for nonce/mixSeed; `H(u)=−ln(u)` safe math.
- **Caps & Γ**: per-proof/per-type/total-Γ; **escort/diversity rules**.
- **Nullifiers** with TTL; storage in sliding windows; reuse rejection.
- **Θ retarget**: EMA with clamps; **observed λ vs target λ** windows.
- **Fork-choice**: longest/weight-aware with deterministic tie-break; **max reorg depth** alerts.

### 5.2 Proof Verification
- **Attestation stacks** (SGX/SEV/CCA) with pinned vendor roots; **trap benchmarks** for Quantum; **QoS metrics** normalized.
- **VDF**: Wesolowski verification; parameter pinning; bench sanity.
- **Policy adapter** maps verified metrics → ψ inputs; **no caps in adapters** to avoid double counting.

### 5.3 ZK (Groth16/PLONK/STARK)
- **VK cache** pinned by hash & circuit_id; **registry allowlist**.
- **Transcript hashing**: Poseidon/BLAKE variants documented; Fiat–Shamir helper used consistently.
- **Subgroup/infinity checks**; **KZG pairing validation**; size limits; msgspec schemas.

### 5.4 Data Availability
- **NMT**: strict namespace ordering; leaf encoding; inclusion/range proofs.
- **Erasure**: pinned (k,n) profiles; layout checks; decoding tests.
- **Sampling**: randomized query plans; target p_fail; light-client verifier and proofs.

### 5.5 Randomness
- **Commit–reveal**: binding commits; reveal verification; anti-bias aggregation.
- **VDF**: constant-time-ish verify; input derived from transcript; **beacon history** for replay detection.
- **QRNG**: optional, **attested** when available; deterministic mix (extract-then-xor) with transcript capture.

### 5.6 P2P
- **Kyber768 + HKDF → AEAD**; transcript hash; replay guards.
- **Token-bucket** rate limits per peer/topic; dedupe blooms.
- **Mesh scoring**; backoff; **address book** hygiene; multi-transport (TCP/QUIC/WS).
- **Validation before decode** for gossip topics.

### 5.7 Mempool
- **Stateless checks** (chainId/gas limits/size/PQ sig precheck).
- **Dynamic floor & watermark**; **surge multiplier**; RBF thresholds.
- **Per-sender fairness caps**; eviction policies; **ingress throttles**.

### 5.8 Execution & VM(Py)
- **AST validator** (imports/builtins/recursion/whitelist).
- **Deterministic stdlib**; **PRNG seeded** from tx hash; **gas-meter** OOG rules.
- **Syscalls** via **capabilities** layer: deterministic IDs; length caps; result-read next-block only.
- **ABI** canonical encoding; event/topic hashing consistency.

### 5.9 AICF
- **Provider staking**; allowlist/attestation verification; leases with renewal; **quotas**.
- **SLA evaluator**: traps_ratio/QoS/latency; **slashing** and cooling-off.
- **Payouts**: epoch accounting; audits; treasury hooks; claim proofs tied to block heights.

### 5.10 Wallets/SDKs
- **Domain-separated signing**; chainId binding; **bech32m HRP + alg_id** strict.
- **MV3** host permissions; session approvals; simulation-first UX; **no blind signing** defaults.
- **Keystore** AES-GCM; mnemonic PBKDF/HKDF-SHA3; WASM PQ gated & tested.

### 5.11 RPC/Studio Services/Website/Explorer
- **Strict CORS** allowlist; **rate-limits**; problem+json errors.
- **No server-side signing**; verify-only & relay; **artifact digests** & code-hash match.
- **CSP/HSTS/COOP/COEP** headers; MDX sanitization; **edge function** isolation; CSRF-free design.

### 5.12 Build/Release/Installers
- **Repro builds** where feasible; SBOMs & lockfiles; pin toolchains.
- **Code signing** (Apple, Windows, GPG for repos); Sparkle appcast **Ed25519** signatures.
- **CI secrets hygiene**: short-lived tokens, dedicated keychains, notarization checks.

---

## 6) Detection, Telemetry & Response

- **Prometheus metrics**: RPC latency, mempool admits/rejects, DA sampling, P2P RTT, AICF SLAs, verifier counts.
- **Structured logs** with request IDs; rate-limit counters; WS subscription stats.
- **Alerts**: head stall, fork depth, p_fail drift, surge in rejects, SLA degradation, beacon finalize lag.
- **Incident playbooks**: (a) reorg or fork instability, (b) DA availability warnings, (c) attestation root incident, (d) updater/appcast compromise.

---

## 7) Residual Risks & Trade-offs

- **Short-window luck** remains; Θ clamps balance responsiveness and stability.
- **TEE/QPU root-of-trust**: if vendor roots are compromised, on-chain verification alone cannot detect it; rely on policy updates & revocations.
- **Sampling false negatives**: DA guarantees are probabilistic; operational targets must be met.
- **User phishing**: wallets reduce risk but cannot eliminate social engineering.

---

## 8) Review Checklist (per change)

- Cryptography parameters pinned? (curves, KZG, Poseidon, VDF)
- Domain separation present for all hashes/signatures?
- Canonical encoding stable? (CBOR/ABI/headers)
- Resource caps enforced? (sizes/gas/time/queue)
- Policy roots/VKs updated with signatures and hashes?
- P2P/mempool rate limits tuned and tested?
- Telemetry added and alerts defined?
- Docs updated: SECURITY/THREAT_MODEL & CHANGELOG.

---

## 9) References

- Specs: PoIES, DA/NMT, Randomness, VM(Py), RPC, AICF, ZK.
- Modules: `consensus/`, `proofs/`, `zk/`, `p2p/`, `mempool/`, `execution/`, `da/`, `randomness/`, `wallet-extension/`, `studio-services/`.
- Policies & roots: `spec/poies_policy.yaml`, `zk/registry/*`.

> _This threat model evolves with the system. Submit PRs to refine adversary capabilities, add mitigations, and link postmortems._
