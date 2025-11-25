# AICF Security — TEE Attestations, Trap Calibration, Audits

This document defines the security guarantees and operational controls for the **AI Compute Fund (AICF)**. It focuses on:
1) **TEE attestations** for AI runners and Quantum controller software,
2) **Trap** design and calibration (AI & Quantum),
3) **Auditability** (logs, evidence, transparency, incident response).

> Code anchors:  
> - Attestations: `proofs/attestations/tee/{sgx.py,sev_snp.py,cca.py}`, `proofs/quantum_attest/{provider_cert.py,traps.py}`  
> - Policy/Eval: `aicf/registry/verify_attest.py`, `aicf/sla/{metrics.py,evaluator.py,slash_engine.py}`  
> - Randomness/Seeding: `randomness/*`  
> - Proofs/VKs: `zk/registry/*`, `proofs/*`  

---

## 1. Security Goals

- **Authenticity:** Only jobs executed by **approved, attested** binaries/hardware are credited and paid.
- **Integrity:** Results must be **verifiable** (cryptographic proofs or trap-verified behavior).
- **Unforgeability:** Providers cannot claim work they didn’t perform or replay stale outputs.
- **Accountability:** Every job has an **evidence trail** sufficient for external audit and dispute resolution.
- **Resilience:** Rotation, revocation, traps, and sampling detect and contain misbehavior quickly.

---

## 2. Threat Model

**Adversaries:**
- Malicious or compromised providers (insider or remote compromise).
- Colluding providers to game redundancy/traps.
- Network adversary attempting replay/injection.
- Supply chain compromise of runner binaries or TEE firmware.

**Non-goals:**
- Protecting model IP beyond standard attest/sealing.
- Side-channel elimination in untrusted hardware (we mitigate, we do not guarantee).

---

## 3. TEE Attestations

### 3.1 Trusted Roots & Chains
- Roots: `proofs/attestations/vendor_roots/*.pem` (Intel SGX/TDX, AMD SEV-SNP, Arm CCA).
- Verifier checks:
  - Cert chain validity, **revocation** status, QE/QGS identities (SGX/TDX).
  - SNP/CCA report signature and **TCB**/FW version thresholds.
  - Network policy pinning of **acceptable families/versions** (see `aicf/policy/example.yaml`).

### 3.2 Measurement Binding
- We pin the **runner measurement** (MRENCLAVE/MRSIGNER for SGX; measurement hashes for SNP/CCA).
- The attested config binds:
  - Runner version/hash,
  - Feature flags (no debug, no dev keys),
  - **Job domain tag** and **chain context** (chainId, epoch).
- Code: `proofs/attestations/tee/common.py` assembles canonical evidence.  
  Policy mapping: `proofs/policy_adapter.py`.

### 3.3 Freshness & Nonce
- Each job includes a **challenge nonce** `H(chainId | height | task_id)`; the attestation quote references it.
- Replay protection: quotes older than `attest_max_age` rejected; per-lease nonce randomized.
- Deterministic `task_id`: `capabilities/jobs/id.py`.

### 3.4 Rotation & Revocation
- Providers must **re-attest** on:
  - Runner update, firmware/TCB changes, policy bump, or at least every `K` epochs.
- Revocation lists:
  - Distributed with node releases or fetched via out-of-band signed bundles.
- On revocation: new jobs blocked; open leases canceled; **jail** + potential **slash**.

