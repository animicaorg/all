---
proposal:
  id: GOV-2025-11-VM-OPC-01
  title: "Activate OP_BLAKE3 (0xC7) in Animica VM"
  authors:
    - name: "Animica Core Maintainers"
      contact: "governance@animica.dev"
  created: "2025-10-31"
  type: "upgrade"               # matches governance/schemas/upgrade.schema.json
  target: "vm"                  # vm / node / protocol / params
  deposit:
    asset: "ANM"
    amount: "100000"            # example; see DEPOSIT_AND_REFUNDS.md
  voting:
    votingPeriodDays: 7
    quorumPercent: 10
    approvalThresholdPercent: 66.7
  rollout:
    policyVersion: "1.0"
    phases:
      - name: "localnet-canary"
        chainId: 1337
        activationTime: "2025-11-05T16:00:00Z"
        abortOn:
          - metric: "node_crash_rate_percent"
            op: ">="
            value: 2.0
          - metric: "mempool_reject_rate_percent"
            op: ">="
            value: 5.0
      - name: "testnet"
        chainId: 2
        activationTime: "2025-11-12T16:00:00Z"
        gates:
          - "no_critical_bugs_open_7d"
          - "sync_rate_99_9_percent_24h"
          - "indexers_green_48h"
        abortOn:
          - metric: "orphan_rate_percent_24h"
            op: ">="
            value: 3.0
          - metric: "api_error_rate_percent_1h"
            op: ">="
            value: 1.0
      - name: "mainnet-gated"
        chainId: 1
        activationTime: "2025-12-10T16:00:00Z"
        gates:
          - "all_exchanges_upgrade_announce"
          - "wallets_release_approved"
          - "explorer_support_merged"
        canaries:
          - "10% nodes on 0xC7-enabled build for 24h (shadow-parse only)"
          - "2% txs using OP_BLAKE3 via allowlist contracts for 48h"
        abortOn:
          - metric: "reorg_depth_max_24h"
            op: ">="
            value: 2
          - metric: "block_gas_spike_percent_over_baseline"
            op: ">="
            value: 25
          - metric: "tx_latency_p95_ms"
            op: ">="
            value: 2500
  versioning:
    vm:
      previous: "0.12.4"
      next: "0.13.0"
    node:
      minRequired: "1.8.0"
      recommended: "1.8.1"
  registriesTouched:
    - "governance/registries/contracts.json"
    - "governance/registries/upgrade_paths.json"
  links:
    discussion: "https://forum.animica.dev/t/gov-2025-11-vm-opc-01"
    specPR: "https://code.animica.dev/vm/specs/pull/421"
    refImplPR: "https://code.animica.dev/node/animicad/pull/1187"
---

# GOV-2025-11-VM-OPC-01 — Activate `OP_BLAKE3 (0xC7)` in Animica VM

**One-liner:** Enable a new deterministic hashing opcode `OP_BLAKE3` to compute BLAKE3 digests efficiently in the Python-VM, improving contract ergonomics and gas efficiency for hashing-heavy workloads (merkle proofs, commitments, DA integrity checks).

---

## 1) Motivation

Contracts today emulate BLAKE3 via bytecode libraries, which is:
- **Gas-inefficient:** ~20–30× more gas than a native opcode for common inputs.
- **Code-bloaty & error-prone:** multiple divergent libraries with subtle bugs.
- **DA alignment:** our DA layer and tooling increasingly rely on BLAKE3 checksums.

A native opcode standardizes behavior, reduces gas costs, and simplifies on-chain verification flows while remaining fully deterministic in the VM.

---

## 2) Scope & Backward Compatibility

- **Soft-fork style feature:** Existing contracts are unaffected. After activation, contracts may begin *using* `OP_BLAKE3`. Pre-activation transactions containing `0xC7` are invalid on chains where it is not yet enabled.
- **Node/API Changes:** Nodes parse and reject pre-activation usage; after the chain’s activation height/time, nodes execute the new opcode.
- **Indexers/SDKs:** Read-only; must bump decoders/ABI tables to recognize opcode metadata in traces.

---

## 3) Technical Specification

### 3.1 Opcode
- **Mnemonic:** `OP_BLAKE3`
- **Opcode byte:** `0xC7`
- **Stack I/O:** `(<bytes>) -> (<digest: bytes32>)`
- **Determinism:** Pure, no I/O, no access to time/chain state.
- **Input size limits:** `0 <= len(input) <= 131072` bytes (128 KiB). Larger inputs must be chunked by caller.
- **Error conditions:**
  - Stack underflow → `VMError(StackUnderflow)`
  - Input length > 128 KiB → `VMError(InvalidInputLength)`
