# Randomness Overview — Commit→Reveal → VDF → optional QRNG mix

This document gives a high-level, implementation-oriented overview of the on-chain randomness beacon used by Animica. The beacon is produced in **rounds** and follows three stages:

1) **Commit–Reveal** (bias resistance)  
2) **VDF Finalization** (unpredictability until a fixed delay)  
3) **Optional QRNG Mixing** (extra entropy when available)

See also:
- Protocol details: `randomness/specs/BEACON.md`, `randomness/specs/COMMIT_REVEAL.md`, `randomness/specs/VDF.md`, `randomness/specs/QRNG.md`
- RPC surfaces: `randomness/rpc/methods.py` (`rand.getParams`, `rand.getRound`, `rand.commit`, `rand.reveal`, `rand.getBeacon`, `rand.getHistory`)
- Types & state: `randomness/types/*`, storage: `randomness/store/*`
- Test vectors: `randomness/test_vectors/*`

---

## Goals

- **Public randomness** usable by contracts and off-chain systems.
- **Bias resistance** against strategic last-reveals or selective withholding.
- **Unpredictability until deadline:** VDF forces a minimum delay before final output.
- **Light-client verifiability:** small proof, no trusted servers, header-bound.
- **Optional entropy mixing** from a QRNG source without weakening the transcript.

---

## Round Lifecycle (at a glance)

Each round `r` runs across fixed windows parameterized by network config:

Commit Window  →  Reveal Window  →  VDF Finalization  →  Beacon(r) ready
[Tc]                 [Tr]                [Tv]

### 1) Commit
Participants submit a binding commitment:

C = H(domain_commit || round_id || addr || salt || payload)

- `payload` is arbitrary bytes contributed by the participant.
- Commits are accepted only within the commit window of round `r`.

### 2) Reveal
After the commit window closes, participants must reveal:

reveal = (addr, salt, payload)
verify_commit(H(…)) == C previously stored

Valid reveals are aggregated into an **intermediate seed**:

agg = Aggregate(reveals)               # e.g., hash-then-xor fold with domain tags

Aggregation is defined in `randomness/commit_reveal/aggregate.py` and is *bias-resistant* (order-independent, domain-separated).

### 3) VDF Finalization (Wesolowski)
We derive the VDF input from the round transcript:

vdf_in = H(domain_vdf_in || round_id || prev_beacon || agg)
(vdf_out, vdf_proof) = VDF_Prove(vdf_in, params)         # off-chain prover, on-chain/light verification
verify(vdf_in, vdf_out, vdf_proof, params) == true

### 4) Optional QRNG Mix
If a QRNG provider is enabled and data is available for round `r`, we **extract** and **mix**:

q = Extract(qrng_bytes)                # e.g., H(domain_qrng || qrng_bytes)
mix = H(domain_mix || vdf_out || agg)  # deterministic transcript hash
beacon = mix XOR q                     # extract-then-xor

If no QRNG bytes are present, `beacon = mix`. Mixing never reduces entropy when extraction is modeled as a PRF.

The finalized **BeaconOut** for round `r` is stored:

BeaconOut {
round_id,
agg,                 # commit-reveal aggregate
vdf_in, vdf_out, vdf_proof,
qrng_mixed: bool,
beacon,              # final 32/64 bytes
hash: H(domain_beacon || …)  # canonical digest
}

---

## Participants & Roles

- **Committers:** any address able to submit `rand.commit` then `rand.reveal`.
- **Prover(s):** runs VDF prover off-chain during `[Tv]`. Anyone can provide a valid proof.
- **Full nodes:** validate commits/reveals, check VDF proofs, produce/verify BeaconOut.
- **Light clients:** verify headers + VDF proof + (optionally) QRNG attest chain.

---

## Security Properties

- **Binding (Commit):** A reveal must match a prior commitment (collision resistance of `H`).
- **Hiding (Commit phase):** Without `salt||payload`, `C` is indistinguishable under `H`.
- **Bias resistance (Aggregate):** No reordering advantage; counting **timeouts as missing** prevents slow-loris reveal games. Optional slashing is available via policy hooks.
- **Unpredictability:** Prior to completing the VDF, `vdf_out` is infeasible to learn, even if `agg` and `prev_beacon` are known.
- **Unbiasable mixing:** QRNG is mixed by extraction (modeled as PRF) then XOR; if QRNG is bad or absent, security reduces to commit-reveal+VDF.
- **Header binding:** The round transcript (and thus `beacon`) is bound to the block headers that close the windows via `round_id` and parameters included in consensus state.

