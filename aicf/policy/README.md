# AICF Policy: Tuning SLA, Slashing, and Economics per Network

This document explains how to configure and tune the **AICF** (AI/Compute Fund) for a specific network (devnet, testnet, mainnet). Policy is loaded at node start and used by:

- **SLA evaluation** (`aicf/sla/evaluator.py`)
- **Slashing engine** (`aicf/sla/slash_engine.py`)
- **Pricing & splits** (`aicf/economics/pricing.py`, `aicf/economics/split.py`)
- **Epoching & settlement** (`aicf/economics/epochs.py`, `aicf/economics/settlement.py`)
- **Registry & quotas** (`aicf/registry/*.py`, `aicf/queue/quotas.py`)

> **Determinism & activation**  
> Policy affects payouts and penalties; to avoid consensus divergence, **activate changes at an epoch boundary** (using `activation_height`/`activation_epoch`). The active policy hash can be surfaced in headers via the chain’s policy-root mechanism (optional but recommended).

---

## 1) Where policy lives

- Default search path: `aicf/policy/{network}.yaml` (e.g. `devnet.yaml`, `testnet.yaml`, `mainnet.yaml`)
- Override with env var: `AICF_POLICY_PATH=/path/to/policy.yaml`
- On startup we log:
  - Parsed policy version
  - Policy hash (SHA3-256 over canonicalized YAML)
  - Planned activation (height/epoch)

Keep policy files under version control and code-review them like you would protocol upgrades.

---

## 2) Policy Surface (what you can tune)

1. **SLA thresholds & windows**
   - trap acceptance ratio, QoS score floors, p95 latency caps
   - rolling windows for decision confidence (min samples)
2. **Slashing rules**
   - reason codes, penalty magnitudes, jail / cooldown timers
   - clawback schedules and grace windows
3. **Economics**
   - unit pricing schedules (AI, Quantum)
   - reward splits (provider / treasury / miner)
   - fee floors/ceilings and indexation hooks
4. **Epoching & caps**
   - epoch length (in blocks), Γ_fund cap per epoch
   - rollover rules and settlement batch sizes
5. **Quotas & registry gates**
   - min stake per capability, concurrent lease caps
   - allow/deny lists, regional filters (optional)
6. **Assignment shaping**
   - randomness seed source, fairness shuffles, priority weights

---

## 3) Reference YAML

Below is a compact, commented example. Use it as a template and adjust per network.

