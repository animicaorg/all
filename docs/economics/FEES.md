# Fees — Base/Gas Fees, Fee Markets, Blob Fees (v1)

This document describes how Animica prices execution and data availability. It is **informational**; the **consensus rules** live in the `spec/` and `execution/` modules and the **DA** rules in `da/`.

**Related specs & code**
- Execution gas: `spec/opcodes_vm_py.yaml`, `execution/gas/*`, `vm_py/gas_table.json`
- Tx/Block encoding: `docs/spec/TX_FORMAT.md`, `docs/spec/BLOCK_FORMAT.md`
- Mempool policy & fee market: `docs/spec/MEMPOOL.md`, `mempool/*`
- Data Availability pricing: `docs/spec/MERKLE_NMT.md`, `docs/spec/DA_ERASURE.md`, `da/*`
- Economics overview: `docs/economics/OVERVIEW.md`

---

## 1) Fee Components

For a transaction `tx`, the **fee** paid by the sender and **revenue** earned by the block producer are decomposed as:

- **Base fee** (`baseFee`) — dynamic per-gas price set by the protocol based on recent block utilization.  
  - **Protocol burn**: a configured fraction (typically 100%) of `baseFee * gasUsed` is **burned**.
- **Tip** (`priorityFeePerGas`) — per-gas price chosen by the sender to incentivize inclusion; paid to the block producer.
- **Blob fee** (`baseDaFee`) — per-**DA unit** price for data availability (applies to blob-carrying txs).  
  - A configured fraction (typically 100%) of `baseDaFee * daUnitsUsed` is **burned**.

> A transaction may carry both *execution gas* and *DA units* (e.g., rollup-style calldata/blobs). Some transactions (pure transfers) have execution gas only.

---

## 2) Units & Notation

- `gasUsed(tx)`: execution gas consumed by `tx`. Determined by the VM + host system:
  - intrinsic gas (`execution/gas/intrinsic.py`),
  - opcode costs (`vm_py/gas_table.json`),
  - gas refunds (`execution/gas/refund.py`),
  - metering (`execution/gas/meter.py`).
- `daUnitsUsed(tx)`: DA units used by blobs (post-erasure & namespacing). Computed by `da/erasure/*` and `da/blob/*`.
- Prices are quoted in **nano-ANM** per unit unless otherwise stated.

**Total debit to sender**

fee_exec   = gasUsed(tx)   * (baseFee + tip)
fee_blobs  = daUnitsUsed(tx) * baseDaFee
fee_total  = fee_exec + fee_blobs

**Block producer revenue**

rev_miner = gasUsed(tx) * tip
+ miner_share_of_other_rewards (AICF etc., see Economics Overview)

**Protocol burn**

burn = burn_ratio_exec * gasUsed(tx) * baseFee
+ burn_ratio_da   * daUnitsUsed(tx) * baseDaFee

---

## 3) Fee Market Dynamics

### 3.1 Execution Base Fee (`baseFee`)
Animica uses a **smooth controller** that targets a block utilization `u_target` relative to a **gas target** `G_target`:

- `u = gasUsed(block) / G_target`
- Controller update per block *n → n+1*:

baseFee_{n+1} = max(MIN_BASE_FEE,
baseFee_n * (1 + α * clamp(u - u_target, -Δ, +Δ)))

Parameters:
- `α` (aggressiveness) — e.g., 0.125
- `Δ` (clamp) — caps per-block change, e.g., ±0.25 (±25%)
- `u_target` — typically 1.0 (target equals `G_target`)
- `G_target` and `MIN_BASE_FEE` are network params (`spec/params.yaml`).

Intuition: when blocks are fuller than target, `baseFee` increases; otherwise it decreases.

### 3.2 DA Base Fee (`baseDaFee`)
Blobs use a **separate controller** with a target **DA capacity** `D_target` per block:

- `u_da = daUnitsUsed(block) / D_target`
- Update:

baseDaFee_{n+1} = max(MIN_DA_FEE,
baseDaFee_n * (1 + β * clamp(u_da - u_da_target, -Δ_da, +Δ_da)))

- `β` may differ from `α` (DA demand can be burstier).
- DA units reflect **post-erasure** expanded size (so cost scales with actual redundancy required for availability).

> Separation ensures execution congestion does not spill into DA pricing and vice versa.

---

## 4) Transaction Anatomy & Intrinsic Gas

Each tx pays an **intrinsic** amount covering:
- Signature verification & envelope size,
- Access list (if present),
- Base **kind** (transfer / deploy / call),
- Optional blob anchors (commitments).

See:
- `execution/gas/intrinsic.py` for formulae,
- VM opcode costs in `vm_py/gas_table.json`,
- ABI/event/log costs in `execution/types/gas.py` and `execution/state/receipts.py`.

**Gas refunds** reduce `gasUsed` at finalization but are capped (`execution/gas/refund.py`) to avoid griefing.

---

## 5) Mempool Fee Policy

The mempool enforces **admission** and **replacement** rules (see `docs/spec/MEMPOOL.md`, `mempool/policy.py`):

- **Admission floor**: `effectivePrice = baseFee + tip ≥ min_gas_price` and `tip ≥ min_tip` (policy-derived).
- **Replacement (RBF)**: a tx with the *same sender+nonce* must bid at least **X% higher** `effective fee` and **Y% higher** `tip` to replace (e.g., X=10%, Y=5%).
- **Blob floor**: txs with blobs must also meet `baseDaFee ≥ min_da_price` at the time of admission (to avoid spam during DA troughs).
- **Watermark eviction**: under pressure, lowest-**priority** txs (effective price per gas) are dropped first, with per-sender fairness caps.