- **Gas cost model (VM v0.13):**
  - `G_base = 12`
  - `G_per32 = 2` per 32-byte chunk (ceil division)
  - `Gas = G_base + G_per32 * ceil(len(input)/32)`
  - **Rationale:** Benchmarked against ref impl; tuned to sit ~3× cheaper than best bytecode lib while proportional to input length.

### 3.2 Serialization/ABI
- **ABI selector:** `hash.blake3(bytes) -> bytes32`
- **Trace tag:** `op_b3`
- **Gas accounting:** charged before compute; overflow reverts.

### 3.3 Activation Mechanism
- Feature flag in chain params: `vm.opcodes.enabled: ["OP_BLAKE3"]`
- Node enforces `activation_time` per chain (see rollout table in header).
- Contracts may `require(vm_version >= 0.13.0)` if they depend on the opcode.

---

## 4) Rollout Plan (Gated & Abort Switches)

See YAML header for machine-readable schedule. Human summary:

1. **Localnet (1337) — Nov 5, 2025:** Enable by default; exercise fuzz and gas instrumentation.
2. **Testnet (2) — Nov 12, 2025:** Enable after green gates. Canary contracts publish merkle verification examples; explorers show opcode in traces.
3. **Mainnet (1) — Dec 10, 2025:** Gated release with shadow parsing first, allowlisted canary usage, then full enablement on success.

**Abort conditions** (trigger immediate pause/revert to pre-activation rule via emergency param switch):
- Reorg depth ≥ 2 within 24h *and* correlated mempool reject spikes.
- p95 tx latency ≥ 2.5s sustained over 1h with opcode usage > 2% of gas.
- Crash rate ≥ thresholds defined per phase (see header).

---

## 5) Reference Implementation

- **VM (Python):** Adds `op_b3(input: bytes) -> bytes32` binding to the BLAKE3 constant-time implementation compiled for the sandbox; falls back to a verified pure-Python path in debug.
- **Node:** Opcode table extended; gas schedule bump to `vm_gas_v13`.
- **SDKs:** New helper `hashBlake3(bytes) -> Bytes32`.

Test vectors (subset):input: 0x
digest: 0xAF1349B9… (blake3("") 32-byte digest) # canonical
input: 0x616e696d696361
digest: 0x… # "animica"


(Full vectors are included in the VM test suite.)

---

## 6) Risks & Mitigations

- **Gas Underestimation:** Could increase block gas utilization. *Mitigation:* conservative `G_per32`; monitor `block_gas_spike_percent_over_baseline`.
- **Implementation Bugs:** Divergence between native and fallback paths. *Mitigation:* cross-tests against official BLAKE3 vectors; deterministic CI.
- **Ecosystem Fragmentation:** Some tooling unaware of opcode. *Mitigation:* explorer/wallet releases prior to mainnet gate.

See `governance/risk/UPGRADE_RISK_CHECKLIST.md` for sign-off steps.

---

## 7) Monitoring & Success Criteria

- p50/p95 gas per `op_b3` call tracks with model ±10%.
- Opcode usage share ≥ 0.5% on testnet within 7 days (indicates adoption).
- No increase in orphan rate beyond 1% absolute over 7 days post-activation.

---

## 8) Backout / Emergency Plan

- Toggle `vm.opcodes.enabled` to remove `OP_BLAKE3` (hard-disable) via emergency admin (see multisig policy).
- If hard-disabled post-activation, contracts calling it will revert; communications plan to developers and exchanges included in `TRANSPARENCY.md`.

---

## 9) On-Chain / Registry Changes

- **Params (chain):**
  - `vm.version = "0.13.0"`
  - `vm.opcodes.enabled += ["OP_BLAKE3"]`
  - `vm.gasTables["0.13"].opcodes["OP_BLAKE3"] = { base: 12, per32: 2, maxInput: 131072 }`
- **Registries:**
  - `governance/registries/upgrade_paths.json`: allow `0.12.x → 0.13.0`
  - `governance/registries/contracts.json`: add opcode metadata hash for trace decoders.

---

## 10) Alternatives Considered

- Contract library only (status quo) — rejected for gas/correctness reasons.
- SHA-3 / Keccak opcode instead — orthogonal; may be proposed separately.

---

## 11) Sign-Off Checklist

- [ ] VM unit tests passing (native + fallback)
- [ ] Node integration tests passing
- [ ] Explorer decoding PR merged
- [ ] Wallet SDK helpers released
- [ ] Infra runbooks updated

---

*This document is an **example** upgrade proposal formatted to validate against the governance schemas and to be consumable by tooling (ballot generation, registry checks, tests). Replace IDs/timestamps as appropriate for real proposals.*
