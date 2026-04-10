# Randomness Specs — Doc Index & Invariants

This directory collects the normative and informative notes for the **Animica Randomness** subsystem — a commit–reveal + VDF beacon with optional QRNG mixing. The specs here complement the code under `randomness/` and the public RPC described in `randomness/rpc/`.

> Scope: consensus-facing rules, wire/object formats, safety invariants, and light-client reasoning. Implementation advice is non-normative unless explicitly called out.

---

## Doc Index

- **Commit–Reveal**
  - *Design & Windows:* `COMMIT_REVEAL.md` (opening/closing times, overlap rules, time source assumptions)
  - *Objects & Hashing:* `commit_reveal.cddl` (commitments, reveals, transcripts)
  - *Slashing Hooks:* `SLASHING.md` (optional; miss/malicious-reveal penalties)

- **VDF**
  - *Construction:* `VDF.md` (Wesolowski parameters, difficulty, soundness bound)
  - *Verifier:* `VDF_VERIFIER.md` (constant-time-ish verifier, transcript, domain tags)

- **Beacon**
  - *Finalization:* `BEACON.md` (aggregate → VDF → optional QRNG mix → `BeaconOut`)
  - *Light Proof:* `LIGHT_PROOF.md` (compact proof for light clients; hash-chain + VDF proof)

- **APIs & Storage**
  - *RPC:* `API.md` (methods, errors, event streams)
  - *State & Persistence:* `STATE.md` (round schedule, ring buffer, indices)

- **Security**
  - *Threat Model:* `SECURITY.md` (biasing attempts, equivocation, DoS, replay)
  - *Parameters:* `PARAMS.md` (round lengths, grace periods, VDF iterations, QRNG policy)

> Filenames above are forward references; they live alongside this README as the module evolves.

---

## Core Invariants (Consensus-Critical)

These properties must hold across all conforming nodes and are enforced by code and validation rules:

1. **Round Identity**
   - Each round has a unique integer `roundId`.  
   - Schedules are immutable once published: `(commit_open, commit_close, reveal_open, reveal_close, vdf_deadline)`.

2. **Commit Determinism**
   - Commitment: `C = H("rand/commit" | addr | salt | payload)` using SHA3-256.  
   - `salt` and `payload` are opaque bytes under caller control; **collision or second-preimage resistance** is assumed from the hash.  
   - Commits are only valid if recorded **before** `commit_close`. Late commits MUST be rejected.

3. **Reveal Validation**
   - Reveal `(addr, salt, payload)` MUST map to a previously accepted `C` for the same `roundId`.  
   - Reveals are only valid **after** `reveal_open` and **before** `reveal_close`.  
   - A given `(addr, C)` pair may reveal **at most once** per round.

4. **Aggregation (Bias Resistance)**
   - Let `R` be the multiset of valid reveals.  
   - The aggregator folds in a **domain-separated combiner**: `A = Comb("rand/aggregate", R)` (hash-xor fold as reference).  
   - Aggregation MUST be order-independent and deterministic given `R`.

5. **VDF Input & Proof**
   - VDF input is transcript-bound: `X = H("rand/vdf-input" | A | prev_beacon)`; `prev_beacon` is zero for round 0.  
   - A block is allowed to finalize the beacon for `roundId` IFF it carries a VDF proof `(π)` for `(X, T)` that the verifier accepts at consensus parameters `T`.  
   - If multiple distinct `(π)` appear, only the **first valid** by block order is effective; others are ignored.

6. **Beacon Monotonicity**
   - `BeaconOut(roundId).value = H("rand/beacon" | X | π | qrng_mix?)`.  
   - The sequence of beacon values forms a **hash chain** anchored at genesis.  
   - Once a `BeaconOut` is finalized for `roundId`, it is immutable except by chain reorg rules of the host consensus.

7. **Optional QRNG Mix (Non-Critical)**
   - When enabled, QRNG bytes are mixed via extract-then-xor with a transcript domain tag.  
   - Absence of QRNG bytes MUST NOT stall finalization; the mix defaults to zero.

8. **Light-Client Soundness**
   - The light proof consists of the previous beacon hash, the current `X`, and the VDF proof `(π)`; verification MUST NOT require access to the full commit/reveal set.  
   - A light client that validates `(X, π)` against `prev_beacon` can accept the `BeaconOut` without additional data.

9. **Replay & Domain Separation**
   - All hashes, commitments, and transcripts include explicit **domain tags** (e.g., `"rand/commit"`, `"rand/vdf-input"`, `"rand/beacon"`).  
   - Objects from other subsystems (DA, PoIES, etc.) MUST NOT be valid here due to domain separation.

10. **Timing Source**
    - Windows are expressed in **block-time** (consensus notion), not wall clock.  
    - Validators MUST reject messages that land outside the active window by the chain’s timing rules.

---

## Parameter Guidance (Non-Normative Defaults)

- Commit window: **N₁** blocks  
- Reveal window: **N₂** blocks (N₂ ≥ N₁/2 recommended)  
- VDF iterations: sized so honest verification time ≪ block time; use Wesolowski with modulus and difficulty per `PARAMS.md`.  
- Storage: retain commitment/reveal records at least **K** rounds for audits; beacons are retained indefinitely (or per chain policy).

---

## Conformance Checklist

Implementations should self-test the following before enabling consensus mode:

- [ ] Commitment round-trip: commit → store → reveal verifies → aggregate stable   
- [ ] Aggregation independence: permutation of reveals yields identical `A`  
- [ ] VDF verifier accepts all reference vectors and rejects perturbed proofs  
- [ ] Light proof verifies without access to commits/reveals  
- [ ] Window enforcement: early/late commit/reveal violations are rejected  
- [ ] Domain tags audited across all hashing sites  
- [ ] Reorg handling: beacon reverts/advances match fork choice

---

## Notation & Hashes

- `H` is SHA3-256 unless otherwise specified.  
- All encodings referenced by `*.cddl` files use **deterministic CBOR** as defined in the Animica wire format.  
- Byte strings in docs are hex prefixed with `0x…`.

---

## Change Policy

Backwards-incompatible changes to **consensus-critical** parts (windows, domain tags, VDF parameters, object fields consumed by validators) require a **network version bump** and must be coordinated via the chain’s governance process.

