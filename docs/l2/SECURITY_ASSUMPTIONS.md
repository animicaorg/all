# Animica L2 — Security Assumptions (10.0.0)

This document states precisely what the 10.0.0 L2 protects, what it assumes,
and what remains trusted. It errs on the side of under-claiming: **the
sequencer-operated component is not trustless, and we do not call it that.**

## The trust model in one paragraph

Animica L2 10.0.0 runs a **designated sequencer**. Trust-minimization does not
come from pretending otherwise; it comes from three mechanisms that bound what
a designated sequencer can do:

1. **Data availability + re-derivability.** Every batch's DA blob is anchored
   (published against L1), and anyone can deterministically reconstruct the
   exact transaction list from the blob and re-execute it against the previous
   state to re-derive the committed `new_state_root` (`l2/da.py`,
   `l2/proof.py`). A sequencer cannot commit a state transition that honest
   re-execution rejects without it being detectable by anyone who checks.
2. **The money invariant.** ANM is never created on L2. The bridge
   (`l2/bridge.py`) continuously enforces
   `total_withdrawable_L2_ANM <= ANM_locked_for_L2_on_L1`, and the VALIDITY
   verifier independently checks per batch that the net balance change equals
   `deposited − withdrawn` exactly. A sequencer that violates conservation
   produces a provably invalid batch.
3. **Forced inclusion / forced exits.** Users can enqueue transactions
   (including withdrawals) via L1. A request the sequencer fails to include by
   its deadline is force-processed (`Bridge.overdue_forced`,
   `Sequencer.process_forced`). Censorship can delay, not confiscate. See
   [FORCED_EXITS.md](FORCED_EXITS.md).

## What is trusted in 10.0.0 (honest list)

| Trusted party / assumption | What they could do | What bounds them |
|---|---|---|
| **Sequencer liveness** | Stop producing batches; soft confirmations stall | Forced-exit path via L1 lets users withdraw without the sequencer's cooperation (bounded by the forced deadline + L1 finality) |
| **Sequencer ordering** | Reorder, front-run, or delay transactions before batch close | Nothing in 10.0.0 prevents ordering discretion inside a batch. This is a real power of the operator. Forced inclusion bounds indefinite delay only |
| **Soft confirmations** | A `SOFT_CONFIRMED` acknowledgment is a promise by the sequencer, not a proof | Treat soft confirmations as trusted until `BATCHED`+`PROVEN`, and as final only at `L1_FINALIZED` (see [TRANSACTION_LIFECYCLE.md](TRANSACTION_LIFECYCLE.md)) |
| **L1 anchoring interpretation (pre-fork)** | Below L1 height `FORK_L2_ANCHOR_HEIGHT = 80_000`, anchoring txs are opaque memos; watchers index them off-consensus | At/above the `FORK_L2_ANCHOR` height, anchoring becomes **consensus-interpreted**: full nodes advertising 10.0.0 validate commitments. Until then, the mapping "this L1 tx anchors that batch" is a convention checked by anyone running the verifier, not by L1 consensus |
| **Watchfulness** | An invalid commitment is *detectable* by re-execution, but somebody has to run the check | `l2_verifyBatch` / the re-execution verifier are cheap enough for any full node to run on every batch; the design assumes at least one honest checker exists |
| **Animica L1 itself** | L2 inherits L1's consensus security for finalized anchoring and deposits | Standard L1 assumptions; deposits credit only after `L1_FINALITY_DEPTH = 64` blocks so L1 reorgs cannot mint unbacked L2 ANM |

Things the sequencer **cannot** do, even maliciously:

- **Forge a transaction.** Every user-signed tx carries an ML-DSA-65 signature
  over a domain-separated preimage that includes the L2 chain id and nonce; the
  verifier rejects any batch containing an invalid signature or a
  pubkey/sender mismatch.
- **Mint ANM.** Conservation is checked by the bridge invariant and re-checked
  independently by the verifier per batch.
