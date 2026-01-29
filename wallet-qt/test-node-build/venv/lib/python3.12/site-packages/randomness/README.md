# Randomness beacon

A small, deterministic module that produces an *unpredictable*, *unbiasable* per-round beacon used across Animica:
- consensus tie-breakers and fork-choice determinism,
- AICF provider assignment shuffles,
- VM `random(bytes)` syscall mixing (when the beacon is available),
- sampling seeds (e.g., DA auditors) and test fixture generation.

The beacon follows a **commit → reveal → VDF → mix** pipeline and is fully post-quantum.

---

## TL;DR

Each round *r*:
1. Participants commit `C_i = H(dom || r || pk_i || reveal_i)` (PQ-signed) during the **commit window**.
2. They later **reveal** `reveal_i` during the **reveal window**; invalid/missing reveals are penalized.
3. The chain derives an interim seed `σ_r` by mixing all valid reveals.
4. A *time-delay proof* (VDF; Wesolowski) is computed over `σ_r` for `T` steps to mitigate last-revealer bias.
5. The final beacon `B_r = H(dom_mix || r || σ_r || vdf_proof_r)` is fixed and consumed by the rest of the system.

---

## Goals

- **Unpredictability:** No party can predict `B_r` ahead of the reveal/VDF steps.
- **Unbiasability:** With ≥1 honest reveal, adversaries cannot bias the output beyond negligible advantage.
- **DoS tolerance:** Failure or withholding by a subset of participants should not stall the beacon.
- **PQ security:** Commit/reveal authentication uses Dilithium3 / SPHINCS+; hashing is SHA3-256; VDF verification is classical.

---

## Actors & components

- **Participants:** Block producers (miners) for the round and optionally any staked actors allowed by policy.
- **Commit store:** Index `{round → set(Commitment)}` (on-chain receipts; prunable).
- **Reveal store:** Index `{round → set(Reveal)}` with validity flags and penalties.
- **VDF engine:** Verifies Wesolowski proofs (see `proofs/vdf`); time parameter `T` tuned in `spec/params.yaml`.
- **Mixer:** Deterministic aggregator that produces `σ_r` and `B_r` from valid reveals and the VDF.

---

## Threat model

Adversary may:
- Control up to a fraction of block producers in the round.
- **Grind:** Try many candidate blocks to bias inclusion of commits/reveals.
- **Withhold:** Skip revealing to nudge the distribution (last-revealer bias).
- **Equivocate:** Publish multiple different reveals matching conflicting commits.
- **Censorship/DoS:** Delay certain reveals within a window.

Mitigations:
- Commits are PQ-signed, round-bound, and **domain-separated**: `dom = "animica/beacon/v1"`.
- **Equivocation-proofs**: multiple reveals for the same `(r, pk)` slash.
- **Withholding penalties**: missing reveal with an accepted commit incurs a fine.
- **VDF delay**: forces an additional time step after the reveal set is known, reducing residual bias of the last revealer.
- **Inclusion rules**: miners must include pending reveals up to block gas/byte caps; omission above a threshold scores negatively in miner fairness metrics.

---

## Round lifecycle

Rounds align with block heights unless configured otherwise (`randomness.roundPeriod`):
- `r = ⌊height / roundPeriod⌋`.

Time is partitioned per round:

[ commit window ] [ reveal window ] [ VDF window (T steps) ] → finalize

### 1) Commit
- Message: `Commit { round, pk, algo_id, C = H(dom || round || pk || reveal), sig_pq }`
- Accepted if:
  - `round == currentRound`,
  - `sig_pq` verifies for `(round, pk, C)`,
  - first commit per `(round, pk)` (duplicates ignored).
- Stored and gossiped; anchored by inclusion in blocks during the window.

### 2) Reveal
- Message: `Reveal { round, pk, reveal, sig_pq }`
- Valid if:
  - A matching commit exists and `H(dom || round || pk || reveal) == C`,
  - `sig_pq` verifies for `(round, pk, reveal)`,
  - within the reveal window.
- Invalid/missing → penalties recorded; equivocations → slashing.

### 3) Mix (interim)
Given the set of valid reveals `R_r = {reveal_i}`, compute:

σ_r = Mix(R_r) = H^*(dom_mix || round || sort_by_pk(reveal_i …))

