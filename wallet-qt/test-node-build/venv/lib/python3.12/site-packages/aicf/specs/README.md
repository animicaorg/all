# AICF Specs

This folder collects pointers to the specification material that governs the **AI/Quantum Compute Fund (AICF)**: provider registry, staking & attestation, SLA & slashing, and economics (pricing → payouts → settlement). The normative behavior lives in code with matching tests and test vectors; this README is a map.

---

## Policy & Parameters (entry point)

- Example network policy: [`aicf/policy/example.yaml`](../policy/example.yaml)  
  Defines epoch length, Γ_fund caps, pricing tiers, split ratios, SLA thresholds, and slashing bounds.

---

## Economics

**What it covers:** converting job units to rewards, fee splits, epoch accounting, and settlement into the treasury/provider balances.

- Pricing model: [`aicf/economics/pricing.py`](../economics/pricing.py)
- Reward split rules: [`aicf/economics/split.py`](../economics/split.py)
- Escrow for requestors (when applicable): [`aicf/economics/escrow.py`](../economics/escrow.py)
- Epoch caps & rollover (Γ_fund): [`aicf/economics/epochs.py`](../economics/epochs.py)
- Settlement batching: [`aicf/economics/settlement.py`](../economics/settlement.py)
- Payout record builder: [`aicf/economics/payouts.py`](../economics/payouts.py)
- Treasury plumbing:
  - Internal ledgers: [`aicf/treasury/state.py`](../treasury/state.py)
  - Epoch mint (if configured): [`aicf/treasury/mint.py`](../treasury/mint.py)
  - Reward crediting: [`aicf/treasury/rewards.py`](../treasury/rewards.py)
  - Withdrawals/cooldowns: [`aicf/treasury/withdraw.py`](../treasury/withdraw.py)
- Vectors & benches:
  - Settlement vectors: [`aicf/test_vectors/settlement.json`](../test_vectors/settlement.json)
  - Settlement batch bench: [`aicf/bench/settlement_batch.py`](../bench/settlement_batch.py)
  - Pricing/ split tests: [`aicf/tests/test_pricing_split.py`](../tests/test_pricing_split.py), [`aicf/tests/test_payouts_settlement.py`](../tests/test_payouts_settlement.py)

**Related (capabilities-side concepts):**
- Treasury split/escrow rationale: [`capabilities/specs/TREASURY.md`](../../capabilities/specs/TREASURY.md)

---

## SLA & Slashing

**What it covers:** measurement of provider quality (traps, QoS, latency, availability), evaluation windows & confidence, and penalties/jailing.

- Metrics collection: [`aicf/sla/metrics.py`](../sla/metrics.py)
- Evaluator (thresholds, windows, confidence): [`aicf/sla/evaluator.py`](../sla/evaluator.py)
- Slash engine: [`aicf/sla/slash_engine.py`](../sla/slash_engine.py)
- Penalty magnitudes & clawback schedule: [`aicf/economics/slashing_rules.py`](../economics/slashing_rules.py)
- Vectors & tests:
  - Slashing vectors: [`aicf/test_vectors/slashing.json`](../test_vectors/slashing.json)
  - SLA tests: [`aicf/tests/test_sla_eval.py`](../tests/test_sla_eval.py), [`aicf/tests/test_slashing.py`](../tests/test_slashing.py)

**Also see (capabilities security considerations):**
- Abuse prevention & replay/nullifiers: [`capabilities/specs/SECURITY.md`](../../capabilities/specs/SECURITY.md)
- Compute flow & traps context: [`capabilities/specs/COMPUTE.md`](../../capabilities/specs/COMPUTE.md)

---

## Provider Registry & Attestation

**What it covers:** provider identities, capability flags (AI/Quantum), attestation verification (TEE/QPU), staking, heartbeats, allow/deny lists, and eligibility filters.

- Registry API: [`aicf/registry/registry.py`](../registry/registry.py)
- Staking & lockups: [`aicf/registry/staking.py`](../registry/staking.py)
- Identity & attestation verification: [`aicf/registry/verify_attest.py`](../registry/verify_attest.py)
- Allow/deny lists & regions: [`aicf/registry/allowlist.py`](../registry/allowlist.py)
- Heartbeats & health decay: [`aicf/registry/heartbeat.py`](../registry/heartbeat.py)
- Eligibility filters & penalties: [`aicf/registry/filters.py`](../registry/filters.py), [`aicf/registry/penalties.py`](../registry/penalties.py)
- Data model (SQLite): [`aicf/db/schema.sql`](../db/schema.sql), [`aicf/db/migrations/0001_init.sql`](../db/migrations/0001_init.sql)
- Tests: [`aicf/tests/test_registry.py`](../tests/test_registry.py), [`aicf/tests/test_staking.py`](../tests/test_staking.py)

---

## Queueing, Assignment & Proof Intake (Context)

While not strictly “spec docs,” these define observable behavior relevant for economics/SLA:

- Queue storage/priority/quotas: [`aicf/queue/storage.py`](../queue/storage.py), [`aicf/queue/priority.py`](../queue/priority.py), [`aicf/queue/quotas.py`](../queue/quotas.py)
- Deterministic job IDs: [`aicf/queue/ids.py`](../queue/ids.py)
- Assignment & leases: [`aicf/queue/assignment.py`](../queue/assignment.py), [`aicf/queue/dispatcher.py`](../queue/dispatcher.py)
- Retries, TTL, receiver: [`aicf/queue/retry.py`](../queue/retry.py), [`aicf/queue/ttl.py`](../queue/ttl.py), [`aicf/queue/receiver.py`](../queue/receiver.py)
- Proof → claim mapping: [`aicf/integration/proofs_bridge.py`](../integration/proofs_bridge.py)
- Execution hooks for payouts: [`aicf/integration/execution_hooks.py`](../integration/execution_hooks.py)
- Randomness for assignment shuffles: [`aicf/integration/randomness_bridge.py`](../integration/randomness_bridge.py)
- Deterministic matching vectors: [`aicf/test_vectors/assignments.json`](../test_vectors/assignments.json)

---

## RPC & Events

- Methods: [`aicf/rpc/methods.py`](../rpc/methods.py)
- Mount & WebSocket events: [`aicf/rpc/mount.py`](../rpc/mount.py), [`aicf/rpc/ws.py`](../rpc/ws.py)
- CLI flows: see `aicf/cli/*` and integration tests like [`aicf/tests/test_cli_provider_flow.py`](../tests/test_cli_provider_flow.py)

---

## Versioning

- Module/version helpers: [`aicf/version.py`](../version.py)  
  Backward-incompatible policy changes must bump the major version and include an activation epoch in network configs.

---

## See Also

- Capabilities layer specs (contract-facing syscalls, determinism, treasury rationale):
  - [`capabilities/specs/SYSCALLS.md`](../../capabilities/specs/SYSCALLS.md)
  - [`capabilities/specs/COMPUTE.md`](../../capabilities/specs/COMPUTE.md)
  - [`capabilities/specs/TREASURY.md`](../../capabilities/specs/TREASURY.md)
  - [`capabilities/specs/SECURITY.md`](../../capabilities/specs/SECURITY.md)

> **Status:** Draft; code and tests are the ground truth. Open questions and tuning notes live in policy files and test vectors.