> **Effective priority** considers fee, size, age; see `mempool/priority.py`.

---

## 6) Blob Accounting & DA Units

A blob-carrying tx includes a **commitment** that is checked against the block’s **DA root**. DA units map to the **erasure-coded footprint**:

daUnitsUsed(tx) = RS_expand(bytes) → shares → namespaced leaves
= ceil(total_shares / shares_per_unit)

- Parameters (`da/erasure/params.py`): `(k, n)` coding rate, share size, and per-block DA limits.
- The block builder computes `da_root` and charges the sender `baseDaFee * daUnitsUsed(tx)`.

**Light clients** verify availability probability via **DAS** proofs (see `docs/spec/DA_ERASURE.md` and `da/sampling/*`).

---

## 7) Tips, Priority & Inclusion

Block producers order by **marginal revenue**:
1. Highest `tip` per gas (*subject to gas limits*),
2. Then blob-paying txs by `baseDaFee` (since burns do not accrue to miner, only inclusion constraints apply),
3. Subject to **dependencies** (nonce queues) and **policy caps**.

**Recommendation for users**
- Set `maxFeePerGas ≥ baseFee + safety_margin`,
- Set `priorityFeePerGas` based on current congestion percentiles,
- For blobs, ensure `baseDaFee` headroom in volatile periods.

---

## 8) Worked Examples

### Example A — Simple transfer
- `gasUsed = 21,000`
- `baseFee = 40 gwei`, `tip = 2 gwei`
- `fee_exec = 21,000 * (42 gwei) = 882,000 gwei`
- `burn = 21,000 * 40 gwei = 840,000 gwei`
- `miner_rev = 21,000 * 2 gwei = 42,000 gwei`

### Example B — Contract call with blob
- `gasUsed = 120,000`; `daUnitsUsed = 18`
- `baseFee = 35 gwei`; `tip = 3 gwei`; `baseDaFee = 12 gwei/DAU`
- `fee_exec = 120,000 * 38 gwei = 4,560,000 gwei`
- `fee_blobs = 18 * 12 gwei = 216 gwei`
- `fee_total = 4,560,216 gwei`
- `burn = 120,000 * 35 gwei + 18 * 12 gwei`
- `miner_rev = 120,000 * 3 gwei`

*(Numbers illustrative.)*

---

## 9) Estimation & Simulation

Use SDK helpers to estimate gas and fees, then **simulate** before sending:

- **Python**
  - `omni_sdk.tx.build.estimate_gas(...)`
  - `omni_sdk.tx.send.send(...)`
- **TypeScript**
  - `@animica/sdk/tx/estimate` and `tx/send`
- **RPC**
  - `state.getBalance`, `state.getNonce`, and a **simulate** method (via Studio Services) for dry-runs.

> For blobs, pre-compute DA units via the DA client (`sdk/.../da/client`) to budget `baseDaFee`.

---

## 10) Edge Cases & Protections

- **Underpriced txs**: rejected at admission or remain stuck; re-price with RBF.
- **BaseFee spikes**: controller clamps per-block changes; users should set sane `maxFeePerGas`.
- **Refund abuse**: refunds are capped and cannot exceed a fraction of gas used.
- **Blob spam**: DA base fee rises under load; minimum DA price gate enforced; block DA limits hard-stop excess.
- **ChainId mismatch**: rejected early (`mempool/validate.py`).

---

## 11) Parameterization (Illustrative Defaults)

- `G_target` (gas target per block): 10,000,000
- `D_target` (DA units target per block): 2,048
- `α = 0.125`, `β = 0.20`
- `Δ = 0.25`, `Δ_da = 0.35`
- `MIN_BASE_FEE = 1 gwei`, `MIN_DA_FEE = 1 gwei`
- Admission floors:
  - `min_gas_price = 1 gwei` (network-dependent)
  - `min_tip = 1 gwei`
  - `min_da_price = 1 gwei`

Actual values are defined in `spec/params.yaml` and may vary by network (devnet/testnet/mainnet).

---

## 12) Implementation Pointers

- **Execution**: `execution/runtime/fees.py`, `execution/gas/*`, `vm_py/runtime/gasmeter.py`
- **Mempool**: `mempool/fee_market.py`, `mempool/policy.py`, `mempool/watermark.py`
- **DA**: `da/erasure/params.py`, `da/blob/commitment.py`, `da/sampling/*`
- **RPC**: `rpc/methods/tx.py`, `rpc/methods/state.py`, DA endpoints in `da/adapters/rpc_mount.py`

---

## 13) FAQ

**Q:** Are DA fees paid to miners?  
**A:** No. By default DA base fees are **burned** (like execution base fees). Miners are compensated via tips and AICF splits.

**Q:** Can a tx set a max for DA fees?  
**A:** Yes. Transactions can include a `maxBaseDaFee` safety cap; if `baseDaFee` exceeds this at inclusion time, the tx is invalid for that block (wallets/SDK should reprice).

**Q:** Do refunds apply to DA?  
**A:** No. Refunds apply to execution gas only.

---

## 14) Summary

- **Two markets**: execution (`baseFee`) and data availability (`baseDaFee`), each with its own controller.
- **Burns** create a counterweight to issuance; **tips** incentivize inclusion.
- **Mempool** enforces fair pricing with replacement rules; **DA** charges are proportional to provable availability cost.

*Version: v1.0 — subject to policy updates via the upgrades process.*
