# Animica: A Utility-Oriented L1 with Proof-of-Integrated-External-Work (PoIES)

**Version:** 0.9 (Draft)  
**Status:** Public preview (aligns with `spec/` and reference implementation)  
**Authors:** Animica Core Team

> **Abstract —** Animica is a Layer-1 blockchain that blends classical hash-based mining with verifiable *useful* work—AI inference, quantum circuits, data availability, and VDFs—under a single, conservative consensus umbrella called **Proof-of-Integrated-External-Work (PoIES)**. A block is accepted when a miner (or committee) presents a random draw plus a verifiable mixture of external proofs that together exceed a dynamically retargeted threshold. PoIES turns latent demand for compute (AI/quantum), storage availability (DA), and verifiable delay (VDF) into on-chain security, while preserving miner simplicity, determinism, and light-client verifiability. We provide the core math, policy levers, and system architecture that bind PoIES to execution, data availability, post-quantum (PQ) cryptography, P2P, and on-chain economics.

---

## 1. Motivation

Public blockchains mostly secure themselves by paying for hashwork (PoW) or stake lockups (PoS). The former wastes energy that could be redirected to socially valuable computation; the latter externalizes security assumptions to capital markets and liveness conditions.

**Animica** proposes a third path:
- Keep the **simplicity and neutrality** of PoW’s open participation and do-once verification.
- Integrate **verifiable external work** with conservative caps to avoid centralization.
- Preserve **light-client security** with succinct proofs and DA sampling.

This yields a security curve where *hashwork* remains a reliable baseline, while *useful proofs* amortize network issuance into services the ecosystem already needs.

---

## 2. Consensus Overview: PoIES

Each candidate block carries:
- A **uniform random draw** `u ∈ (0,1]` derived from a header-bound nonce (hashwork),
- A set of **external proofs** `p ∈ P = {AI, Quantum, Storage, VDF, HashShare}`, each mapped to a non-negative contribution `ψ(p)` after verification and policy clipping.

A block is accepted if:

\[
S \;=\; H(u) \;+\; \sum_{p \in P} \psi(p) \;\;\ge\;\; \Theta,
\quad \text{with}\; H(u) = -\ln(u)
\]

- `H(u)` is the standard lottery term from the exponential race.
- `ψ(p)` are additive, non-negative contributions from verifiable proofs.
- `Θ` is the **difficulty threshold**, retargeted by an EMA to maintain target block interval.
- Policy **caps** ensure no single proof type dominates and that total external credit is bounded by a global `Γ`.

### 2.1 Mapping proofs to ψ
Each proof is verified by a type-specific module producing measurable metrics (e.g., AI redundancy/QoS, quantum traps, storage PoSt windows, VDF seconds). A chain policy `(spec/poies_policy.yaml)` maps metrics → `ψ` with:
- **Per-type caps** (e.g., max AI share per block),
- **Diversity bonus** (escort rules) to encourage multi-proof mixes,
- **Global Γ cap** limiting the sum of all external contributions.

### 2.2 Retargeting and stability
`Θ` follows a fractional EMA with clamps to resist oscillations and timestamp jitter. We keep the classic PoW monotonicity—harder targets reduce the acceptance probability—and preserve a direct light-client story.

---

## 3. Security Model

- **Soundness:** Every `ψ(p)` arises from a verified, attested proof with policy-pinned roots (TEE/QPU certs, DA parameters, VDF params). If a proof fails verification or exceeds caps, its `ψ` is zeroed; the block may still pass on hashwork alone.
- **Grinding resistance:** Miners cannot choose `u` independently of the header; nullifiers prevent reusing external proofs across blocks. Escort/diversity rules disincentivize cherry-picking.
- **Centralization controls:** Caps (`Γ`, per-type ceilings, nullifier TTLs) ensure that specialized hardware cannot fully crowd out baseline hashwork.
- **Light clients:** Only header roots, succinct proof receipts, and DA sampling proofs are required to audit acceptance and availability.

---

## 4. System Architecture

Animica is modular; each module is independently testable and pinned by on-chain roots:

1. **Spec** (`spec/`): canonical parameters, schemas, OpenRPC, math notes.
2. **Core** (`core/`): canonical serialization, headers/blocks/txs, persistent KV.
3. **Consensus** (`consensus/`): PoIES scorer, caps, retarget, fork choice.
4. **Proofs** (`proofs/`): verifiers for HashShare, AI, Quantum, Storage (PoSt heartbeat), VDF.
5. **DA** (`da/`): Namespaced Merkle Trees, erasure coding, DAS verification, retrieval API.
6. **Execution** (`execution/`): deterministic state machine for transfers/events; VM bridge.
7. **VM(Py)** (`vm_py/`): audited subset of Python with a small-step IR and deterministic stdlib.
8. **Capabilities** (`capabilities/`): contract-facing syscalls (AI/Quantum/DA/zk/random) with deterministic receipts.
9. **P2P** (`p2p/`): PQ handshake (Kyber), encrypted transports (TCP/QUIC/WS), gossip and sync.
10. **RPC** (`rpc/`): FastAPI JSON-RPC & WS with pre-admission checks and OpenRPC schema.
11. **AICF** (`aicf/`): AI/Quantum provider registry, staking, SLAs, pricing, and payouts.
12. **Randomness** (`randomness/`): commit-reveal → VDF → optional QRNG mixing.
13. **Wallet/SDK/Studio**: browser extension (MV3), Flutter desktop/mobile builds, multi-language SDKs, web IDE.
14. **ZK** (`zk/`): verifiers and adapters (Groth16, PLONK, STARK demo), policy pinning, native accelerations.

