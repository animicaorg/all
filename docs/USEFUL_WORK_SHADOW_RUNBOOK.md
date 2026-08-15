# Useful-work proof verification — shadow-mode rollout runbook

Operational procedure for `FORK_USEFUL_WORK_VERIFY` (`core/network_params.py`,
`consensus/useful_work_verify.py`, `core/chain/block_import.py`).

The security properties, and the things this rule explicitly does **not**
provide, are in the module docstring of `consensus/useful_work_verify.py` under
`FORGERY VERDICT` and `RESIDUAL WEAKNESSES`. Read those before arming anything.
This file is the procedure only.

**The one-sentence honest summary, so it cannot be lost in the procedure:** this
rule does *not* force a miner to run a model. Check 2 requires the receipt's
worker to be the block's coinbase, so every valid proof is self-signed by the
miner; a null worker with a fabricated `outputDigest` passes. Do not describe it
to operators or miners as "serve or don't mine".

A full operator rollout runbook, including the live-node deployment mechanics and
the go/no-go for the *required-proof* fork, lives at
`/root/animica-growth-staging/RUNBOOK-USEFUL-WORK.md`.

---

## 0. State on arrival

* The activation height for mainnet is **absent from the table on purpose**.
  `is_fork_active(FORK_USEFUL_WORK_VERIFY, h, chain_id=1)` is `False` at every
  height. Nothing in this release can fire on mainnet without an explicit env
  var. Testnet and devnet activate at 0 (safe — see step 1).
* The rule is **presence-gated**: a block carrying no proofs is valid at every
  height. Live mainnet blocks carry no proofs, so an armed gate is a no-op today.
* The rule is a pure **tightening**: Σψ is recomputed and logged, never credited
  toward Θ. It can only reject a block PoW already accepted.

## 1. Why activating this cannot halt the chain

| Property | Consequence |
|---|---|
| Presence-gated | A block with `proofs: []` is never rejected. 95,004/95,004 stored blocks qualify. |
| No ψ credit | Acceptance stays `header_hash <= target(Θ)`. Miners need no change. |
| Grandfathered | Blocks below the activation height are never re-validated. |
| Fail-closed only on unknown proof *types that are present* | Nothing present ⇒ nothing to fail. |

The rule that **requires** a proof is a different, later fork. It is a 100% hard
cutover for every miner and must not share this height.

## 2. Arm shadow mode (per node)

```bash
# on each node, in the node's environment (NOT bash-sourced; set it where the
# process env is defined for that deployment)
ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT=<a height BELOW the current head>
ANIMICA_USEFUL_WORK_SHADOW=1
```

Setting the height below the head makes the gate evaluate every incoming block
immediately, which is what produces telemetry. The shadow env makes it
observe-only: it logs the verdict and returns `None`.

Restart the node so the env is picked up. The rule records **no state in any
mode**: replay is decided by re-deriving nullifiers from the block's own
ancestors, so a shadow node and an enforcing node compute the same verdict from
the same chain.

## 3. What to watch

Logger `animica.chain.block_import`.

| Log line | Meaning | Action |
|---|---|---|
| `useful_work SHADOW: no proofs` (debug) | The expected steady state today. | none |
| `useful_work SHADOW: block carries proofs (accepted)` (warning) | **The signal.** A miner has upgraded and is attaching evidence that verifies. | start counting |
| `useful_work SHADOW: block would be rejected …` (error) | A proof was attached and failed. The `reason` names the exact check. | investigate before enforcing |

Every line carries structured `extra`: `height`, `proofs`, `psi_micro_total`,
`h_micro`, `theta_micro`, `s_micro`, `theta_ok`, `failures[]`, `policy_digest`.

Reasons that indicate a **node-local** problem rather than a bad block. Each of
these means *this node would reject a block another node accepts*, i.e. a split
under enforcement. All must read **zero** across the whole fleet:

