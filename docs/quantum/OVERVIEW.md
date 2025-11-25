# Quantum/OVERVIEW — What “Quantum-Ready” Means in Animica

**Status:** Stable (v1)  
**Audience:** protocol engineers, provider operators, security reviewers  
**Related:** `docs/spec/proofs/QUANTUM_V1.md`, `docs/pq/POLICY.md`, `aicf/specs/*`, `consensus/*`, `proofs/quantum_attest/*`

---

## 1) The Problem & Our Position

Quantum hardware is real but uneven: devices differ widely in qubit count, fidelity, connectivity, and access models. A production L1 cannot assume broad quantum availability *today*, but we **can**:
- **Verify** attestations and trap-circuit results supplied by quantum providers.
- **Account** for such work inside consensus using **PoIES** (ψ contributions) without central trust.
- **Pay** providers fairly (or slash them) based on **SLA metrics** and on-chain proofs.

**“Quantum-ready”** in Animica means the protocol, SDKs, and economics already support quantum workloads in a way that is **secure, auditable, opt-in, and incrementally useful**—even before quantum is ubiquitous.

---

## 2) What “Quantum-Ready” Concretely Includes

1. **PQ cryptography everywhere (defensive posture):**
   - Node identity & wallet keys use **Dilithium3 / SPHINCS+** (see `docs/pq/*`).
   - P2P handshake uses **Kyber768 KEM** with **HKDF-SHA3-256** session derivation.
   - This is **orthogonal** to quantum compute; it mitigates harvest-now-decrypt-later risk.

2. **Quantum proof primitive (productive posture):**
   - A first-class **QuantumProof v1** envelope (see `docs/spec/proofs/QUANTUM_V1.md`).
   - Inputs: provider identity/attestation, **trap-circuit** outcomes, QoS/latency stats.
   - The proof maps to consensus metrics via `proofs/policy_adapter.py` → ψ inputs.

3. **Attestation & traps (soundness posture):**
   - Provider certs verified by `proofs/quantum_attest/provider_cert.py`.
   - Trap circuits & sampling math in `proofs/quantum_attest/traps.py` with confidence bounds.
   - QoS and throughput benchmarks calibrated in `proofs/quantum_attest/benchmarks.py`.

4. **Economics & SLAs (operational posture):**
   - **AICF** (AI Compute Fund) matches jobs ↔ providers, prices units, enforces SLAs:
     latency, availability, traps ratio, error rates (`aicf/*`).
   - **Slashing** and **cooldowns** for misbehavior or degraded service.

5. **Consensus integration (incentive posture):**
   - Under **PoIES**, verified quantum work contributes a bounded ψ component.
   - **Caps/diversity rules** (Γ, escort `q`) prevent quantum dominance or single-provider capture (see `consensus/caps.py`, `consensus/scorer.py` and policy in `spec/poies_policy.yaml`).

---

## 3) Lifecycle: From Job to Consensus Credit

┌────────────┐     enqueue (capabilities)     ┌─────────────┐
│  Contract  │ ─────────────────────────────▶ │   AICF      │
└─────┬──────┘                                │ (matcher)   │
│                                       └─────┬───────┘
│ result next block                      lease│assign
│                                           ┌─▼───────────────┐
│                                           │ Quantum Provider │
│  consume result via read_result()         │  (attested)     │
▼                                           └─┬───────────────┘
┌────────────┐                                     │
│ Execution  │ ◀── capabilities.jobs.resolver ─────┘ produces:
│  (block N) │        (ingests on-chain proofs)       QuantumProof v1
└─────┬──────┘
│ receipts/logs
▼
┌──────────────┐    ψ map & caps     ┌───────────────┐
│  Consensus   │ ◀────────────────── │ Proof Verifier│
│  (PoIES)     │                     │   (quantum)   │
└──────────────┘                     └───────────────┘

- **Contracts** can enqueue quantum work (devnet/demo flows) using `capabilities/host/compute.py`.
- Providers complete jobs and publish **QuantumProof** with attest & trap outcomes.
- Verifier checks the envelope; policy adapter converts to **ψ inputs**.
- Consensus updates **S = −ln(u) + Σψ** and enforces **caps/Γ** as per network policy.

---

## 4) Security Model (High Level)

- **Identity & attestation:** Providers hold signed identities; chains of trust are transparent and pinned (registry roots in `proofs/attestations/vendor_roots/*`).
- **Trap circuits:** Statistical guarantees bound cheating probability; policy sets minimum **trap ratio** and **confidence**.
- **QoS & latency:** Reported and audited; SLA infra penalizes chronic underperformance.
- **No consensus shortcuts:** Quantum ψ is **additive** and **capped**—it cannot bypass headers/tx validity or fork-choice safety rules.
- **Fallback safety:** If quantum proofs are absent or invalid, the chain proceeds with hash/AI/storage/VDF work—no liveness dependency on quantum.

---

## 5) Interoperability & Formats

- We keep **envelope fields stable** and versioned (see `docs/spec/proofs/ENVELOPE.md`).
- JSON fixtures live under `proofs/test_vectors/quantum.json`; registry roots under `aicf/fixtures/*`.
- All verifiers are pure-Python with optional **native speedups** where applicable.

**QuantumProof v1 (sketch):**
```json
{
  "type_id": "quantum_v1",
  "provider_attest": { "cert": { "...": "..." }, "sig": "..." },
  "traps": { "seed": "0x..", "total": 8192, "correct": 8127 },
  "bench": { "depth": 16, "width": 64, "shots": 4096 },
  "qos": { "latency_ms": 950, "availability": 0.999, "error_rate": 0.013 },
  "nullifier": "0x...", 
  "links": { "job_id": "…", "task_id": "…" }
}


⸻

6) Operations & Policy
	•	Network policy defines: enabled circuits, minimum trap coverage, target confidence, SLA bands, and ψ weight caps (see consensus/policy.py, spec/poies_policy.yaml).
	•	Provider onboarding: register → attest → stake → heartbeat.
	•	Runtime auditing: compare observed traps/QoS to claims; schedule slashes/jails on failure.

⸻

7) Roadmap
	•	More circuits/benchmarks: richer trap sets, cross-vendor comparability.
	•	Aggregated proofs: batching multiple jobs per block to amortize overhead.
	•	Marketplace transparency: public performance leaderboards via AICF RPC.
	•	Hybrid ZK attest: SNARK-wrapped attest summaries where feasible.

⸻

8) FAQs
	•	Is quantum mandatory? No. It is additive. Miners/validators can mine with other work types.
	•	Why not pure “quantum PoW”? Hardware asymmetry and access centralization. PoIES balances incentives, caps dominance, and keeps liveness independent.
	•	How do users benefit now? Useful compute can be requested by contracts; providers are paid on delivery; the network records auditable proofs.

⸻

9) References
	•	docs/spec/proofs/QUANTUM_V1.md — formal proof format & checks
	•	aicf/specs/* — economics, SLA, settlement, registry
	•	consensus/* — caps, scorer, retarget, fork-choice
	•	proofs/quantum_attest/* — attest & traps math

⸻

Changelog
	•	v1: Initial definition of “quantum-ready” posture; lifecycle, security, and policy hooks.