Threats & mitigations:
- **Selective withholding of reveals:** reveals must arrive within `[Tr]` or are excluded.
- **Adaptive reveal timing:** strict deadlines; inclusion is window-gated.
- **Eclipse:** multiple transports and peers; re-audit after connectivity changes.
- **Bad QRNG:** treat as optional; require attestation if used; never rely solely on QRNG.

---

## Parameters (by network)

Configured in `randomness/config.py` and surfaced via `rand.getParams`:

- `round_period_secs` — total period (≈ `Tc + Tr + Tv`).
- `commit_secs`, `reveal_secs`, `vdf_verify_budget_ms`.
- `vdf_params` — modulus/iterations profile.
- `qrng.enabled`, provider IDs, max bytes, attestation flags.

**Guidance:** choose `Tv` so that a typical prover finishes well within the window, while verification stays cheap (Wesolowski verify is fast).

---

## API Overview (RPC)

- `rand.getRound` → current round id & window timings.
- `rand.commit(round, commitment)` → accept commitment if within `[Tc]`.
- `rand.reveal(round, addr, salt, payload)` → accept reveal if within `[Tr]` and matches a stored `C`.
- `rand.getBeacon(round)` → return `BeaconOut` (once finalized).
- `rand.getHistory(cursor, limit)` → paginate past `BeaconOut`s.
- Events (`randomness/rpc/ws.py`): `roundOpened`, `roundClosed`, `beaconFinalized`.

---

## Light-Client Verification

1. Verify headers and the **round schedule** (chain params).
2. Recompute `vdf_in` from `prev_beacon` (from previous verified round) and `agg` (from reveals included or referenced via receipts).  
3. Check `VDF_Verify(vdf_in, vdf_out, vdf_proof, params) == true`.  
4. If `qrng_mixed==true`, verify QRNG **attestation** (if policy requires).  
5. Recompute `beacon` and compare to the published `beacon`/digest.

---

## Failure Modes & Fallbacks

- **Sparse participation:** Even with few reveals, VDF guarantees unpredictability. Bias resistance drops toward VDF-only; acceptable by design.
- **No VDF proof within `[Tv]`:** do not finalize; the round remains pending. Implementers MAY allow late finalization (policy-gated) or fall back to stretching `prev_beacon` if explicitly configured (not recommended for mainnet).
- **QRNG outage:** proceed without QRNG.

---

## Pseudocode (reference)

```text
onRoundStart(r):
  openCommitWindow(r)

onCommit(r, C):
  if withinCommit(r): store(C)

onReveal(r, addr, salt, payload):
  require withinReveal(r)
  C' = H(commit_domain || r || addr || salt || payload)
  require stored(C') == true
  addToAggregate(r, addr, salt, payload)

onVdfFinalize(r):
  vdf_in  = H(vdf_domain || r || prev_beacon || agg(r))
  (y, π)  = VDF_Prove(vdf_in, params)              # off-chain
  assert VDF_Verify(vdf_in, y, π, params)

  mix = H(mix_domain || y || agg(r))
  if qrng_available(r):
    q    = H(qrng_domain || qrng_bytes(r))         # extractor
    out  = mix XOR q
    flag = true
  else:
    out  = mix
    flag = false

  store BeaconOut{r, agg, vdf_in, y, π, qrng_mixed:flag, beacon:out}


⸻

Operational Notes
	•	DoS hardening: rate-limit commits/reveals; constant-time-ish checks; fixed-size responses where feasible.
	•	Telemetry: export reveal counts, on-time ratios, VDF verify times, QRNG usage, and finalization latency.
	•	Reorg handling: if windows straddle reorgs, recompute agg from the canonical chain segment.

⸻

Test & Reproducibility
	•	Use fixtures in randomness/test_vectors/*; verify round transcripts and VDF proofs match published BeaconOut.
	•	Deterministic hashing domains are defined in randomness/constants.py and randomness/utils/hash.py — do not change without a version bump and migration notes.

⸻

TL;DR
	•	Commit–reveal keeps contributors from biasing the seed they revealed to.
	•	VDF ensures nobody can predict beacon before the window ends.
	•	QRNG can add entropy but never reduces security if mixed via extract-then-xor.
	•	Everything is header-bound and light-verifiable.

