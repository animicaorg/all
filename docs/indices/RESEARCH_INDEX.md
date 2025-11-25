<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->

# Research Index · Animica

A curated map of all **research-facing documents in-repo** plus a bibliography of **external references** that inform Animica’s design (PoIES, DA/NMT, ZK, VDF, PQC, TEEs, randomness, economics).

Use this as the jumping-off point for deep dives, literature review, and citations in RFCs or papers.

---

## 0) Orientation

- Project overview: see the **Whitepaper** and the **PoIES math** notes below.
- For implementer-focused specs (schemas, wire formats, algorithms), prefer the `docs/spec/*` tree and the module READMEs.
- This index tracks research artifacts and background references; it doesn’t replace normative specs.

---

## 1) In-Repo Research Notes

### Consensus & PoIES
- **Whitepaper** — high-level system & motivation  
  `docs/research/WHITEPAPER.md`
- **PoIES Math** — derivations for \(S = -\ln u + \sum \psi \ge \Theta\), bounds, inequalities  
  `docs/research/POIES_MATH.md`
- **Fairness Analysis** — lottery vs. pools, variance, reward stability  
  `docs/research/FAIRNESS_ANALYSIS.md`

### Data Availability & Sampling
- **DA Sampling Proofs** — probability bounds, sample sizes, failure regimes  
  `docs/research/DA_SAMPLING_PROOFS.md`

### AI & Quantum Proofs
- **AI Traps** — red-team methodology, trap families, calibration metrics  
  `docs/research/AI_TRAPS.md`
- **Quantum Traps** — trap families & detection power, noise models  
  `docs/research/QUANTUM_TRAPS.md`

### Cryptography & Randomness
- **VDF Security** — assumptions, parameterization, verifier costs  
  `docs/research/VDF_SECURITY.md`
- **PQC Migration** — long-term roadmap & data format evolution  
  `docs/research/PQ_MIGRATION.md`

> See also normative spec chapters:
> - `docs/spec/poies/*` (scoring, acceptance, retarget, fairness, security)  
> - `docs/spec/proofs/*` (HASHSHARE, AI_V1, QUANTUM_V1, STORAGE_V0, VDF_BONUS, ENVELOPE)  
> - `docs/spec/MERKLE_NMT.md`, `docs/spec/DA_ERASURE.md`, `docs/spec/LIGHT_CLIENT.md`  
> - `docs/randomness/*` (beacon, VDF params, API, security)  
> - `docs/economics/*` (fees, rewards, inflation, MEV, bounds)

---

## 2) External References (Selected, Non-Normative)

> Titles/authors/years are provided for orientation; use up-to-date editions where applicable.

### Zero-Knowledge Proofs & Hashes
- **Groth16** — Jens Groth, 2016: “On the Size of Pairing-based Non-interactive Arguments”  
- **PLONK** — Ariel Gabizon, Zachary J. Williamson, Oana Ciobotaru, 2019: “Plonk”  
- **FRI & STARKs** — Eli Ben-Sasson et al., 2018: “Scaling proofs and FRI commitments”  
- **Poseidon Hash** — Lorenzo Grassi et al., 2019: “Poseidon: A New Hash Function for ZK Applications”  
- **KZG Commitments** — Aniket Kate, Gregory M. Zaverucha, Ian Goldberg, 2010

### Pairings & Curves
- **BN254 / Optimal Ate Pairing** — Paulo S. L. M. Barreto, Michael Naehrig, 2006; Scott et al.  
- **py_ecc** — Ethereum Foundation reference implementations (BLS12-381, BN128)

### Data Availability, NMT, Erasure
- **Namespaced Merkle Trees (NMTs)** — Mustafa Al-Bassam et al.: “LazyLedger” / Celestia literature  
- **Erasure Coding** — Reed–Solomon codes; survey literature for parameter selection in DAS  
- **DAS in Modular Blockchains** — community papers on sampling guarantees & light clients

### Verifiable Delay Functions (VDF)
- **Wesolowski VDF** — Benjamin Wesolowski, 2019: “Efficient Verifiable Delay Functions”  
- **Pietrzak VDF** — Krzysztof Pietrzak, 2018: “Simple Verifiable Delay Functions”

### Post-Quantum Cryptography (PQC)
- **Kyber** — KEM (NIST PQC Round 3) — Joppe W. Bos et al.  
- **Dilithium** — Signature (NIST PQC Round 3) — Vadim Lyubashevsky et al.  
- **SPHINCS+** — Stateless hash-based signatures — Bernstein, Hülsing, Kölbl et al.  
- **NIST PQC Draft Standards & KATs** — official specifications and test vectors

### TEEs & Attestation
- **Intel SGX** — ECDSA/DCAP attestation guides; PCK/TCB/QE identity documents  
- **AMD SEV-SNP** — SNP report format, certificate chains, TCB updates  
- **Arm CCA** — Realm Management Monitor (RMM) & CCA token specs  
- **TPM/DICE** — device identity and event logs (optional)

### Randomness Beacons & Mixing
- **Public Randomness Beacons** — NIST beacon papers, drand literature  
- **QRNG** — Provider whitepapers; extraction & mixing techniques (extract-then-xor)  
- **Bias Resistance** — commit-reveal analyses; grinding and liveness trade-offs

### Economics & Incentives
- **Mining Economics** — payout variance, pool centralization, Gini/HHI metrics  
- **MEV** — surveys on ordering, fairness, and mitigations

> **Note:** External references inform rationale. The **source of truth** for protocol behavior remains the spec and tests in this repository.

---

## 3) How to Contribute Research

1. Open an issue with **[Research]** prefix describing the question/hypothesis.  
2. Draft notes under `docs/research/` (new file) and link them here in a PR.  
3. Where results affect behavior, add cross-refs and tests to the relevant module/spec.  
4. Record deltas in `docs/CHANGELOG.md` under **Research**.

**Style:** follow `docs/STYLE_GUIDE.md`. Put equations in LaTeX where helpful. Prefer precise terminology (ψ, Γ, Θ, λ).

---

## 4) Reproducibility & Artifacts

- Methods & environment pinning: `docs/benchmarks/METHODOLOGY.md`, `docs/docs/REPRODUCIBILITY.md` (if present)
- Where applicable, include:
  - Hardware profile, OS, compiler toolchains  
  - Random seeds and dataset hashes  
  - Commit SHA/tag and version of any circuit/toolchain

---

## 5) See Also

- **Specs**: `docs/spec/*`  
- **Security**: `docs/security/*`  
- **Economics**: `docs/economics/*`  
- **Randomness**: `docs/randomness/*`  
- **PoIES**: `docs/spec/poies/*`

---

_Last updated: YYYY-MM-DD_
