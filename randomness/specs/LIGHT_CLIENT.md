# Light Client Verification — Rounds & Proofs

**Status:** Normative (client-facing)  
**Module:** `randomness/beacon/light_proof.py`  
**Version tag:** `animica:rand:light:v1`  
**Consensus impact:** None (verifies outputs produced by consensus full nodes)

This document specifies how a *light client* verifies the randomness beacon round-by-round without downloading all commits/reveals or executing DA/attestations. The light client verifies a compact **Light Round Proof** that anchors the round to a canonical block header, reconstructs the VDF input from header-committed metadata, and checks the **Wesolowski** VDF proof. It then chains outputs to obtain a succinct, auditable beacon history.

QRNG mixing (if enabled locally; see `QRNG.md`) is **not** consensus-critical and is *optional* for light clients. Light verification targets the consensus beacon value `V_r` per round `r`.

---

## 1) Roles & trust

- **Full node (producer):** Executes commit→reveal window logic, aggregates reveals, derives VDF input, verifies (or accepts) a VDF proof in the winning block, and commits round metadata into the block header’s *Proofs Root*.
- **Light client (verifier):** Tracks headers (via standard header sync and fork choice) and validates each round using a compact proof. It **does not** fetch individual commits/reveals.

**Assumptions for a light client**
1. You can obtain the canonical block header at height `h_r` (the round’s anchor) and verify its acceptance via normal header sync (PoIES weight and signatures as applicable).
2. You trust at least one checkpoint: `(B_{r0}, h_{r0}, header_{r0})`, or the genesis round.

---

## 2) Canonical objects and hashes

Domain separation (prepended, byte strings):

D_INPUT  = “animica:rand:input:v1”
D_CHAIN  = “animica:rand:chain:v1”
D_META   = “animica:rand:meta:v1”

Per round `r`:
- `A_r` — **Aggregate reveals hash**, committed by the producer into the header’s *Proofs Root* (see §3). The construction of `A_r` is consensus-defined but opaque to light clients.
- `X_r` — **VDF input**, defined as:

X_r = SHA3-256(D_INPUT || B_{r-1} || A_r)

- `(V_r, π_r, params_r)` — VDF output and proof under Wesolowski, with parameter set identifier (modulus label, iteration count `T_r`, etc.).  
- `B_r` — **Beacon chain hash** exported for light usage:

B_r = SHA3-256(D_CHAIN || r || V_r || header_hash(h_r))

Including `header_hash(h_r)` binds the beacon value to the specific canonical block that carried `A_r`.

Notes:
- The consensus beacon is `V_r` (32B). `B_r` is a *derived* chaining convenience for light clients.

---

## 3) Header anchoring (what’s committed)

Each block header contains a **Proofs Root**, a Merkle root over typed leaves for consensus-adjacent artifacts (e.g., randomness, useful-work proofs). The randomness leaf is:

RandMeta = {
round:   r,              # uint
aggr:    A_r,            # 32 bytes (aggregate reveals hash)
vdf: {
params_id:  u16,       # profile label (modulus/iterations)
iterations: T_r,       # uint
}
}

The producer inserts `H(D_META || encode(RandMeta))` as a leaf in the Proofs tree. The light proof supplies a Merkle branch from this leaf to the header’s Proofs Root.

---

## 4) Light Round Proof (LRP)

A compact, CBOR-encoded object (see `beacon/light_proof.py`):

LightRoundProof := {
v:            1,                         # version
r:            uint,                      # round id
h:            uint,                      # block height that anchors round r
header_hash:  bytes .size 32,            # hash of the anchor header at height h
rand_meta:    RandMeta,                  # as in §3 (includes A_r and VDF params)
vdf_output:   bytes .size 32,            # V_r
vdf_proof:    bytes,                     # Wesolowski proof π_r
proofs_branch: [bytes],                  # Merkle branch to header.proofs_root
}

**Size target:** Typically < 2 KiB (dominated by the Wesolowski proof).

---

## 5) Verification algorithm (light client)

**Inputs:**  
- Trusted checkpoint `(B_{r-1}, h_{r-1}, header_hash(h_{r-1}))` for the previous verified round.  
- Candidate `LightRoundProof p`.

**Steps:**
1. **Sanity & monotonicity**
   - Check `p.v == 1`.
   - Require `p.r == r_prev + 1`.
   - Require `p.h > h_{r-1}` (strictly later header).
