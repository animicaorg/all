# MEV: Ordering Rules, Mitigations, Transparency (v1)

**Scope.** This document describes the default transaction **ordering policy**, identifies common **MEV** (Miner/Maximal Extractable Value) vectors in Animica, and specifies **mitigations** and **transparency** requirements that builders/miners and operators MUST/SHOULD implement. It aligns with `mempool/*`, `execution/runtime/*`, `randomness/*`, and `rpc/*`.

> TL;DR: We prioritize safety and liveness first, then fairness. The reference builder uses a **bucketed priority** with **per-sender FIFO**, **beacon-seeded tie-breaking**, and an optional **Call Auction** for DEX-like calls. Operators MUST publish ordering metadata per block.

---

## 1) Threat Model

MEV arises when a block builder can profit by **reordering**, **inserting**, or **censoring** user transactions:
- **Sandwiching** (front-run → victim swap → back-run)
- **Back-running** liquidations / oracle updates / AMM swaps
- **Time-bandit** reorgs to capture on-chain arbitrage
- **Censorship-for-rent**: extracting side payments for inclusion/omission
- **Private order flow asymmetry**: preferential access to orderflow

**Non-goals (v1):**
- We do not attempt to eliminate all MEV. We constrain the worst externalities, reduce unilateral rent extraction, and make behavior observable.

---

## 2) Vocabulary & Signals

- **Effective Priority** (mempool): computed in `mempool/priority.py` from *tip*, *size*, *age*, and *replace-by-fee (RBF) deltas*.  
- **Buckets**: priority bands (e.g., `VIP`, `P1`, `P2`, `FLOOR`).  
- **Sender FIFO**: per-sender nonce-ordered queue; **no cross-sender reordering** within the same bucket by fee deltas alone.
- **Beacon**: randomness output (`randomness/beacon/*`) used as a seed for tie-breakers.
- **Auction Window**: optional short interval where price-sensitive calls are **batched** and **cleared at a uniform price** (call auction).

---

## 3) Reference Ordering Rule (Builder)

The reference builder in `mempool/drain.py` + `execution/runtime/executor.py` selects transactions under gas/byte limits using the following **deterministic** policy:

1. **Admission filters** (mempool/validate.py): reject wrong chainId, oversize, invalid PQ signatures, or below-min-fee.
2. **Bucketize** by Effective Priority:  
   `VIP` ≥ surge floor × X; `P1` ≥ floor × 1.0; `P2` ≥ floor × 0.5; `FLOOR` = just at floor. (Configurable.)
3. **Within each bucket**:  
   a. **Per-sender FIFO**: strictly increasing nonces.  
   b. **Inter-sender tie-break**: compute stable key
      \[
      k = \mathrm{sha3\_256}(\text{beacon\_round}\,\|\,\text{txHash}\,\|\,\text{sender})
      \]
      and sort ascending. This prevents deterministic “same-sender always wins” and thwarts trivial fee sniping races.  
   c. **Age bonus**: tiny additive rank offset capped to avoid starvation.
4. **RBF policy** (mempool/policy.py): Exact nonce replacement from same sender requires ≥ **RBF\_THRESHOLD** (e.g., +10–15% effective fee). Prevents griefing by micro-bumps.
5. **Diversity guard** (optional): limit per-sender share in a block (e.g., ≤ 25%) to reduce captive orderflow exploitation.
6. **Optional Call Auction** (Section 5) for whitelisted methods (AMM swaps, liquidations) over a short **N-second** in-block window.

> **Determinism**: Ordering depends only on mempool state, chain head, and beacon; all are recorded or derivable.

---

## 4) Baseline Mitigations

### 4.1 User-side protections
- **Slippage caps & deadlines.** Wallets and SDKs MUST default to strict `minReturn` and short `deadline` on DEX calls.
- **MEV-protected RPC** (private submit): Support for `/rpc` endpoints that keep txs in **sealed** ingress queues until included or expired (no gossip leakage).
- **Permit / meta-tx** domains. Distinct sign domains to avoid replay across endpoints (`core/encoding/canonical.py`).

### 4.2 Protocol/operator mitigations
- **Bucketed priority + per-sender FIFO** (above): removes pure fee-sniping cross-sender within a bucket.
- **Beacon-seeded tie-breaking**: removes deterministic grindability of last-byte fee bumps.
- **Encrypted ingress** (optional): TLS-only; for private relays, keep tx body hidden from peers until inclusion (careful with liveness).
- **Anti-censorship alarms**: Prometheus metrics on *time-to-first-inclusion* per sender; outliers trigger alerts.

### 4.3 Reorg restraint
- **Retarget & fork-choice** (`consensus/fork_choice.py`, `consensus/difficulty.py`) plus **nullifier TTLs** dampen time-bandit incentives. Operators SHOULD configure max reorg depth alarms and publish them.

---

## 5) Optional Call Auction (Uniform Clearing)

For methods labeled **price-sensitive** (registry in `execution/specs/STATE.md` appendices or DEX app ABI):
1. Builder opens an **Auction Window** (e.g., 100–300 ms local, bounded) when the first such tx is seen while assembling the block.
2. Collect eligible swaps; **simulate** against a **frozen pre-auction state**; compute a **uniform clearing price/route**.  
3. All qualifying orders fill at that price up to pool capacity; **pro-rata** when oversubscribed.  
4. Commit results → produce **Auction Receipt** (Section 7).