where `H^*` is a folding hash (iterative SHA3-256) over the ordered list. (Ordering by `pk` ensures determinism.)

If `R_r` is empty, fallback:

σ_r = H(dom_fallback || round || H(header_r) || H(header_{r-1}))

(Uses block header entropy to preserve liveness.)

### 4) VDF delay
Run a VDF for `T` steps seeded by `σ_r`:
- `y_r = VDF_Eval(σ_r, T)`, `π_r = proof`.
- On-chain: only the proof `π_r` and output `y_r` are needed; verification is cheap.

### 5) Finalization

B_r = H(dom_beacon || round || σ_r || y_r || π_r)

Anchored in the first block after the VDF window closes (or immediately if `T=0` on small/dev nets).

---

## Cryptographic details

- **Hash:** SHA3-256 everywhere, with explicit domain tags:
  - `dom`, `dom_mix`, `dom_beacon`, `dom_fallback`.
- **Signatures:** Dilithium3 (default) or SPHINCS+; the signer’s `algo_id` is part of the signed message and stored.
- **VDF:** Wesolowski over a class group or RSA modulus (network-selectable). Parameters live in `spec/params.yaml`.
- **Aggregation:** Ordered fold via hash (not XOR) to avoid linearity exploits.
- **Domain separation:** Round number, chain id, and version are included in every hash transcript.

---

## Parameters (controlled by `spec/params.yaml`)

- `roundPeriod` — blocks per randomness round (default: 1).
- `commitWindowBlocks` — length of commit phase.
- `revealWindowBlocks` — length of reveal phase.
- `vdfSteps` (`T`) — difficulty of the VDF delay (can be 0 on devnet).
- `penalties` — withhold/equivocation fines and slashing fractions.
- `minRevealsForBonus` — optional miner credit for including ≥N reveals.

---

## Fallbacks & failure modes

- **No valid reveals:** Use fallback seed from recent headers; flag `degraded=true`.
- **Partial participation:** Proceed with any subset; penalties apply to non-reveals with prior commits.
- **Reorgs:** Commits/reveals are keyed by `(round, pk)`; on reorg, re-index and re-evaluate validity against the new history. Finalized `B_r` is immutable once anchored.
- **Late reveals:** Ignored for `r` but recorded (no credit, no penalty if commit absent).

---

## Interfaces

- **Adapters**
  - `capabilities.adapters.randomness.read_beacon(round|head)` — used by VM syscall `random(bytes)` to mix in `B_r`.
  - `aicf.integration.randomness_bridge` — shuffles provider lists with `B_r`.
- **P2P topics**
  - `beacon/commit/v1`, `beacon/reveal/v1`, `beacon/vdf/v1`.
- **RPC (read-only)**
  - `randomness.getHead()` → `{round, beacon, finalized}`
  - `randomness.getRound(r)` → `{commits, reveals, σ_r, vdf, beacon, status}`

All messages are CBOR with deterministic encoding and PQ signature envelopes.

---

## Storage & retention

- Commit/reveal receipts kept for `k` rounds (configurable) for auditability; VDF proofs retained longer.
- Indexes by `round`, `pk`, and `status` to support slashing and metrics.

---

## Security notes

- **Grinding:** Commit must precede the reveal window; commit contents are bound to the round and key; miner cannot adaptively pick commits after seeing others’ reveals.
- **Last-revealer advantage:** Delayed finalization via VDF reduces adaptive bias; penalties for withholding raise cost.
- **Censorship:** Inclusion metrics and peer gossip ensure reveals propagate even if one miner is adversarial.

---

## Testing

- Property tests: invariance under permutation of reveals (ordering neutrality via sort).
- Adversarial tests: withheld reveals, equivocations, empty rounds, reorgs.
- Vectors: canonical transcripts for `(C, reveal, σ_r, y_r, π_r, B_r)` across parameter sets.

---

## Usage in other modules

- **Consensus:** Tie-breakers and slot lotteries read `B_r`.
- **Capabilities:** `random()` syscall mixes `B_r` with the call-local seed to produce deterministic-yet-unpredictable bytes per block.
- **AICF:** Assignment shuffles and traps schedule seeds derive from `B_r`.

