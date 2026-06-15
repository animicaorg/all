# ENA Training Pool — train together, serve while training, code with the result

This is the north-star design for turning ENA from a single-operator training CLI
into a **community flywheel**: many people fund and train *one* model together, the
network *serves that model for inference while the next round is still training*,
and an **ENA-native coding agent** consumes it. Usage revenue flows back to fund the
next round.

**The end state (the whole point):** one prebuilt binary / one command starts
*everything* — node + miner + useful-work + training + serving — joining one pool
that trains one global model. A CPU box mines and does CPU useful-work; a GPU box
also trains shards and serves the promoted checkpoint. No manual wiring. Every
phase below is plumbing that this single command turns on.

```
  Funders ──ANM──▶ ┌──────────────────┐
                   │   ENA TRAIN POOL  │──shards──▶ Trainers (GPU) ──checkpoints──▶┐
                   │  (coordinator)    │◀──receipts/eval──────────────────────────┘
                   └────────┬──────────┘
                            │ promotes best checkpoint (passes eval gate)
                            ▼
              ┌─────────────────────────────┐
   Servers ──▶│ P2P serving layer (on-chain │◀── inference ── ENA coding agent / clients
  (inference) │  advertise + per-call pay)  │      pays ANM ─────────────┐
              └─────────────────────────────┘                            │
                            ▲──────────── revenue funds the pool ─────────┘
```

## Locked decisions (2026-06-15)

1. **Build all three, phased**: `pool → serve → agent`.
2. **Serving is on-chain / P2P**, not routed through the centralized
   `pool.animica.org` broker. Workers advertise on-chain; clients discover and
   pay per call. (The broker remains available as an optional convenience route,
   but the canonical path is decentralized.)
3. **A new ENA-native coding agent** (its own tool/repo loop + on-chain identity),
   not a thin wrapper over an existing tool.
4. **One unified binary/CLI runs everything** — `animica node` / `animica miner`
   boots node + miner + useful-work + training + serving from one config with
   capability auto-detect. No separate processes to wire up. (Phase 4)
5. **Pool-level miner-version enforcement**, enforced immediately on deploy:
   the pool refuses shares + AI jobs from miners below the required version so
   everyone must update to the unified build. **Policy only — never a consensus
   fork** (no chain-stall risk), and reversible by clearing the config flag.
   No network-wide protocol bump. (Phase 5 — shipped)
6. **One global canonical model** — every pool, run by anyone, contributes to a
   *single* model rather than forking its own. The model's identity and current
   promoted head live on-chain; pools are contributors, not forks. (Phase 6)

The unified mining + AI build ships as **1.0.0**.

## Why this reuses ~70% of what exists

| Need | Existing primitive | File |
| --- | --- | --- |
| Per-epoch checkpoints (enables serve-while-train) | `Trainer(save_strategy="epoch")` | `ena/training.py` |
| Wallet-funded contribution | demand quote→pay→confirm | `ena/demand.py` |
| Worker fan-out / atomic claim | job queue + `claim_one_job` | `ena/jobs.py`, `ena/store.py` |
| On-chain reward receipts | `build_training_receipt` → `TrainingReceipt` → `UsefulWorkProof` | `ena/training.py`, `aicf/aitypes/training_receipt.py` |
| Proportional share→payout | XMR pool ledger pattern | `stratum_pool/xmr_payouts.py` |
| Deterministic content hashing / sharding | `_stable_bucket`, `split` | `ena/datasets.py` |
| OpenAI-compatible serving guts | `LocalBundleRunner` | `ai/flagship_agent/.../inference.py` |
| Provider abstraction | `openai_compatible` / `ollama` adapters | `ena/providers.py` |

The work is the **connective tissue**, not new foundations.

## Roles & economics

Three roles earn from a pool; a fourth (users) pays into it.

- **Funders** pay ANM to a pool. Earn a configurable cut of each round's reward as
  ROI, share ∝ ANM contributed.
- **Trainers** claim a dataset shard, train it, submit a checkpoint + a
  `TrainingReceipt`. Earn the bulk, share ∝ *verified work weight*
  (`gpu_hours`, falling back to `samples_processed`).
- **Servers** (Phase 2) serve the promoted checkpoint for inference. Earn per
  token served (settled per-call in the P2P layer).
- **Users** (Phase 3 agent + any OpenAI-compatible client) pay per call; revenue
  is split server↔pool, refilling the funding budget.

`reward_split` is basis points across the three earning roles and must sum to
10000, default `{funders: 2000, trainers: 6000, servers: 2000}`.

## Pool lifecycle (rounds)

A pool trains in **rounds**. Each round:

1. **Fund** — funders `fund_quote`→pay ANM (memo = `pool_hash`)→`fund_confirm`.
   Confirmed ANM increases the round budget and records a funder contribution.