**Benefits**: Removes sandwiching space; MEV captured becomes **spread reduction** to users instead of builder rent.

**Notes**:
- Window bounded to protect liveness; non-auction txs continue to fill in parallel, but state touching auctioned pools is staged.
- Deterministic with the beacon seed; reproducible from sidecar metadata.

---

## 6) Censorship & Inclusion Guarantees

- **Inclusion SLO**: At min fee floor, a compliant builder SHOULD include a ready tx within **K blocks** (default K=3) unless conflicting or failing simulation.
- **User-visible reason codes** for non-inclusion (mempool/notify.py): `FEE_TOO_LOW`, `NONCE_GAP`, `CONFLICTS`, `AUCTION_OVERSUBSCRIBED`, `POLICY_DENYLIST` (rare; governance-only).

---

## 7) Transparency & Audits

To make ordering auditable, each block MUST emit **Ordering Metadata**:

- **Ordering Digest**:  

ordering_digest = sha3_256( concat(
builder_id,
beacon_round_id,
bucket_params_hash,
rbf_threshold,
diversity_config_hash,
per-tx: (index, txHash, sender, bucket, tie_key, age_rank, flags)
))

Publish in a **Block Metadata Sidecar** (JSON/CBOR) over RPC: `chain.getBlockByHash(..., includeOrdering=true)`.

- **Auction Receipt** (when used): uniform price, total demand/fill, per-order fill, and seed commitments.

- **Metrics** (`rpc/metrics.py`, `mempool/metrics.py`, `execution/metrics.py`):
- Inclusion latency percentiles by bucket.
- % blocks using auction; average user surplus (simulated vs executed price).
- Censorship alarms (95th percentile inclusion > K blocks).

- **Public Logs**: Builders SHOULD archive ordering sidecars for ≥90 days and allow random audits.

---

## 8) Configuration (Defaults)

| Key | Module | Default | Notes |
| --- | --- | --- | --- |
| `MIN_GAS_PRICE_FLOOR` | `mempool/config.py` | dynamic EMA | Base floor; surge multiplier on pressure |
| `RBF_THRESHOLD` | `mempool/policy.py` | 12% | Same-nonce replacement min delta |
| `BUCKET_THRESHOLDS` | `mempool/priority.py` | VIP/P1/P2/FLOOR | Ratios vs floor |
| `DIVERSITY_MAX_FRACTION` | `mempool/policy.py` | 0.25 | Optional per-sender cap per block |
| `TIE_BREAK_SEED` | `execution/runtime/env.py` | beacon hash | Round-derived |
| `AUCTION_ENABLED` | `execution/config.py` | false | Per-method allowlist |
| `AUCTION_WINDOW_MS` | `execution/config.py` | 200 | Builder-local window |
| `ORDERING_SIDEcars` | `rpc/config.py` | true | Enable sidecar endpoints |

---

## 9) Developer Guidance (DEX & Oracles)

- **DEX**: Require `minReturn`, `deadline`, and **EIP-712-like** sign domain (already in `core/encoding/canonical.py`). Consider **batch-friendly** entrypoints to leverage the call auction.
- **Oracles**: Stagger updates or use **commit-reveal** for sensitive values to reduce predictable back-runs.
- **Liquidations**: If possible, **sealed-bid** style auctions or Dutch auctions to reduce back-run contests.

---

## 10) Known Limitations & Future Work

- **Private Orderflow**: Private relays mitigate leakage but can re-centralize. We recommend plurality of relays and **attestation/transparency** for operators.
- **PBS-like separation**: Proposer/Builder Separation is out of scope for v1 but considered for future; hooks can be added to `rpc/methods/block.py` and P2P topics.
- **Encrypted Mempool**: Full body encryption requires additional DoS protections; research ongoing.

---

## 11) Pseudocode

```text
build_block():
candidates = mempool.snapshot()
buckets = bucketize(candidates, floor, surge)
seed = beacon.current_round_hash()

for B in [VIP, P1, P2, FLOOR]:
  groups = group_by_sender_FIFO(buckets[B])
  for g in groups:
    g.tie_key = sha3_256(seed || g.sender || g.head_tx.hash)

  order = stable_sort(groups, key=(g.tie_key, g.age_bonus))
  for g in order:
    while g.has_ready_tx():
      tx = g.peek()
      if fits_limits(tx) and !touches_auction_state(tx):
        include(tx); g.pop()
      else:
        break

if AUCTION_ENABLED:
  run_call_auction(seed)

finalize_block(ordering_sidecar, auction_receipt?)


⸻

12) Compliance Checklist (Operator)
	•	Bucketed priority configured and active.
	•	Per-sender FIFO enforced.
	•	Tie-break uses current beacon seed.
	•	RBF threshold ≥ 10%.
	•	Diversity cap set (or documented rationale if disabled).
	•	Ordering sidecar published for each block.
	•	Metrics exported (latency, censorship alarms).
	•	(If enabled) Call Auction receipts published.

⸻

13) Summary

This policy narrows MEV by limiting toxic reordering (bucketed priority, per-sender FIFO), randomizing ties with a public beacon, and offering an optional call auction to collapse sandwiching margins into user surplus. Transparency sidecars and metrics make behavior auditable and comparable across builders, enabling pressure toward fairer ordering without compromising liveness.

Version: v1.0. Compatible with Animica node Gate for mempool+execution. Future revisions may add PBS hooks and encrypted ingress options.
