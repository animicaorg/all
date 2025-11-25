# Proposal Types
**ParamChange · Upgrade · PQRotation · Policy · Treasury**

This document standardizes proposal classes used in Animica governance. Each
class extends the base lifecycle defined in `governance/PROCESS.md`
(Draft → RFC → Vote → Enact → Review) with tailored artifacts, risk
assessments, and default voting thresholds.

> ⚖️ Always pick the **least powerful** class that fits the change. If a
proposal spans multiple classes, split it or justify the stronger class.

---

## 0) Common Schema (All Types)

Every RFC must include a `RFC.md` with YAML front-matter:

```yaml
id: RFC-YYYY-ShortSlug
title: "Concise descriptive title"
author: ["handle1", "handle2"]
sponsors: ["working-group-or-team"]
class: "<OneOf: ParamChange|Upgrade|PQRotation|Policy|Treasury>"
created: "YYYY-MM-DD"
status: "RFC"
effective_date: "YYYY-MM-DD"     # or null
sunset_date: null                # or date, if temporary
security_review: "required|n/a"
coi_disclosures:
  - "name: disclosure text"
references:
  - "link to code/spec PR or doc"

All proposals must also ship an IMPACT.md covering:
	•	Security & threat model deltas
	•	Ops/rollout, reversibility/rollback
	•	User/dev impact (wallets, SDKs, exchanges)
	•	Metrics & success criteria
	•	Test plan and fixtures (where applicable)

⸻

1) ParamChange

Scope: Reconfigure chain parameters without changing state formats or
consensus rules in a way that requires a hard fork.

Examples
	•	Θ retarget constants; Γ caps; PoIES diversity weights
	•	Fee market floors/surge multipliers
	•	Mempool limits; P2P rate limits; randomness window durations

Artifacts
	•	params/DIFFS.md (before/after table)
	•	Spec references (e.g., docs/spec/DIFFICULTY_RETARGET.md)
	•	Test vectors or simulations if probabilistic behavior changes

Default Governance
	•	Class: Process/Policy or Params(Consensus) depending on impact
	•	Quorum: ≥ 15% voting power
	•	Threshold: Simple majority
	•	Review: 14 days post-enact

Template Snippet

class: "ParamChange"
parameters:
  - path: "consensus.difficulty.ema_alpha"
    before: 0.125
    after:  0.100
  - path: "poies.caps.total_gamma"
    before: 1.00
    after:  1.10
risk_level: "medium"
rollback_plan: "flip feature flag; revert config; no data migration"


⸻

2) Upgrade

Scope: Protocol or network upgrades that alter consensus behavior,
object formats, or state transition logic. May be soft fork (tightening
rules) or hard fork (format/semantic change).

Examples
	•	Header/tx/receipt schema change
	•	Enabling a new proof kind or syscall that affects consensus
	•	VM instruction semantics change

Artifacts
	•	UPGRADE.md (activation height/epoch, feature flags)
	•	Spec diffs: docs/spec/* (format and semantics)
	•	Migration plan and back-compat notes
	•	Test vectors; devnet rehearsal results

Default Governance
	•	Soft fork: quorum ≥ 20%, threshold ≥ 60%, notice ≥ 14 days
	•	Hard fork: quorum ≥ 25%, threshold ≥ 66.7%, notice ≥ 21 days
	•	Emergency path: see SECURITY_COUNCIL.md (≤72h activation + 14d ratification)

Template Snippet

class: "Upgrade"
fork_kind: "soft|hard"
activation:
  strategy: "height|epoch|time"
  value: 123456
feature_flags:
  - "vm.strict_mode_v2"
compatibility:
  min_node_version: ">=0.9.0"
  min_wallet_version: ">=0.8.3"
rollback_plan: "deactivate flag if activation guard not crossed"


⸻

3) PQRotation

Scope: Post-quantum cryptography rotations: algorithm policy updates,
deprecations, key/address rules, and alg-policy Merkle root changes.

Examples
	•	Rotate Dilithium3→include Falcon as optional (example), adjust weights
	•	Deprecate SPHINCS+ variant on timeline; introduce new KEM
	•	Address rules or bech32m HRP additions related to PQ keys

Artifacts
	•	pq/alg_policy/*.json updated; computed alg-policy root
	•	docs/pq/POLICY.md diffs; wallet & SDK compatibility matrix
	•	Migration schedule, deprecation windows, cross-signing plan

Default Governance
	•	Quorum: ≥ 20%
	•	Threshold: ≥ 60%
	•	Notice: staged: announce → testnet → mainnet (e.g., 30d/14d/14d)
	•	Review: 30 days after final stage

Template Snippet

class: "PQRotation"
alg_policy:
  enable:
    - "dilithium3@1"
  deprecate:
    - id: "sphincs-shake-128s@1"
      sunset: "2026-03-01"
alg_policy_root:
  old: "sha3-512:abc…"
  new: "sha3-512:def…"
wallet_impact: "keygen default changes; import remains compatible"


⸻

4) Policy

Scope: Non-consensus policies that affect node operation, interfaces, or
community processes.

Examples
	•	RPC rate limits; CORS origins; API deprecations
	•	Mempool admission policy (non-consensus portions)
	•	Governance/editorial process tweaks; disclosure rules

Artifacts
	•	POLICY.md with rationale & measurable targets
	•	Config diffs; effect on operators & client apps
	•	Rollout & revert instructions

Default Governance
	•	Quorum: ≥ 15%
	•	Threshold: Simple majority
	•	Notice: ≥ 7 days

Template Snippet

class: "Policy"
area: "RPC|Mempool|P2P|Docs|Governance"
change:
  description: "Increase default WS burst tokens per IP"
  before: {"ws_tokens": 200}
  after:  {"ws_tokens": 400}
monitoring: ["metrics: rpc_ws_rejects_total", "SLO: p95 connect < 300ms"]


⸻

5) Treasury

Scope: Changes to mint schedule slices, AICF funding caps, reward splits,
grants programs, faucets, or operational budgets tied to on-chain transfers
or accounting modules.

Examples
	•	Adjust AICF epoch cap Γ_fund and reward split
	•	Launch grants wave with defined milestones
	•	Rebalance miner/treasury split or burn schedule

Artifacts
	•	economics/… specs updated; payout math and examples
	•	Budget spreadsheet or CSV; on-chain addresses; custody/accountability notes
	•	Milestones, KPIs, audit/reporting cadence

Default Governance
	•	Quorum: ≥ 20%
	•	Threshold: ≥ 60%
	•	Notice: ≥ 14 days
	•	Controls: multi-sig or time-lock requirements documented

Template Snippet

class: "Treasury"
program: "AICF Epoch Cap Adjustment"
changes:
  gamma_fund_cap:
    before: 0.15
    after:  0.18
split:
  provider: 0.70
  miner:    0.20
  treasury: 0.10
controls:
  payout_multisig: "anim1… (3/5)"
  timelock_days: 7
reporting:
  kpis: ["jobs_completed", "sla_pass_rate", "proofs_attested"]
  cadence: "monthly"


⸻

6) Decision Matrix (Summary)

Type	Typical Impact	Quorum	Threshold	Notice	Review
ParamChange	Config & parameters (no format change)	15%	50%+	≥7d	14d
Policy	Non-consensus operational policy	15%	50%+	≥7d	14d
PQRotation	PQ alg-policy, keys, deprecations	20%	60%+	staged	30d
Upgrade (SF)	Consensus tightening (soft fork)	20%	60%+	≥14d	30d
Upgrade (HF)	Consensus/format change (hard fork)	25%	66.7%+	≥21d	30d
Treasury	Funding/splits/payments governance	20%	60%+	≥14d	30d

Editors may escalate a proposal to a stricter class if risk is understated.

⸻

7) Cross-Repo Artifact Checklist
	•	Specs: docs/spec/*, docs/economics/*, docs/pq/*
	•	Code: gated by feature flags; activation logic guarded
	•	Vectors: new/updated test vectors; CI must pass
	•	SDKs: compatibility notes (TS/Py/Rust)
	•	Wallets/Explorer: required min versions and store submissions
	•	Ops: runbooks, dashboards/alerts updates
	•	Comms: blog post, forum, site updates

⸻

8) Risk & Rollback Patterns
	•	ParamChange/Policy: config flip or flag; revert without data migration
	•	PQRotation: dual-stack acceptance window; wallet migration flows; cross-signing
	•	Upgrade: activation guards; shadow mode on testnet; emergency off-switch if feasible
	•	Treasury: time-locked payouts; phased disbursement; milestone-gated releases

⸻

9) Examples Index
	•	proposals/2025-enable-theta-clamp/ (ParamChange)
	•	proposals/2026-hashshare-v2/ (Upgrade—soft)
	•	proposals/2026-header-cddl-v3/ (Upgrade—hard)
	•	proposals/2025-pq-rotation-q1/ (PQRotation)
	•	proposals/2025-aicf-epoch-gamma/ (Treasury)

⸻

Edits to this document require a Policy-class proposal.