2. **Shard** — the pool dataset is deterministically split into `num_shards`
   (same `_stable_bucket` hashing as `datasets.split`, generated lazily on first
   claim). Trainers `claim_shard` (atomic, mirrors `claim_one_job`).
3. **Train & submit** — each trainer trains its shard (`ena train run` on the
   shard sub-manifest) and `submit_shard`s the checkpoint + metrics. A
   `TrainingReceipt` is built per submission; a trainer contribution is recorded
   weighted by verified work.
4. **Aggregate + eval gate** — once enough shards are in, `aggregate` merges the
   submitted adapters (weighted LoRA-adapter average; merge plan hashed
   deterministically) and runs the **eval gate**. If the candidate beats the
   threshold (or no gate is set) it is **promoted** to the pool's
   `served_checkpoint` and the round advances.
5. **Payout** — `payout` snapshots the round's contributions and splits the
   budget by `reward_split`, then proportionally within each role by weight
   (integer-safe, remainder to the largest share, same shape as XMR payouts).
   Emits a payout plan the on-chain scheduler executes.

**Serve-while-train** falls out for free: serving workers always load the
*currently promoted* `served_checkpoint` (round N) while trainers produce round
N+1. A half-baked checkpoint never gets served because promotion is gated on eval.

## One global canonical model (Phase 6)

The default `pool_id` derives from `{base_model, dataset_sha256, method, name}`,
so distinct specs make distinct pools. For a *single global model* that everyone
improves, pools must converge instead of fork:

- **Canonical identity on-chain** — a well-known global model id + lineage record
  (genesis base_model, method, reward policy). "Starting your own pool" registers
  you as a *contributor* to this model, not the owner of a new one.
- **On-chain promoted head** — the current global `served_checkpoint` hash is
  published on-chain so anyone can discover and serve "the" model, and every new
  round trains on top of the same head (`base_model` of round N+1 = promoted head
  of round N).
- **Pools are shards of one effort** — each operator's pool runs rounds, but its
  `TrainingReceipt`s and promoted checkpoints anchor to the global lineage. The
  global head advances when *any* pool's aggregated round passes the shared eval
  gate against the global head.
- **Rewards stay local, the model stays global** — each pool still splits its own
  budget among its own funders/trainers/servers; only the model artifact + lineage
  are shared. This is what makes independent operators additive, not divisive.

## Version policy (Phase 5 — shipped)

`animica.stratum_pool.version_gate` + two `PoolConfig` flags
(`min_miner_version`, `require_min_version`, env
`ANIMICA_POOL_MIN_MINER_VERSION` / `ANIMICA_POOL_REQUIRE_MIN_VERSION`). When on,
the pool rejects miners reporting an older/missing version: the authorize hook
flags the address and the share validator refuses its shares; AICF jobs are not
routed to it. Miners advertise their version in `mining.subscribe` features
(`version` / `agent_version` / `aicf.version`). Pure pool policy — it never
touches consensus, so it cannot fork or stall the chain, and clearing the flag
restores the old behaviour instantly. The unified build reports `1.0.0`.

## Phasing

- **Phase 1 — coordinator core** *(done)*: `ena/pool.py` `PoolService`
  (create / fund / claim_shard / submit_shard / aggregate+gate+promote / payout),
  `Pool`/`PoolShard`/`PoolContribution` schemas, pool tables + store methods,
  unit tests. Heavy deps stay lazy; the core is stdlib-only and CPU-testable.
- **Phase 1b** *(next)*: CLI (`ena pool …`), HTTP (`/pool/*`), facade, and an
  ena.animica.org pool section.
- **Phase 2 — serve while training**: OpenAI-compatible inference server wrapping
  `LocalBundleRunner`, hot-reloading on promotion; on-chain advertisement of
  serving endpoints + discovery + per-call settlement + response verification.
- **Phase 3 — ENA-native coding agent**: dedicated agent (tool/repo loop,
  on-chain identity) consuming the pool model over the P2P serving layer.
- **Phase 4 — unified node+miner**: one supervisor/CLI boots node + miner +
  useful-work + trainer + server, capability auto-detect, prebuilt binary + CLI.
- **Phase 5 — version policy** *(done)*: see above.
- **Phase 6 — one global canonical model**: see above; builds on Phase 2's
  on-chain discovery.

## Determinism contract (must stay stable — ids/receipts are anchored)

- `pool_id = "enapool-" + sha3(canonical_spec)[:32]` where the spec is
  `{base_model, dataset_sha256, method, name}`.
- `pool_hash = sha3(canonical({…spec, pool_id, pool_hash:""}))` — used as the
  on-chain payment memo so any funder's payment binds to the pool.
- Shards, merge plans, and contributions all hash via the shared
  `canonical_json` / `sha3_hex` helpers in `ena/models.py`.