```yaml
version: 1
name: testnet-1
activation:
  # Activate at start of this epoch (preferred) or at this height.
  activation_epoch: 120           # mutually exclusive with activation_height
  # activation_height: 480_000

epochs:
  length_blocks: 1000             # ~ epoch size
  fund_cap_atomic: 50_000_000     # Γ_fund: max total payouts per epoch (atomic units)
  settle_batch_size: 500          # payouts per settlement batch
  rollover_unspent: true          # carry unused cap forward (bounded by 2×cap)

economics:
  pricing:
    ai:
      unit: "ai_unit"             # abstract compute unit
      base_per_unit: 25_000       # atomic units / ai_unit
      piecewise:
        - upto: 10_000            # tiered price for small jobs
          per_unit: 30_000
        - upto: 100_000
          per_unit: 22_500
        - upto: null              # infinity
          per_unit: 17_500
      surge_multiplier_max: 2.0   # under queue pressure; bounded for predictability
      surge_threshold_queue_len: 500
    quantum:
      unit: "q_unit"              # standardized qubit-depth×shots
      base_per_unit: 120_000
      piecewise:
        - upto: 2_000
          per_unit: 140_000
        - upto: null
          per_unit: 100_000
  splits:
    # Sum must equal 1.0; engine enforces invariants.
    provider: 0.82
    treasury: 0.12
    miner: 0.06
  fee_floor_atomic: 2_000         # minimum requester co-pay (if applicable)
  indexation:
    # Optional CPI/FX hooks. If enabled, only apply clamped EMA in [0.9, 1.1].
    enabled: false
    clamp_min: 0.9
    clamp_max: 1.1

sla:
  windows:
    samples_min: 50               # min samples before strong decisions
    lookback_jobs: 500            # rolling window for provider scoring
  thresholds:
    traps_ratio_min: 0.02         # fraction of trap-cases embedded in workloads
    traps_pass_min: 0.98          # required pass rate on traps
    qos_score_min: 0.85           # normalized quality score [0,1]
    latency_ms_p95_max: 2500
    availability_min: 0.995       # heartbeat based
  confidence:
    # Use Wilson/Clopper-Pearson lower bounds for pass/fail; require the bound ≥ threshold.
    method: "wilson"
    z_value: 2.0                  # ~95% confidence
  decay:
    health_half_life_blocks: 5000 # decay old evidence to be forgiving but firm

slashing:
  grace:
    startup_blocks: 200           # ignore severe penalties while a provider warms up
    missed_heartbeat_soft: 3      # soft warnings before penalties
  penalties:
    # reason_code: { penalty_atomic, jail_blocks, cooldown_blocks, notes }
    TRAPS_CHEAT:
      penalty_atomic: 2_000_000
      jail_blocks: 20_000
      cooldown_blocks: 0
      notes: "Trap mismatch or tampering"
    QOS_FAIL:
      penalty_atomic: 500_000
      jail_blocks: 0
      cooldown_blocks: 10_000
      notes: "Quality below floor with confidence"
    LATENCY_SLA:
      penalty_atomic: 250_000
      jail_blocks: 0
      cooldown_blocks: 5_000
      notes: "p95 latency > cap"
    LEASE_LOSS:
      penalty_atomic: 150_000
      jail_blocks: 0
      cooldown_blocks: 2_000
      notes: "Lease expired without proof/return"
    HEARTBEAT_MISS:
      penalty_atomic: 50_000
      jail_blocks: 0
      cooldown_blocks: 1_000
      notes: "Repeated heartbeat gaps"
  clawback:
    enabled: true
    schedule_blocks: [ 0, 10_000, 30_000 ]  # staged clawback for large penalties
    fractions:       [ 0.25, 0.50, 0.25 ]
  bounds:
    max_penalty_per_epoch: 5_000_000        # cap per provider
    never_exceed_stake: true

registry:
  stake_min:
    ai: 5_000_000
    quantum: 12_000_000
  allowlist:
    enabled: false
    providers: []
  denylist:
    enabled: false
    providers: []
  regions:
    require_any_of: []            # e.g., ["us-east", "eu-west"]

quotas:
  leases_max_concurrent:
    ai: 4
    quantum: 2
  units_per_epoch_max:
    ai: 200_000
    quantum: 20_000

assignment:
  randomness: "beacon"            # "beacon" or "local"
  fairness_shuffle_rounds: 2
  weights:
    priority: 3.0
    fee: 2.0
    age: 1.0


⸻

4) Tuning Guidance

Devnet
	•	Goal: rapid iteration, low friction.
	•	Suggested: low stakes, relaxed SLA floors, small penalties, short epochs (100–250 blocks), high Γ_fund to exercise code paths.

Testnet
	•	Goal: realistic stress and economics rehearsal.
	•	Tighten SLA thresholds gradually; enable confidence bounds; moderate penalties; medium epochs (1k–2k blocks); set Γ_fund to ~1–3× expected weekly spend; enable surge pricing to study queue dynamics.

Mainnet
	•	Goal: safety-first, predictable economics.
	•	Conservative pricing (avoid extremes); robust splits (treasury ≥ 10%); strict trap/QoS floors with confidence gates; enable clawback and never_exceed_stake; longer epochs (e.g., 4k–10k blocks) to smooth variance; set Γ_fund from treasury forecasts and usage targets.

⸻

5) Safety Invariants (enforced by code)
	•	provider + treasury + miner == 1.0 (within epsilon)
	•	payout_epoch_sum ≤ Γ_fund (cap respected; overflow is deferred/rolled)
	•	penalty ≤ current_stake when never_exceed_stake = true
	•	Activation must be future epoch relative to the current head
	•	Piecewise pricing segments must be strictly increasing upto values

⸻

6) Rollout Checklist
	1.	Draft policy changes + rationale (PR).
	2.	Run unit tests & scenario sims:
	•	queue pressure, surge, settlement limits
	•	SLA fail cases → penalties → balances
	3.	Dry-run on a forked DB snapshot (shadow mode).
	4.	Pick an activation epoch (T + 1 epoch minimum).
	5.	Publish policy + computed policy-hash; announce to operators.
	6.	Observe metrics for 2–3 epochs; be ready with a hotfix plan.

⸻

7) Observability (what to watch)

From aicf/metrics.py and SLA/economics modules:
	•	aicf_enqueue_total, aicf_assign_total, aicf_proof_accept_total, aicf_payout_total
	•	Queue depth, surge multiplier, avg wait
	•	SLA pass rates (trap/QoS/latency), confidence windows
	•	Slashes by reason; stake runway per provider
	•	Epoch fund utilization (payouts/Γ_fund), rollover amounts
	•	Settlement latency and batch runtimes

⸻

8) FAQ

Q: Prices seem too low or too high under load.
A: Adjust surge_threshold_queue_len and surge_multiplier_max; revisit base tiers.

Q: Providers get jailed too often in bursts.
A: Increase sla.windows.lookback_jobs, add decay (longer half-life), and prefer cooldown over jail for non-adversarial issues.

Q: Γ_fund caps out mid-epoch.
A: Increase fund_cap_atomic, enable rollover_unspent, or lengthen the epoch.

Q: We need a controlled onboarding.
A: Enable registry.allowlist and set minimum stakes per capability.

⸻

9) File Hygiene & Versioning
	•	Bump version: when changing structure (not just values).
	•	Keep commented history in VCS – never edit on live nodes.
	•	Record the policy-hash used for each epoch in release notes.

⸻

10) Minimal Template (copy me)

version: 1
name: <network-name>
activation: { activation_epoch: <N> }
epochs: { length_blocks: 1000, fund_cap_atomic: 0, settle_batch_size: 500, rollover_unspent: true }
economics:
  pricing: { ai: { base_per_unit: 0 }, quantum: { base_per_unit: 0 } }
  splits: { provider: 0.85, treasury: 0.10, miner: 0.05 }
sla:
  windows: { samples_min: 50, lookback_jobs: 500 }
  thresholds: { traps_pass_min: 0.98, qos_score_min: 0.85, latency_ms_p95_max: 2500, availability_min: 0.995 }
slashing:
  penalties: { QOS_FAIL: { penalty_atomic: 0, jail_blocks: 0, cooldown_blocks: 0 } }
registry:
  stake_min: { ai: 0, quantum: 0 }
quotas:
  leases_max_concurrent: { ai: 2, quantum: 1 }
assignment:
  randomness: "beacon"


⸻

Tip: Keep devnet/testnet/mainnet policies side-by-side to make diffs obvious. Treat policy like code: review, test, schedule, measure.
