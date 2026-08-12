# Animica L2 — Forced Inclusion & Forced Exits

`l2/bridge.py`. The forced path is what bounds the designated sequencer's
power: a censoring or dead sequencer can delay users, but it cannot trap
funds. It is a load-bearing part of the trust model — see
[SECURITY_ASSUMPTIONS.md](SECURITY_ASSUMPTIONS.md).

## Mechanism

1. **Enqueue via L1.** A user who is being censored (or whose sequencer is
   down) submits their *fully signed L2 transaction bytes* on L1 instead of to
   the sequencer. The bridge indexes it as a `ForcedRequest`
   (`Bridge.enqueue_forced`), keyed by
   `request_id = sha3_256("animica.l2.forced.v1" || raw_l2_tx)` — idempotent,
   so re-submission cannot duplicate it.
2. **Deadline.** Each request carries
   `deadline_height = submit_l1_height + deadline_blocks` (an L1 height). The
   sequencer is expected to include pending forced txs in its normal batches
   (`Bridge.pending_forced`) well before the deadline; doing so marks the
   request included (`mark_forced_included`).
3. **Escape.** If the deadline passes without inclusion
   (`Bridge.overdue_forced(current_l1_height)`), the escape path
   `Sequencer.process_forced(l1_height)` injects the overdue transactions into
   the pipeline — this is what guarantees censorship resistance. Exit-shaped
   requests execute as `FORCED_WITHDRAW` (`TxType` 4), which behaves exactly
   like `WITHDRAW`: burn on L2, unique nullifier, claimable on L1 once the
   containing batch is L1-finalized.

Because the forced tx is an ordinary signed L2 tx, all normal rules still
apply — signature, nonce, balance, fees (a forced withdraw pays the standard
2-unit withdraw weight). Forcing changes *when* a tx must be included, never
*what* it may do.

## Why exits stay safe without trusting the sequencer

- The withdrawal burn is part of a batch whose DA blob lets anyone re-derive
  the post-state; a sequencer cannot fake "user withdrew" or "user did not".
- The withdrawal nullifier (`sha3_256("animica.l2.withdraw.nullifier.v1" ||
  l2_txid)`) is spent exactly once by the L1 claim; replay and double-claim
  are rejected.
- The claim is released only from ANM already locked on the L1 bridge; a claim
  that would exceed locked funds is refused as an invariant violation rather
  than paid.

## Withdrawal states

```
BURNED ──(batch containing the burn reaches L1_FINALIZED)──► CLAIMABLE ──(L1 claim spends nullifier)──► CLAIMED
```

## Deposit safety (the mirror image)

Deposits use L1 confirmation tiers so reorgs can never mint unbacked L2 ANM:

| Tier | Depth | Meaning |
|---|---|---|
| `OBSERVED` | seen in a block | may vanish on reorg; never credited |
| `CONFIRMED` | ≥ 12 blocks (`L1_CONFIRM_DEPTH`) | still never credited |
| `FINALIZED` | ≥ 64 blocks (`L1_FINALITY_DEPTH`) | locked on L1 and claimable on L2 via a protocol-minted `DEPOSIT_CLAIM` |

On an L1 reorg, `rollback_l1_to(safe_height)` drops OBSERVED/CONFIRMED
deposits above the new safe height; FINALIZED deposits are past finality and
are never rolled back. Deposit ids
(`sha3_256("animica.l2.deposit.v1" || l1_txid || beneficiary || amount)`) make
observation idempotent across restarts, and `authorize_deposit_claim` credits
a claim only when a FINALIZED, not-yet-credited deposit matches beneficiary
and amount exactly.

## RPC surface

- `l2_getDeposit(depositId)` — tier + credited flag
- `l2_getWithdrawalProof(nullifier)` — withdrawal record + state
- `l2_getSequencerStatus` — includes the bridge summary (locked/credited/burned/claimed totals, pending forced count)
