# Beacon — Construction, Transcript, and Fork-Choice Interactions

This document specifies the **consensus-relevant** randomness beacon used by Animica. It defines the round schedule, canonical transcript, what a block must commit to, how nodes verify the beacon, and how differing beacons across forks interact with fork choice.

> See also: `randomness/specs/COMMIT_REVEAL.md` (inputs & anti-bias), `randomness/specs/VDF.md` (delay & proof), and code in `randomness/beacon/*`, `randomness/commit_reveal/*`, `randomness/vdf/*`.

---

## 1) Overview

Each beacon **round** aggregates many independent, pre-committed secrets via commit–reveal, derives a seed `X`, runs a VDF of delay `T`, and outputs:

BeaconOut {
round_id,             // uint
aggregate_hash,       // H over all valid reveals for the round (domain-separated)
X,                    // H(“rand/vdf-input” | aggregate_hash | prev_beacon)
T,                    // delay parameter
vdf_y, vdf_pi,        // Wesolowski output and proof (see VDF.md)
commit_root,          // Merkle root of all commitments admitted for the round
reveal_root,          // Merkle root of all reveals included in the aggregate
opt_qrng_tag          // optional (non-consensus) annotation; see §7
}

The **beacon record** for the current round is embedded in the block header/body (implementation: a compact CBOR struct referenced from the header). Verification is deterministic and does **not** depend on mempool/P2P state.

---

## 2) Rounds & Schedule

A round `r` spans two non-overlapping windows measured in **blocks** (default) or **time** (if configured). The canonical computation is provided by `randomness/beacon/schedule.py`.

- **Commit window**: blocks `[B_c(r), ..., B_c(r)+L_commit-1]`
- **Reveal window**: blocks `[B_r(r), ..., B_r(r)+L_reveal-1]`, with `B_r(r) = B_c(r)+L_commit`
- **Finalize height**: the first block at height `B_f(r) = B_r(r) + L_reveal` **may** finalize round `r`.

Parameters live in `randomness/config.py`:
- `L_commit`, `L_reveal` — window lengths
- `reveal_grace` — optional grace (blocks) where late reveals are still accepted if paired with an *early* commitment (see COMMIT_REVEAL.md)
- `T` and modulus/params — VDF settings (see VDF.md)

**Consensus rule:** A block that claims to finalize round `r` **MUST** have height `≥ B_f(r)` and **MUST NOT** include any reveals with admission index outside the configured reveal window (including grace).

---

## 3) Canonical Transcript

All hashes use **SHA3-256** with explicit domain separation.

### 3.1 Commitment leaf
As defined in COMMIT_REVEAL.md:

commit = H(“rand/commit” | addr | salt | payload_hash)

Commit leaves are `[addr, commit]`. The **commitment set** admitted for `r` is summarized by:

commit_root = MerkleRoot( sort_by_bytes( [ (addr, commit) ] ) )

### 3.2 Reveal leaf
A reveal carries the data needed to verify its prior commitment:

reveal_leaf = (addr, salt, payload_hash)
proof_commit ∥ path against commit_root

Verification checks `H("rand/commit" | addr | salt | payload_hash) == commit`.

The **reveal set** for `r` that the proposer includes is summarized by:

reveal_root = MerkleRoot( sort_by_bytes( [ (addr, salt, payload_hash) ] ) )

> **Size control:** Payload bytes themselves are not carried in the consensus record; only `payload_hash` is. (Payloads may be optional attachments verified out-of-band or posted as ordinary transactions if the network enables that pattern.)

### 3.3 Aggregation & seed
Let `R = sort_by_bytes( [ (addr, salt, payload_hash) ] )` over **all valid reveals**
the block claims (and proves) for round `r`.

Aggregate with a bias-resistant fold:

aggregate_hash = H(“rand/aggregate” | fold_xor(H(addr|salt|payload_hash)) )
X = H(“rand/vdf-input” | aggregate_hash | prev_beacon)

`prev_beacon` is the `BeaconOut` of the **previous finalized round** (or a fixed genesis seed at `r=0`).

---

## 4) What a Block MUST Contain to Finalize a Round

When a proposer finalizes round `r` in a block at height `≥ B_f(r)`, the block must embed:

1. `round_id = r`
2. `commit_root` for the round’s commit window
3. `reveal_root` and the **full list** of reveal leaves **or** (size-optimized path) just `reveal_root` plus a **deterministic accumulator transcript** that allows recomputation of `aggregate_hash` from revealed leaves included **in the block**  
   > *Recommended*: include the compact list of reveal leaves. This keeps verification self-contained and prevents ambiguity (§8).
4. `aggregate_hash` computed from exactly those reveals
5. `X` computed from `aggregate_hash` and `prev_beacon`
6. `T`, `vdf_y`, `vdf_pi` verifying `X` per VDF.md
7. Optional `opt_qrng_tag` (non-consensus, ignored by verifiers)

**Block validity checks** (consensus-critical):

- Heights fall within configured windows for `r`.
- Every reveal has a valid Merkle proof to `commit_root` and lies within the round’s commit window.
- `reveal_root` matches the provided reveal list.
- `aggregate_hash` matches the provided reveal list and fold construction.
- `X` recomputes from `aggregate_hash` and `prev_beacon`.
- `(vdf_y, vdf_pi)` passes Wesolowski verification for `(N, X, T)` (VDF.md).
- Domain tags exactly match: `"rand/commit"`, `"rand/aggregate"`, `"rand/vdf-input"`.

If any check fails, the block is invalid.

---

## 5) Node Verification Procedure (summary)

Given a candidate block with a beacon record:

1. Recompute `commit_root` **(carried as a field)** — no recomputation needed unless block also carries the commitment list (debug path).
2. Verify all reveals:
   - Recompute commit for each `(addr, salt, payload_hash)`.
   - Verify `proof_commit` paths against `commit_root`.
3. Recompute `reveal_root` from reveals; must match header field.
4. Recompute `aggregate_hash` via the deterministic fold over the sorted reveals.
5. Derive `X` and verify VDF `(vdf_y, vdf_pi)` (see VDF.md).
6. Accept `BeaconOut` as the finalized output for `round_id`.

All of the above is implemented in `randomness/beacon/finalize.py`.

---

## 6) Fork-Choice Interactions

Different competing blocks at the same height may finalize the **same round `r`** with different reveal sets (and thus different `aggregate_hash`/`X`). This is permitted: each block fully commits to its own beacon transcript.

- **Fork choice prevails:** The canonical chain is selected by the protocol’s fork choice (height/weight, etc.). The **beacon of the canonical block** becomes the round’s canonical output; beacons on orphaned branches are discarded with their branches.
- **Lag for usage:** Any sub-system (e.g., nonce-seed for `H(u)`, tie-breakers, lotteries) MUST use a **lagged** beacon, e.g. round `r-L` with `L≥1`, to prevent last-block grinding advantages. The lag `L` is set in chain params.
- **Deterministic parents:** Because each block commits to its parent hash, the beacon transcript is implicitly tied to a unique ancestry; reorgs consistently switch to the parent’s canonical `prev_beacon`.
- **No cross-fork ambiguity:** Verifiers never combine reveals across forks; only the reveals **present in the block** (with proofs against that block’s `commit_root`) are considered.

---

## 7) Optional QRNG Mixing (non-consensus)

If configured, producers may mix QRNG bytes into an **annotated** beacon (for dashboards/UX). The QRNG annotation is **ignored** by consensus. If a future network wants QRNG to affect consensus, a separate spec/bip with attestation and availability would be required.

---

## 8) Inclusion & Anti-Bias Notes

- The commit–reveal scheme ensures **unpredictability** as long as at least one honest participant commits and later reveals.
- A proposer could attempt to **drop** some reveals to bias the outcome. The recommended mitigation is economic:
  - pay fees/rewards for including valid reveals,
  - expose reveal omission metrics,
  - optionally adopt policy rules in future networks (e.g., minimum-inclusion requirements).
- Consensus **does not** require global completeness (which is not verifiable); it requires **self-consistency**: the aggregate must match the explicit reveal set carried in the block and each reveal must prove to the `commit_root` of the round.

---

## 9) Light Clients

The `randomness/beacon/light_proof.py` object is a compact witness:
- includes `(round_id, X, T, vdf_y, vdf_pi)` and a hash chain to the previous beacon.
- a light client:
  1) trusts a recent checkpoint header,
  2) verifies the chain of beacons via header hashes,
  3) verifies VDF proofs per round.

If a light client needs to audit reveals, it requests the reveal list for the round and verifies the roots and proofs; otherwise it can treat `X` as a pseudorandom beacon once the header is trusted.

---

## 10) Parameters (defaults)

- `L_commit = 16` blocks, `L_reveal = 16` blocks, `reveal_grace = 2`.
- `T` calibrated to ~4 s on reference hardware (actual `T` recorded in the record).
- VDF security per `VDF.md` (RSA-3072 or class groups at ≥128-bit security).
- Lag for downstream consumers: `L = 1` round (minimum), suggest `L = 2` for conservatism.

---

## 11) Pseudocode (block finalization)

```text
finalize_round(r, reveals[], commit_root, prev_beacon, T, N):
  assert height >= B_f(r)
  for each (addr, salt, payload_hash, proof_commit) in reveals:
    c = H("rand/commit" | addr | salt | payload_hash)
    assert verify_merkle(proof_commit, (addr, c), commit_root)

  reveals_sorted = sort_by_bytes([ (addr, salt, payload_hash) for ... ])
  reveal_root = MerkleRoot(reveals_sorted)

  agg = 0
  for leaf in reveals_sorted:
    agg ^= H(addr | salt | payload_hash)
  aggregate_hash = H("rand/aggregate" | agg)

  X = H("rand/vdf-input" | aggregate_hash | prev_beacon)
  (y, pi) = prover_wesolowski(N, X, T)   // off-chain

  // Block carries: r, commit_root, reveals (or reveal_root+transcript), aggregate_hash, X, T, y, pi
  return BeaconOut{r, aggregate_hash, X, T, y, pi, commit_root, reveal_root}

Verifier recomputes everything and runs verify_vdf(N, X, T, y, pi).

⸻

12) Testing & Invariants
	•	Deterministic re-computation of aggregate_hash from block-carried reveals.
	•	Negative tests: wrong proof_commit, wrong aggregate_hash, wrong X, wrong (y, pi), reveals outside window.
	•	Reorg tests: finalize beacon on branch A then on branch B; fork choice selects only the canonical one; downstream consumers use lagged rounds.

⸻

13) Interaction with Consensus & Mining
	•	The beacon output can seed ephemeral randomness in consensus (e.g., parts of H(u)’s nonce domain). Networks must apply a lag L so proposers cannot grind the current round by selectively choosing reveals and block bodies.
	•	VDF work MAY contribute limited ψ-units under PoIES (see consensus policy), but the verification here is identical regardless of how ψ is accounted.

⸻

14) Serialization

All fields are encoded in deterministic CBOR; see randomness/types/core.py and randomness/protocol/* for exact encodings. Map keys use canonical ordering; byte strings are big-endian, fixed width where applicable.