---

## 5. Data Availability (DA)

Blocks include a **DA root** computed over namespaced erasure-coded shares. Light nodes sample random positions, verify NMT proofs, and accept availability with a tunable failure bound. The **retrieval service** and **P2P DA topics** provide independent paths to collect shares. This design defends against withhold attacks and underpins safe contract execution and zk verification downstream.

---

## 6. Execution & Contracts

- Deterministic **Python-VM** executes transfers and simple contracts with tight metering.
- A minimal, capability-oriented stdlib enables **blob pinning**, **AI/Quantum enqueue**, **zk.verify**, and **beacon reads**, each returning deterministic receipts **consumable next block**.
- Receipts and logs form **stable Merkle roots** in receipts bloom; execution results are reproducible.

---

## 7. Cryptography (PQ-first)

- **Addresses & Signatures:** Dilithium3 / SPHINCS+; KEM: Kyber-768 (for P2P).
- **Domains:** Strict domain separation strings for signatures, headers, and nullifiers.
- The wallet, SDKs, and P2P stack default to PQ algorithms with graceful fallbacks for development.

---

## 8. Economics

- **Fees:** Classic base/tip split with a dynamic floor and surge multiplier in the mempool.
- **Rewards:** Block rewards are split among **leader**, optional **committee**, and **AICF** escrow. External proofs that contribute `ψ` participate in reward accounting through receipts.
- **AICF:** Providers stake, accept jobs, and return proofs. SLAs (trap ratios, QoS, latency) determine payouts; misbehavior triggers **slashing**.

Policy knobs—issuance, fee burn, Γ cap, per-type ceilings—allow governance to balance security, utility, and decentralization.

---

## 9. Light Clients & Upgrades

- **Light verification**: headers, DA samples, and beacon light proofs (hash chain + VDF) allow clients to track chain health and verify inclusion without full state.
- **Upgrades**: versioned parameters and feature flags with explicit hard/soft-fork gates in `spec/params.yaml`, plus **alg-policy roots** for PQ rotations and zk circuit VK pinning.

---

## 10. Formal & Empirical Validation

- **Lean/K** skeletons capture acceptance lemmas and VM small-step semantics.
- **Test vectors** across txs, headers, proofs, DA, and VM provide cross-implementation reproducibility.
- **Benches** report verifier throughput (zk, DA, VDF), PoIES scoring latencies, and end-to-end block assembly time.

---

## 11. Threats & Mitigations

- **Grinding:** Header-bound nonces, nullifier TTLs, and escort rules reduce selection bias.
- **DoS:** Strict RPC/Mempool rate limits, P2P token buckets, and payload caps at decode time.
- **Attestation faults:** TEE/QPU roots are pinned; proofs carry provider identity and traps; slashing applies.
- **Centralization:** Γ and per-type caps bound specialized hardware advantage; baseline hashwork remains viable.
- **Supply chain:** Reproducible builds, SBOMs, and pinned dependencies across critical paths.

---

## 12. Roadmap

- **M0**: Core/consensus/proofs/DA/randomness with CPU miner and devnet.  
- **M1**: VM(Py) execution, capabilities, Studio & SDKs; AICF MVP.  
- **M2**: ZK verifiers (Groth16/PLONK) and native accelerations; extended wallets.  
- **M3**: Production testnet; governance bootstrap; broader provider onboarding.

---

## 13. Notation (quick)

- `Θ` — target threshold (difficulty).  
- `Γ` — global cap on external `ψ` per block.  
- `ψ(p)` — contribution from proof `p` after verification and caps.  
- `H(u)` — `−ln(u)` from a uniform draw tied to the header nonce.  

---

## 14. Conclusion

Animica reframes “mining” as a market for verifiable, socially valuable work—without sacrificing the simplicity and verifiability that made PoW robust. PoIES integrates useful proofs under strict policy caps, preserves open participation, and exposes deterministic interfaces for applications.

---

## Appendix A — Acceptance Probability (Sketch)

Let `A = Σψ` after caps. Since `u ~ Uniform(0,1]`, we have `H(u) ~ Exp(1)`. Acceptance is:

\[
\Pr[S \ge \Theta] = \Pr[H(u) \ge \Theta - A]
= \begin{cases}
1, & A \ge \Theta \\
e^{-(\Theta - A)}, & A < \Theta
\end{cases}
\]

Thus `A` shifts the exponential tail to the left while retaining the memoryless property, enabling familiar retarget math and combining multiple independent sources of verifiable work.

---

## Appendix B — Light Verification Checklist

1. Verify header signatures/domains and policy roots.  
2. Check PoIES auxiliary receipts Merkle root and (optionally) sample attached proofs.  
3. Run DA sampling against the DA root to the configured failure bound.  
4. Track `Θ` via the difficulty schedule and ensure header chain monotonicity.  

---

**License:** © Animica Authors. Portions may be licensed under the project’s main license; see repository root.  
**Contact:** community@animica.org • Security: security@animica.org
