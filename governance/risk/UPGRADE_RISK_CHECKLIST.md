# UPGRADE RISK CHECKLIST
_Pre-/Post-deployment gates for Animica upgrades (VM, DA, PQ, Params, Contracts)_

**Version:** 1.0  
**Status:** Active  
**Applies to:** Any proposal that modifies executable code paths, consensus/VM behavior, PQ suites, DA parameters, or system contracts.  
**See also:** `governance/diagrams/UPGRADE_FLOW.mmd`, `governance/policies/TRANSPARENCY.md`, `governance/risk/PARAMS_BOUNDARIES.md`, `governance/policies/SLASHING_AND_RECOURSE.md`.

---

## 0) Summary & Ownership

| Role | Responsibilities |
|---|---|
| Proposal Owner | Completes checklist, coordinates reviews, publishes artifacts |
| Stewards (Gov Multisig) | Gatekeepers for risk sign-off, vote scheduling, activation approval |
| Domain Reviewers | VM/Consensus, DA, PQ/Crypto, Networking/Mempool, Wallet/DevEx |
| Release Captain | Drives runbook during activation window; owns go/no-go |
| Observability Lead | Dashboards, alerts, and guardrail SLOs |

---

## 1) Pre-Merge Gates (Proposal & Code Ready)

**Artifacts & Hygiene**
- [ ] Proposal includes YAML header & passes schema (`governance/schemas/*`).  
- [ ] Bounds/delta checks pass (`params_bounds.json` + `params_current.json`).  
- [ ] Linked ADR(s) updated for major behavior changes.  
- [ ] Security/Risk sections completed with explicit **failure modes** and **mitigations**.

**Testing & CI**
- [ ] Unit & integration tests added/updated; CI green.  
- [ ] Determinism checks for VM code paths (same inputs → same outputs).  
- [ ] Fuzz/simulation (where relevant) with seeds and reproducible logs.  
- [ ] Backwards-compatibility tests for storage/ABI (or migration plan supplied).  
- [ ] Cross-version network test (old↔new nodes) demonstrates safe coexistence until activation.

**Cryptography / PQ (if applicable)**
- [ ] Algorithms limited to allowed set (`PQ_POLICY.md`).  
- [ ] KATs/vectors attached; third-party references cited.  
- [ ] Key/attestation format stability verified; wallet SDKs updated.

**Data Availability (if applicable)**
- [ ] Blob size/RS parameters within bounds; bandwidth model documented.  
- [ ] Light-client path unaffected or migration guide provided.

**Networking & Mempool**
- [ ] Gossip sizes/limits within bounds; RBF policy interactions reviewed.  
- [ ] Reorg/latency sensitivity documented.

**Ops & Rollback**
- [ ] Signed release artifacts planned (checksums/signatures).  
- [ ] Rollback plan (exact commits/flags) and **abort switch** defined.  
- [ ] Canary cohort criteria + exit metrics declared.

**Comms & Docs**
- [ ] Release notes draft with user impact and timelines.  
- [ ] Explorer/wallet/SDK compatibility matrix updated.  
- [ ] Risk label assigned (Low/Medium/High) with rationale.