- **Replay a tx.** Nonces are strict; withdrawals are nullifier-protected;
  deposit claims are matched one-to-one against finalized L1 deposits
  (`authorize_deposit_claim` requires exact beneficiary + amount and
  not-yet-credited).
- **Steal via a fake deposit.** Deposits credit only from FINALIZED L1 events;
  OBSERVED/CONFIRMED deposits are never credited and are dropped on reorg
  (`rollback_l1_to`).
- **Double-release a withdrawal.** The L1 claim spends the nullifier exactly
  once; a claim exceeding locked funds raises an invariant violation and
  refuses rather than releasing unbacked ANM.

## Settlement modes

`ANIMICA_L2_SETTLEMENT_MODE` selects the proof backend (`l2/proof.py`):

### VALIDITY (default) — `ReExecutionValidityBackend`

The proof for a batch is its DA blob plus the public inputs (chain id, batch
number, prev/new state roots, transactions/receipts/escrow/data roots, fee /
deposit / withdrawal aggregates). Verification:

1. checks the DA blob reconstructs and hashes to the committed `data_root`;
2. checks the prior state matches `prev_state_root`;
3. re-verifies **every** user signature (batched ML-DSA-65) and the
   pubkey→sender binding;
4. re-executes the batch deterministically and asserts every committed root
   and aggregate;
5. asserts no ANM was minted: Δ(total balances) == deposited − withdrawn
   (fees move to the L2 treasury *inside* L2 and cancel).

**Be precise about what this is:** it is a *real, anyone-can-run verifier* —
its security is identical to a full node re-executing the batch — but it is
**not succinct and not zero-knowledge**. Verification cost is linear in the
batch. We ship it because a succinct ZK proof of the full post-quantum
(ML-DSA-65) signature stack is not something that can honestly be shipped as
production cryptography today, and we never fake a proof.

**The ZK slot.** The `ProofBackend` interface and `ProofPublicInputs` are
deliberately backend-independent. A future `ZkValidityBackend` — a PQ-friendly
succinct proving stack — drops into the same slot with the same public inputs
and the same `verify` contract, changing nothing above it. Until that exists,
VALIDITY mode means "validity by universal re-execution", not "ZK rollup".

### OPTIMISTIC — `OptimisticBackend`

Commit now; allow a challenge window (default 100 batches) during which anyone
can submit the re-execution disproof (a challenger runs the *same* verifier to
produce a fraud proof). This mode presupposes a **bonded** sequencer with
slashing tracked by the bridge/settlement layer; it exists for migration
scenarios and adds a synchrony assumption (an honest challenger must act within
the window). Its detection machinery is identical to VALIDITY; only *when*
verification happens differs.

### DEV — `DevBackend`

No verification at all. `verify` returns `True` unconditionally. **Not
trust-minimized in any sense.** Local development only; never run this
off-devnet.

## Cryptographic assumptions

- **ML-DSA-65** (FIPS 204, the L1-canonical `0x1003` scheme) is unforgeable.
  It is the only accepted scheme for value-moving txs (`SigScheme.ML_DSA_65`).
- **SHA3-256** is collision- and preimage-resistant: it underpins txids,
  the SMT (`l2/state.py`), batch ids, `data_root`, deposit ids, and
  withdrawal nullifiers.
- Domain separation is used for every hash/signature context
  (`animica.l2.tx.v1`, `animica.l2.smt.leaf/node`, `animica.l2.proof.pi.v1`,
  `animica.l2.deposit.v1`, `animica.l2.withdraw.nullifier.v1`, …) so no
  artifact is valid in a context it was not created for.

## Operational hazards (not protocol guarantees)

- The store's crash-safety guarantee is per-machine (WAL + atomic snapshot
  commit, `l2/store.py`); operators must still back up `data_dir`.
- `check_invariant` failures are treated as **fatal**: halt settlement rather
  than risk unbacked withdrawals. Do not "fix" this by loosening the check.
- Fee-schedule constants are consensus: a sequencer that deviates produces
  batches the verifier rejects.