| Reason | Meaning | Fix |
|---|---|---|
| `payment_status_unknown` | The payment tx was found in the ancestry but this node has no execution receipt for it. **The known blocker** — headers commit `receiptsRoot = 0`, so status is not chain-derived. Expected on any node that snapshot-synced past the range. | Commit receipts in a header root, or replace the payment check. Not fixable by configuration. |
| `nullifier_scan_incomplete` | A block body inside the `payment_max_age` (64-block) window is missing, so the replay scan could not complete. | Stop pruning bodies inside the window. |
| `payment_unresolved` | The payment tx is in no ancestor of this block within the window. Usually a genuinely bad proof; investigate before assuming node-local. | — |
| `payment_not_in_ancestry` | A `ChainView` answered without proving ancestry. Should be unreachable with the shipped adapter. | Bug — report it. |
| `anchor_unresolved` | The ancestor at `anchorHeight` is not reachable locally. | Check body retention. |
| `worker_sig_backend_unavailable` | This build cannot verify ML-DSA-65 at all (missing `animica._vendor.dilithium_py_v2`). Every proof fails **on this node only**. | Reinstall. A loud one-shot `log.error` also fires the first time a proof-carrying block arrives. |
| `verifier_unavailable:*` | This build's verifier does not import. | Fix the build. |

Reasons that indicate a **bad block** (safe to reject, no fleet action):
`worker_sig_invalid`, `worker_not_miner`, `self_dealing_same_identity`,
`payment_reverted`, `payment_below_floor`, `payment_after_anchor`,
`payment_too_old`, `nullifier_replay_work`, `nullifier_replay_payment`,
`slot_mismatch`, `anchor_hash_mismatch`, `structure:*`, `unsupported_proof_type:*`.

## 4. Go / no-go for enforcement

Do **not** unset the shadow env until all of the following hold:

1. A non-zero `proofs` count has been observed on real blocks for a sustained
   window (days, not blocks). Until then there is nothing to enforce and
   enforcement buys nothing.
2. Zero occurrences of every node-local reason in the table above, across
   **every** node in the fleet, not just one.
3. **`payment_status_unknown` is structurally impossible, not merely observed at
   zero.** This is now the single biggest blocker. Payment execution status is
   read from the node-local receipt side-table (`b"\x25" || tx_hash`) because
   headers commit `receiptsRoot = 0`; a node that snapshot-synced past a range
   has no entry and fails closed while an executing node accepts. Either commit
   receipts in a header root so status is chain-derived, or replace the payment
   check with something verifiable from block bodies alone. Observing zero in a
   window where no proofs exist proves nothing.

   (The *previous* biggest blocker — an in-memory `MemoryNullifierStore` that was
   written for side-chain blocks, never unwound on reorg and emptied by a restart
   — is FIXED. Replay is now decided by re-deriving the tags from the block's own
   ancestors, so there is no store, nothing to unwind, and nothing to forget.)
4. `ANIMICA_AICF_REQUIRE_SETTLEMENT=1` remains set, and
   `rpc/methods/aicf_jobs.submit_inference_job` has been changed to actually
   verify the payment tx's `to` and `value` (today neither is checked, and
   `aicf.workerRegister` / `aicf.workerSubmitResult` accept an unauthenticated
   `address` string). Enforcing a rule that pays out on receipts before those are
   closed funds its own forgery.
5. The `payment_floor_base_units` has been set deliberately by governance, with
   its consequence written down. At the shipped default of 1 ANM against a
   ~150 ANM miner subsidy, self-dealing is taxed ~0.7%, not prevented.

## 5. Promote to enforcing

```bash
# remove the shadow env everywhere FIRST, in one coordinated pass
unset ANIMICA_USEFUL_WORK_SHADOW
```

A shadow node and an enforcing node disagree about a block carrying an invalid
proof. A mixed fleet is a split waiting for the first such block, so the two
states must not coexist longer than a rolling restart.

Only after the fleet is uniformly enforcing via env should a height be **pinned
in code** in `ACTIVATION_HEIGHTS_BY_NETWORK[("mainnet", 1)]`, so nodes agree
without configuration. Pin a height with runway (well above the head at ship
time), and ship it as its own release.

## 6. Rollback

Unset `ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT` and restart. The gate returns
`None` at every height and the node behaves exactly as it did before this
release. There is **no persisted state to unwind at all** — the rule writes
nothing, by design.

Once a height is pinned in code, rollback is the env override again
(`ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT` to a very large number), applied to the
whole fleet at once.

## 7. Deployment note

The live node runs `/root/animica-mainnet-601`, mounted read-only at `/app` —
**not** `/root/animica`. Any code change here reaches the node only through
whatever process syncs that worktree. Nothing in this release is deployed by the
builder.