_Run locally:_
```bash
python governance/scripts/validate_proposal.py path/to/proposal.md --strict --pretty
python governance/scripts/check_registry.py --strict --pretty
pytest -q governance/tests
2) Pre-Activation Go/No-Go (T-0)
Pre-flight

 Release tags & images built; checksums match repo chains/checksums.txt.

 Canary infra deployed on testnet/localnet; final smoke tests pass.

 Monitoring dashboards pinned (Γ, block times, uncle/orphans, mempool, DA prove times, beacon equivocations).

 Status page ready; incident comms template prepared.

Governance

 Vote closed, tally published, deposit refunds processed.

 Timelock satisfied; activation height/date pinned & announced.

Stakeholder Acknowledgments

 Node ops, exchanges, explorers, wallets ACK readiness.

 Providers (AICF/Quantum/DA/Beacon) ACK scheduling & telemetry format.

Go/No-Go Call

 All owners present in live bridge; clear decision & timestamp recorded.

 Abort criteria repeated verbally and in runbook.

3) Activation Runbook (Canary → Full)
Stage 0 — Arm Abort

 Feature flags compiled; abort tx prepared & signed but not broadcast.

 Emergency contacts online (pager/bridge).

Stage 1 — Canary (X% traffic / subset)

 Enable on small validator/miner cohort or at low TPS window.

 Observe for N blocks or T minutes (declare both).

 Guardrails SLOs met?

Block interval within ±Y% of baseline

Orphan rate ≤ baseline + Δ

Mempool admitted/rejects normal

DA prove time p95 ≤ target

Beacon/Provider equivocations = 0

 Decision: proceed / hold / abort.

Stage 2 — Ramp

 Expand cohort to Y%; repeat metrics & health checks.

 Verify cross-version peer stability and catch-up.

Stage 3 — Full Enable

 Flip network-wide flag at height H (or wall time).

 Keep abort armed for gov.activate.abort_window_blocks.

 Publish status update.

4) Abort & Rollback Playbook
Abort Triggers (examples)

 p95 block interval > 2× baseline for M consecutive windows.

 Orphan rate > threshold; safety invariant violated.

 Determinism fault detected (same input → divergent output).

 Beacon/provider equivocation / DA failure rate above X%.

Actions

 Broadcast pre-signed abort tx; disable feature flag.

 Announce publicly within 15 minutes; switch to incident mode (Sev classification).

 Open dossier with evidence (logs, txids, diffs).

 Convene stewards for emergency review; plan fix or revert.

5) Post-Activation Verification (T+7d default)
Technical

 Metrics steady; no slow drifts (TPS, latency, Γ, DA p95).

 No increase in crash/oom reports; memory footprint within budget.

 Light clients / wallets confirm expected paths.

Ecosystem

 Major infra (exchanges, explorers, dapps) report “all green.”

 Provider SLAs met; no new strikes.

Documentation

 Final release notes published; changelogs updated across repos.

 Parameter snapshots (params_current.json) updated; checksum bumped.

 Diagrams/ADRs revised if behavior materially changed.

Governance Close-Out

 Tally + activation report linked in Transparency minutes.

 Any waivers/exceptions documented with sunset date.

6) Postmortem (If Incident Occurred)
 Timeline with UTC times & heights.

 Root cause analysis (5-whys or similar).

 Blast radius & user impact quantified.

 Corrective actions with owners & deadlines.

 Links: commits, dashboards, txids, PRs.

 Follow-up proposal (if policy/params need change).

7) Guardrails & Thresholds (Defaults)
Metric	Guardrail (example)	Source
Block interval p95	≤ +25% vs 14-day baseline	Explorer/Prom
Orphan/uncle rate	≤ baseline + 0.5%	Explorer
Γ (useful-work share)	Within target band	PoIES dashboard
Mempool rejects	No spike > 2× baseline	Node metrics
DA prove time p95	≤ target (e.g., 2s)	DA metrics
Beacon equivocations	0	Beacon telemetry

Tune via params_current.json within bounds from PARAMS_BOUNDARIES.md.

8) Templates & Snippets
Go/No-Go Log (example)

markdown
Copy code
Activation: GOV-YYYY-MM-XXX — Height H / Date YYYY-MM-DD HH:MM UTC
Go/No-Go: GO
Canary Window: N blocks (H..H+N)
Abort Switch: tx 0x… (armed), window: X blocks
Owners Present: <list>
Decision Time: HH:MM UTC
Links: dashboards, PR, release notes
Local Commands

bash
Copy code
# Validate proposal & registries
python governance/scripts/validate_proposal.py path/to/proposal.md --strict --pretty
python governance/scripts/check_registry.py --strict --pretty

# Run tests
pytest -q governance/tests
9) Change Log
1.0 (2025-10-31): Initial checklist with staged rollout, abort/rollback, metrics guardrails, and governance close-out.