2. **Header availability**
   - Obtain header `H = header_at_height(p.h)` via header sync.
   - Check `hash(H) == p.header_hash`.
   - Using `p.proofs_branch`, verify inclusion of `leaf = H(D_META || encode(p.rand_meta))` under `H.proofs_root`.
   - Check `p.rand_meta.round == p.r` and that `params_id, iterations` are acceptable for the network.
3. **Recompute VDF input**
   - Compute `X_r = SHA3-256(D_INPUT || B_{r-1} || p.rand_meta.aggr)`.
4. **Verify VDF**
   - Run Wesolowski verification with `(X_r, p.vdf_output, p.vdf_proof, params_r)`. Reject if it fails.
5. **Construct and record the chained value**
   - Set `B_r = SHA3-256(D_CHAIN || p.r || p.vdf_output || p.header_hash)`.
   - Persist `(B_r, p.h, p.header_hash)` as the new checkpoint; expose `V_r = p.vdf_output` to consumers.

**Result:** Accept the round if *all* checks pass.

---

## 6) Forks & reorgs

- If header sync selects a heavier chain where the header at `p.h` changes, previously accepted `B_r` anchored to the old header MUST be discarded.  
- Light clients should bind `B_r` to `header_hash` (as specified) so downstream consumers can detect reorg-affinity.  
- To advance across a reorged segment, re-verify LRPs against the new canonical headers.

---

## 7) QRNG mixing (optional, non-consensus)

- `B_r` and the LRP verify `V_r` only. If a client chooses to use mixed output `M_r` (see `QRNG.md`), it may fetch `(qrng_root, leaves)` out-of-band and audit the mix locally. Failure to obtain QRNG data **must not** invalidate the light proof of `V_r`.

---

## 8) Security considerations

- **Soundness:** An adversary must either (a) forge the header commitment (i.e., out-mine or out-work the canonical chain) or (b) forge a valid Wesolowski proof for `X_r`.  
- **Bias resistance:** The aggregation that produced `A_r` is enforced by full nodes; light clients rely on the header’s commitment to `A_r`.  
- **Replay protection:** `B_r` binds `header_hash` and `r`, making cross-height replay detectable.  
- **Parameter drift:** Clients must maintain an allowlist of acceptable `params_id/iterations` to prevent weakened VDF profiles.

---

## 9) Interop notes

- **Message encoding:** The CBOR layout of `LightRoundProof` is stable and round-trips via `msgspec`.  
- **Header fields:** `proofs_root` is the Merkle commitment field; fork-choice and other header fields are verified during header sync, outside this spec.  
- **Batch verification:** Clients may verify multiple LRPs in sequence to amortize header fetches.

---

## 10) Reference pseudocode

```python
def verify_light_round(prev: tuple[bytes,int,bytes], p: LightRoundProof, net_params) -> tuple[bytes,int,bytes,bytes]:
    B_prev, h_prev, hh_prev = prev

    assert p.v == 1
    assert p.r > 0 and p.r == (round_from(B_prev) + 1)  # or track r_prev out-of-band
    assert p.h > h_prev

    H = fetch_header(p.h)
    if hash_header(H) != p.header_hash:
        raise Invalid("header hash mismatch")

    leaf = sha3_256(D_META + cbor_encode(p.rand_meta))
    if not merkle_verify(leaf, p.proofs_branch, H.proofs_root):
        raise Invalid("rand_meta not in proofs_root")

    if p.rand_meta.round != p.r:
        raise Invalid("round id mismatch")
    if not params_allowed(net_params, p.rand_meta.vdf):
        raise Invalid("disallowed vdf params")

    X_r = sha3_256(D_INPUT + B_prev + p.rand_meta.aggr)
    if not wesolowski_verify(X_r, p.vdf_output, p.vdf_proof, p.rand_meta.vdf):
        raise Invalid("bad VDF proof")

    B_r = sha3_256(D_CHAIN + u64be(p.r) + p.vdf_output + p.header_hash)
    return (B_r, p.h, p.header_hash, p.vdf_output)  # new checkpoint + V_r


⸻

11) Test vectors

Vectors should cover:
	•	Happy path from a known checkpoint across N rounds.
	•	Bad VDF proof.
	•	Wrong A_r (Merkle branch altered).
	•	Header mismatch at h.
	•	Parameter downgrade attempt (iterations too small).
	•	Reorg scenario: accept old, then roll forward on new chain.

See randomness/beacon/light_proof.py and randomness/tests/test_light_client_verify.py.

