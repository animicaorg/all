# Rewards — Leader / Committee / Rain / AICF Payouts (v1)

This document explains how newly issued ANM and protocol-side payouts are distributed each block and each epoch across **block leaders**, an optional **committee**, a fairness **rain** mechanism, and **AICF** (AI Compute Fund) actors. The goal is to align incentives with **PoIES** (Proofs of Incentivized and Extensible Work): leaders are rewarded for timely blocks, useful work contributors are rewarded over time, and compute providers are paid trust-minimized via on-chain proofs.

> **Authoritative logic** lives in `consensus/`, `execution/`, and `aicf/`. This page is an overview of parameters and formulas.

**Related specs & code**
- PoIES scoring & receipts: `consensus/scorer.py`, `consensus/share_receipts.py`, `docs/spec/poies/SCORING.md`
- Difficulty/Θ: `consensus/difficulty.py`, `docs/spec/poies/RETARGET.md`
- Fees & burns: `docs/economics/FEES.md`, `execution/runtime/fees.py`
- AICF settlement: `aicf/economics/*`, `aicf/integration/execution_hooks.py`, `docs/quantum/PROOFS.md`
- Randomness/committee selection: `randomness/beacon/*`, `docs/randomness/OVERVIEW.md`

---

## 1) Buckets & High-Level Flow

At block **n**, total economic flows are:

1) **Fees** from transactions  
   - **Burn**: base-fee portions (execution & DA) per `FEES.md`.  
   - **Leader tips**: sum of `priorityFeePerGas * gasUsed(tx)` for all txs in the block.

2) **Base issuance** (protocol mint)  
   - Per-block amount `I_n` (declining or epoch-stepped schedule).  
   - Split into **Leader**, **Committee**, and **Rain** buckets by policy weights.

3) **AICF payouts** (proof-of-compute settlements)  
   - Driven by included **AI/Quantum** proof claims.  
   - Paid from AICF treasuries (funded by policy mint / fees), then split: **Provider / Leader / Treasury**.

### 1.1 Parameters (illustrative defaults)
Defined in `spec/params.yaml` and `aicf/policy/example.yaml`:

```text
issuance_per_block: I_n                      # units: ANM
issuance_split:
  leader:    0.50                            # 50% of I_n
  committee: 0.10                            # 10%
  rain:      0.40                            # 40%

aicf_split:
  provider:  0.85                            # provider earns 85% of job price
  leader:    0.10                            # leader inclusion share
  treasury:  0.05                            # protocol reserve

committee:
  enabled: true
  epoch_blocks: 900                          # ~ epoch length
  size: 64                                   # members selected per epoch via beacon
  stipend_per_block: S_c                     # fixed ANM per block per committee (from I_n split)
  performance_weighting: true                # weight by liveness/latency if enabled

rain:
  window_blocks: 7200                        # distribution window (≈ 1 day)
  weighting: "psi"                           # weight by ψ(p) contributions (capped)
  min_payout: 1e-9 ANM                       # dust floor
  cap_per_id: 2x avg                         # anti-whale cap for rain


⸻

2) Leader Rewards (Per Block)

Leader (the block producer at height n) receives:

leader_reward(n) = tips(n)
                 + I_n * split.leader
                 + aicf_leader_share(n)

	•	tips(n): sum of tx tips in block n.
	•	I_n * split.leader: issuance share.
	•	aicf_leader_share(n): sum over completed AICF claims included in block n of the leader slice (see §5).

Accounting is performed in execution/runtime/fees.py and aicf/integration/execution_hooks.py.

Notes
	•	Tips fluctuate with mempool congestion.
	•	If issuance_per_block declines over time, guaranteed leader rewards decline while tips remain market-driven.

⸻

3) Committee Rewards (Per Block, Optional)

To improve liveness attestations and provide light-client anchoring, an ephemeral committee may be selected each epoch via the randomness beacon (see randomness/beacon/*).
	•	Committee size: C = committee.size
	•	Committee stipend pool per block: I_n * split.committee
	•	If performance_weighting is disabled, the pool splits evenly across active members:

committee_member_reward = (I_n * split.committee) / active_members


	•	If enabled, weight shares by normalized uptime / latency during the epoch. The normalization and caps are implemented in aicf/economics/epochs.py (shared helpers) or an equivalent committee module if separate.

Selection
	•	Deterministic; seed from recent beacon output.
	•	Rotation each epoch (committee.epoch_blocks).
	•	Members publish small attestations (headers/DA/vrf) which are aggregated; absence reduces weight.

If committee.enabled = false the entire committee slice is rolled into rain for the epoch.

⸻

4) Rain Rewards (Fairness Distribution)

Rain distributes a fraction of issuance to recent, non-leader contributors of useful work under PoIES, mitigating variance and skew.

Window
	•	Sliding window W = rain.window_blocks. We aggregate share receipts across blocks n-W+1 … n.

Eligible receipts
	•	From consensus/share_receipts.py: each included proof contributes a receipt with:
	•	Identity (payee) — a derived address or miner id,
	•	Weight w_i — by default w_i = ψ_i (capped by policy and Γ caps),
	•	Nullifier — prevents double-claim across windows.

Pool & Distribution
	•	Pool per block: I_n * split.rain.
	•	Let R = {receipts in window}; define W_total = Σ w_i.
Each eligible payee i receives:

rain_i(n) = (I_n * split.rain) * (w_i / W_total)


	•	Apply caps:
	•	Per-identity cap: rain_i(n) ≤ cap_per_id * (I_n * split.rain) / |unique_payees|
	•	Dust floor: payouts below min_payout are accrued until ≥ floor.

Rationale
	•	Rewards long-tail miners contributing non-leading proofs (hash shares, AI/Quantum, storage, VDF) and smooth revenue between leads.
	•	Ties distribution directly to useful work via ψ; caps preserve diversity (see docs/spec/poies/FAIRNESS.md).

Implementation hooks:
	•	Aggregation in consensus/share_receipts.py.
	•	Payout accounting in execution/runtime/fees.py (rain ledger) or via dedicated treasury module.

⸻

5) AICF Payouts (When Proofs Settle)

When an AI/Quantum job produces a valid proof and the claim is included in block n, a priced payout occurs (see aicf/economics/pricing.py):
	1.	Price the job: P = price(units, schedule) (units derived from proof metrics).
	2.	Split the payout by policy:

P_provider = P * aicf_split.provider
P_leader   = P * aicf_split.leader
P_treasury = P * aicf_split.treasury


	3.	Credit balances:
	•	Provider’s AICF balance increases by P_provider.
	•	Current block leader gets P_leader (booked in the same block as the claim).
	•	Treasury accrues P_treasury.
	4.	Settlement
	•	AICF balances are realized as on-chain transfers via scheduled epoch settlements (batched) or direct credit if configured.
	•	Slashing (SLA failures) reduces provider balances per aicf/sla/slash_engine.py.

Example
	•	P = 10.0 ANM; split (0.85/0.10/0.05) → Provider 8.5, Leader 1.0, Treasury 0.5 ANM.

⸻

6) Supply, Burns & Net Issuance
	•	Gross issuance per block: I_n.
	•	Burns: from base fees (execution + DA). See FEES.md.
	•	AICF payouts do not mint; they move funds within AICF treasuries (which are funded by policy mint or fee allocations).
	•	Net issuance:

net_issuance(n) = I_n - burns(n)



Policy may target flat, deflationary, or modestly inflationary regimes by tuning I_n and fee controllers.

⸻

7) State & Accounting
	•	On apply-block
	•	Compute leader reward components and credit coinbase (execution/state/apply_balance.py).
	•	Accrue committee/rain in their respective ledgers; credit when due.
	•	Process AICF claims in aicf/integration/execution_hooks.py (maps proofs → payouts).
	•	Receipts
	•	Transaction & block receipts reflect miner payouts via logs/topics where applicable; AICF events are emitted for explorer indexing.
	•	Introspection
	•	RPC endpoints (read-only) surface last epoch splits, rain distribution, committee membership, AICF balances.

⸻

8) Edge Cases & Safeguards
	•	Leader empty blocks: still receive I_n * split.leader (unless protocol specifies minimum tx requirement; default: no).
	•	Committee inactivity: inactive members earn zero; pool rolls to active subset or into rain if none.
	•	Rain manipulation:
	•	ψ caps & Γ caps prevent artificial splitting.
	•	Nullifiers prevent double-claiming.
	•	Per-identity caps reduce whale capture.
	•	AICF misbehavior: failing SLAs triggers slashing; claims without valid proofs are rejected.
	•	Deficits: If AICF treasury underfunded for scheduled settlements, payouts queue until replenished (back-pressure).

⸻

9) Worked Snapshot (Illustrative Numbers)

Assume:
	•	I_n = 6.0 ANM
	•	split = { leader:0.5, committee:0.1, rain:0.4 }
	•	tips(n) = 0.9 ANM
	•	burns(n) = 4.2 ANM (base-fee burns)
	•	Committee active members: 60/64; equal weighting
	•	Rain window has W_total = 12,000 ψ-units and miner X has w_X=120.

Block n:
	•	Leader issuance: 3.0 ANM
	•	Committee pool: 0.6 ANM → per active member: 0.6 / 60 = 0.01 ANM
	•	Rain pool: 2.4 ANM → miner X share: 2.4 * (120 / 12000) = 0.024 ANM
	•	AICF claims: 1 job; P=10 ANM → Leader 1.0 ANM, Provider 8.5, Treasury 0.5.

Leader total: tips 0.9 + issuance 3.0 + AICF leader 1.0 = 4.9 ANM
Net issuance: 6.0 - 4.2 = 1.8 ANM (deflation/neutral depends on regime)

⸻

10) Parameterization & Upgrades
	•	Tuned via governance / upgrades (docs/spec/UPGRADES.md):
	•	issuance_per_block schedule,
	•	splits (leader/committee/rain),
	•	committee size & stipend, selection cadence,
	•	rain window & caps,
	•	AICF splits & SLA policies.

Changes activate at epoch boundaries to avoid mid-epoch discontinuities.

⸻

11) Implementation Hooks (Where to Look)
	•	consensus/share_receipts.py — aggregation & deterministic receipts Merkle.
	•	execution/runtime/fees.py — coinbase crediting and pool accruals.
	•	aicf/integration/execution_hooks.py — proof claims → payouts.
	•	aicf/economics/settlement.py — epoch settlement batching.
	•	randomness/beacon/* — beacon/selection for committees.
	•	rpc/methods/* — read-only surfaces for explorers/ops.

⸻

12) Summary
	•	Leader: tips + issuance share + AICF leader slice.
	•	Committee: small, stable issuance-backed stipend for liveness anchoring (optional).
	•	Rain: fairness mechanism that shares issuance with useful work contributors over a rolling window weighted by ψ.
	•	AICF: providers paid on proof; leader and treasury receive small splits; slashing enforces QoS.

Version: v1.0 — subject to policy updates via the upgrades process.
