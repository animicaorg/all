# Commit–Reveal — Binding Rules, Anti-Griefing & Bias Analysis

This note specifies the consensus-relevant rules for the **commit–reveal** phase of Animica’s randomness beacon, plus the rationale behind the anti-griefing measures and an analysis of adversarial bias. It complements the module code in `randomness/commit_reveal/` and the invariants listed in `randomness/specs/README.md`.

---

## 1) Model & Objects (recap)

- **Rounds.** Each round `r` has fixed windows (block-time): `[commit_open_r, commit_close_r)` and `[reveal_open_r, reveal_close_r)`.
- **Commitment.**  
  `C = H("rand/commit" | addr | salt | payload)` using SHA3-256.  
  Domain separation ensures unambiguous binding to the randomness subsystem.
- **Reveal.** Tuple `(addr, salt, payload)` is valid iff `H("rand/commit" | addr | salt | payload) == C` previously recorded for round `r`.
- **Aggregation.** Let `R` be the set of *valid* reveals for round `r`. The aggregator folds deterministically:  
  `A = Comb("rand/aggregate", R)` (reference combiner: hash-xor fold; order-independent).
- **VDF input.** `X = H("rand/vdf-input" | A | prev_beacon)`.

> All encodings are deterministic CBOR at the wire; see the CDDL in `randomness/` for exact shapes.

---

## 2) Binding & Acceptance Rules (consensus-critical)

1. **Timing.**
   - Commits **must** arrive before `commit_close_r`.
   - Reveals **must** arrive in `[reveal_open_r, reveal_close_r)`.
   - Early/late messages are **rejected** and **not** aggregated.

2. **Uniqueness / De-dup.**
   - A given `(addr, C, r)` can reveal **at most once**.
   - Multiple commits that hash to the **same** `C` are a no-op; only the first record is kept.
   - A node **may** track at most `M_commits_per_addr_per_round` distinct **commitments** per `addr` (see §4). Default: **1**.

3. **Validity.**
   - Reveals must verify `C == H("rand/commit"|addr|salt|payload)` and must reference a **recorded** `C` for the **same** round `r`.
   - `payload` byte length is capped (config) to prevent resource abuse; reveals over the cap are invalid.

4. **Aggregation determinism.**
   - Aggregation is **set-based**: order, duplication, or gossip topology must not change `A` provided the same validity filter is applied.
   - Missing or withheld reveals are simply **absent** from `R` (no special casing).

5. **Reorg safety.**
   - Commit/reveal acceptance is part of block execution. Reorgs that roll back acceptance **also** remove those entries from `R`. Final `A` is recomputed on the adopted chain.

6. **No threshold gating.**
   - Finalization of a beacon round **does not** require a minimum number of reveals. (Thresholds incentivize censorship & griefing; see §4.)

---

## 3) Rationale: Why these bindings?

- **Domain separation** avoids cross-protocol replay.
- **Commit before reveal** removes adaptive selection of `payload` after seeing honest reveals.
- **Set-based aggregation** prevents “last reveal wins” games about ordering.
- **No reveal threshold**: if finalization required ≥ *t* reveals, an attacker could censor *t-1* honest reveals and stall progress indefinitely.

---

## 4) Anti-Griefing Measures

Adversaries may try to:
- Spam commits/reveals (DoS).
- Selectively reveal (abort) to bias the beacon.
- Censor others within the reveal window.

Mitigations:

1. **Per-origin quotas.**
   - `M_commits_per_addr_per_round = 1` (default). Can be raised to a small integer where desirable, but bias rises with `M` (§5).
   - Optional **stake-gated multiplier**: higher `M` only with bonded stake; slashed on misbehavior hooks.

2. **Economic friction (optional, policy-driven).**
   - **Bonds/fees** on commits refunded at reveal reduce spam. (Non-consensus hook in `commit_reveal/slashing.py`.)
   - **Per-block RPC/mempool token buckets** for commit/reveal endpoints.

3. **Size & CPU caps.**
   - `payload` max length cap; bounded hashing work.  
   - Reject oversized messages at mempool admission.

4. **Window sizing.**
   - Reveal window wide enough to blunt transient censorship but short enough to bound liveness. Typical: `reveal_len ≈ commit_len` or slightly shorter.

5. **No threshold gating.**
   - Prevents stall attacks. Withholding only reduces `|R|` and thus adversarial influence; it cannot force an **invalid** aggregation.