### 3.5 Evidence Artifact
Minimal envelope persisted per job:
```jsonc
{
  "provider_id": "prov:abc",
  "task_id": "0x…",
  "attestation": {
    "scheme": "sgx|snp|cca",
    "report": "<bytes|b64>",
    "certs": ["<pem>","<pem>"],
    "measurement": "0x…",
    "qe_identity": "…",
    "tcb_version": "…",
    "timestamp": 1713202001
  },
  "nonce": "0x…",
  "sig": "0x<provider-sig>"
}

Schema validation lives in proofs/schemas/* and normalizers.

⸻

4. Traps (AI & Quantum) — Design & Calibration

4.1 Principles
	•	Secrecy-before-use: trap seeds kept confidential until epoch finalization.
	•	Audit-after: publish trap seed/selection transcript post-epoch for reproducibility.
	•	Coverage: trap families cover diverse failure modes (memorization, refusal, hallucination).

4.2 AI Traps
	•	Families:
	•	Deterministic prompts with known canonical outputs (format and content).
	•	Semantic equivalence sets (different phrasings → same normalized answer).
	•	Adversarial consistency checks (internal contradictions).
	•	Scoring:
	•	Binary checks (exact hash) where feasible; else string-distance/metric band.
	•	Thresholds set in aicf/policy/* and consumed by aicf/sla/evaluator.py.
	•	Leakage Control:
	•	Trap corpora are rotated; golden answers are salt-hashed until disclosure.
	•	Provider access is limited to generated prompts, not answer keys.

4.3 Quantum Traps
	•	Implemented in proofs/quantum_attest/traps.py with statistical acceptance tests.
	•	Families:
	•	Clifford-only, low-depth T circuits, randomized compiler seeds.
	•	Verification:
	•	Expected output distributions; use goodness-of-fit with α (e.g., 1e-6).
	•	Confidence bounds and multiple-hypothesis correction over a batch.
	•	Anti-Gaming:
	•	Seeds derived from beacon (randomness/beacon/*) per-epoch: seed = HKDF(beacon||task_id).
	•	Provider cannot precompute across unknown seeds.

4.4 Calibration Process
	•	Pre-deploy: backtest traps on clean runs; record FPR/FNR.
	•	On-chain policy: publish target thresholds, sample sizes, and α.
	•	Live tuning: evaluator tracks drift; proposals to tighten thresholds undergo governance review.

⸻

5. Redundancy & Agreement
	•	When k-of-n redundancy is enabled, agreement between providers is a core security signal.
	•	Disagreements feed quality penalties and trigger focused audits.
	•	Tie-breakers favor providers with stronger attestation posture and historical trap performance.

⸻

6. Audits & Transparency

6.1 Evidence Logging
	•	Immutable log entries in aicf/treasury/state.py and aicf/registry/*:
	•	{provider_id, task_id, height, evidence_hash, verdict, payout/slash}.
	•	Logs include hash pointers to artifacts (attest reports, outputs, trap programs).

6.2 External Audits
	•	Quarterly:
	•	Runner binary reproducibility (deterministic build, hash match).
	•	Attestation chain validation sampling.
	•	Trap suite efficacy review (coverage & drift).
	•	Publish transparency reports with anonymized stats:
	•	trap pass rates, latency distributions, slashing events.

6.3 Reproducibility
	•	Attestation + trap selection transcripts reconstructable from:
	•	randomness/beacon rounds,
	•	Registry state at epoch start,
	•	Hash-locked corpora revisions.
	•	See docs/zk/docs/REPRODUCIBILITY.md (and website/public/sitemap.xml placeholder for site build) for general guidelines.

⸻

7. Incident Response
	1.	Detect: evaluator flags Critical (invalid proof / forged attestation / systemic trap fail).
	2.	Contain: auto-jail, cancel leases, block new assignments.
	3.	Investigate: export evidence bundle; notify provider.
	4.	Decide: apply slashing per aicf/sla/slash_engine.py and policy.
	5.	Recover: require re-attestation, hotfix runner, or raise TCB baseline.
	6.	Report: transparency log entry; postmortem within T+7 days.

⸻

8. Key Management
	•	Provider identity keys (sign assignments/receipts) must be hardware-backed where possible.
	•	TEE signing keys come from the vendor attestation stack; we never accept debug keys.
	•	Rotate provider keys at most R epochs; maintain continuity by cross-signing rotation notices.

⸻

9. Supply Chain Controls
	•	Reproducible builds for runners; pin dependencies and container bases.
	•	SBOM published; binary digests pinned in measurement allowlist.
	•	CI/CD uses short-lived tokens; release artifacts must match pinned hashes.

⸻

10. Interactions with Proof Systems
	•	For circuits (e.g., ZKML or policy circuits), pin VKs in zk/registry/vk_cache.json; verify against registry.yaml metadata.
	•	Any change to VKs requires:
	•	Hash verification,
	•	Signed update via zk/registry/update_vk.py,
	•	Staged rollout with compatibility window.

⸻

11. Governance Hooks
	•	Policy updates to:
	•	Accepted TEE families/versions,
	•	Trap thresholds and sample sizes,
	•	Revocation lists,
	•	VK allowlists,
go through a network governance process; nodes reject jobs violating current policy roots.

⸻

12. Checklists

Provider Onboarding
	•	Identity verified; stake bonded.
	•	TEE family supported; attestation verified with runner measurement allowlist.
	•	Heartbeat passes health & SLA grace rules.

Release
	•	Runner measurement updated and pinned.
	•	Trap corpora version bump; beacon-derivation unchanged (or audited change).
	•	VK set unchanged or updated via signed tool.

Audit Pack
	•	Evidence bundle (attest+nonce+transcript).
	•	Job logs (assign/complete), outputs digests.
	•	Evaluator verdict & thresholds snapshot.

⸻

13. References
	•	docs/aicf/OVERVIEW.md, docs/aicf/SLA.md, docs/aicf/JOB_API.md
	•	docs/quantum/PROVIDER_GUIDE.md, docs/quantum/BEACON_MIXING.md
	•	docs/randomness/SECURITY.md
	•	docs/zk/docs/SECURITY.md, zk/registry/*
	•	proofs/attestations/*, proofs/quantum_attest/*