6. **Auditability.**
   - Nodes retain per-round indexes `(addr → commits/reveals)` for K rounds to spot spam/censorship patterns.

---

## 5) Bias Analysis (selective-abort under XOR)

Let the beacon preimage be:

A = XOR(Honest_1, …, Honest_h, Adv_1, …, Adv_m)

where `Honest_i` are independent, uniformly random 256-bit strings, and `Adv_j` are adversarial 256-bit strings **fixed** at commit time. During the reveal window the adversary can choose any subset `S ⊆ {1..m}` to include (selective abort).

**Key facts (over GF(2)^256):**
- The adversary’s reveals span a subspace of dimension `rank ≤ m`.
- After honest reveals are known, to force a specific `k`-bit prefix, the adversary needs a subset `S` whose XOR equals a target `Δ` that fixes those `k` bits.

**Upper bound on forcing probability for a `k`-bit predicate:**
- If `rank < k`, the best success probability is at most `2^{rank - k}`.  
  (Intuition: there are `2^rank` achievable XOR values from `m` commitments; exactly a `2^{rank-k}` fraction satisfy a given `k`-bit prefix.)
- If `rank ≥ k`, the adversary **can** realize any `k`-bit prefix with high probability, but **cannot** in general fix all 256 bits unless `rank = 256` (full rank), which requires *m ≥ 256* independent commitments.

**Implications & policy:**
- With default `M=1`, per-address, and realistic Sybil friction (stake/fees/rate-limits), practical `rank` is small.
- Example: `m = 20` independent commitments trying to force `k = 16` bits yields an upper bound of `2^{20-16} = 16×` baseline success. Baseline for a 16-bit prefix is `2^-16`; boosted success ≈ `16 · 2^-16 ≈ 2.44e-4`.  
  This is **still small**, and drops rapidly with smaller `m` or larger `k`.

**Why XOR + VDF?**
- XOR is *unbiased* if at least one honest reveal is present and the adversary cannot adapt the *value* of their inputs post-commit (only reveal or abort). Selective abort enables at most subspace selection (as above).  
- The **VDF** does not prevent selective abort (which happens pre-VDF) but ensures that post-aggregation manipulation is infeasible and that beacon availability is decoupled from reveal-phase compute races.

**Further hardening (optional, non-default):**
- **Commit caps by identity class** (e.g., per stake bucket) to reduce `rank`.
- **Prefix sealing:** derive the beacon as `H(A)` and use only suffix bits for protocol choices, so small prefix bias has negligible effect.
- **Delay-reveal tickets:** escrow small bonds burned if never revealed.

---

## 6) Pseudocode (consensus sketch)

```text
on_commit(addr, C, r, block_height):
  assert commit_open_r <= block_height < commit_close_r
  assert per_addr_commits[r][addr].size < M_commits_per_addr_per_round
  store_commit(r, addr, C)

on_reveal(addr, salt, payload, r, block_height):
  assert reveal_open_r <= block_height < reveal_close_r
  assert len(payload) <= MAX_PAYLOAD
  C = H("rand/commit" | addr | salt | payload)
  assert commit_exists(r, addr, C) and not revealed_before(r, addr, C)
  mark_revealed(r, addr, C)
  add_to_R(r, H("rand/reveal" | addr | payload))  # domain-separated input to combiner

finalize_round(r):
  A = Comb("rand/aggregate", R[r])
  X = H("rand/vdf-input" | A | prev_beacon)
  accept first valid VDF proof π for (X, T)
  beacon_r = H("rand/beacon" | X | π | optional_qrng_mix)

Note: Implementations may hash payload during aggregation (as shown) to decouple combiner cost from raw payload size.

⸻

7) Parameters (suggested defaults)
	•	M_commits_per_addr_per_round = 1
	•	MAX_PAYLOAD = 64 bytes (sufficient entropy; implementation cap may differ)
	•	Window lengths: see PARAMS.md; ensure reveal window ≥ network round-trip + jitter budget.

⸻

8) Security Checklist
	•	Domain tags audited: "rand/commit", "rand/reveal", "rand/aggregate", "rand/vdf-input", "rand/beacon".
	•	Window enforcement tested (early/late rejection).
	•	Per-origin quotas & RPC rate-limits enforced.
	•	Aggregation order independence verified.
	•	Reorg recomputation correct.
	•	Storage retention ≥ K rounds for audits.

