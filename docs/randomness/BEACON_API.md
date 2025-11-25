# Beacon API — Contract Interface & Light Proofs (v1)

This document specifies how **contracts** consume the randomness beacon on-chain, and how **light clients** verify the beacon off-chain using compact proofs. It complements:
- `randomness/specs/BEACON.md` — round lifecycle & transcript
- `randomness/specs/LIGHT_CLIENT.md` — light verification model
- `randomness/vdf/{params.py,verifier.py}` — VDF details

> TL;DR  
> - Contracts read the **previous finalized beacon** via a deterministic syscall surface.  
> - Off-chain verifiers use a **LightBeaconProof** containing a Wesolowski VDF proof + a compact header link.  
> - Everything is domain-separated and parameter-bound to prevent replay.

---

## 1) Terminology

- **Round**: A randomness epoch with `commit → reveal → VDF → finalize`. Identified by `round_id: u64`.
- **BeaconOut**: Finalized output for a round: `beacon: bytes32` plus metadata.
- **VDF**: Wesolowski proof `π` that `y = x^(2^T)` in a group of unknown order with challenge `l`-bits.
- **Light proof**: Minimal object for verifying `(round_id, beacon)` from a trusted checkpoint without a full node.

---

## 2) Contract-Facing API (VM syscall surface)

Contracts access beacon randomness via the VM stdlib bindings (wired by `randomness/adapters/execution.py` → VM runtime):

### 2.1 Functions

```python
# Python-VM stdlib surface (conceptual signatures)

from stdlib import abi
from stdlib import syscalls  # includes .random namespace

# Deterministic bytes derived from the LAST FINALIZED beacon (round_id_final - 1),
# domain-separated by caller and per-call nonce.
syscalls.random.bytes(length: int, *, nonce: bytes = b"") -> bytes

# Return the last finalized round metadata.
syscalls.random.latest() -> {
    "round_id": int,
    "beacon": bytes,           # 32 bytes
    "prev_beacon": bytes,      # 32 bytes
    "finalized_height": int,   # block height that sealed this round
}

# Return a specific round if still retained.
syscalls.random.get(round_id: int) -> {
    "exists": bool,
    "beacon": bytes|None,
    "finalized_height": int|None,
}

Determinism & availability
	•	Contracts can only read the most recently finalized beacon (typically “previous round”).
	•	Within a block, all executions see the same latest() (no intra-block drift).
	•	bytes() derives output as:

out = H("rand/bytes" | chain_id | round_id | beacon | caller | nonce | length)

streamed/repeated as needed to reach length.

Gas
	•	G_RANDOM_LATEST = small constant (header/DB read)
	•	G_RANDOM_BYTES_BASE + G_RANDOM_BYTES_PER_32 * ceil(length/32)
	•	Exact numbers live in execution/specs/GAS.md under “syscalls/random”.

Gotchas
	•	Front-running: do not use bytes() for unpredictable auctions in the same round that produced the beacon; prefer commit–reveal within your contract, or use the next round.
	•	Liveness: if a beacon is not finalized by the round deadline, latest() stays pinned to the last good round.

⸻

3) Node & RPC (for dapps/backends)

The node exposes read-only RPC:
	•	rand.getParams → group size, delay T, soundness l, round timings
	•	rand.getRound → status of a specific round (open/closed/finalized)
	•	rand.getBeacon → { round_id, beacon, prev_beacon, finalized_height }
	•	rand.getHistory → windowed list of recent beacons
	•	WS events: roundOpened, roundClosed, beaconFinalized

See randomness/rpc/methods.py.

⸻

4) Light Client Proofs

A LightBeaconProof lets a client verify (round_id, beacon) from a trusted checkpoint header without replaying the entire chain.

4.1 Object (msgspec/CBOR)

LightBeaconProof (CBOR map)
{
  "version": 1,
  "round_id": u64,
  "params_hash": bytes32,     # hash of VDF params (n_bits, l_bits, T, backend)
  "seed_x": bytes,            # VDF input x for this round (transcript-defined)
  "T": u64,                   # iterations
  "y": bytes,                 # VDF output element (|N| bytes)
  "pi": bytes,                # Wesolowski proof element (|N| bytes)
  "prev_beacon": bytes32,

  # Header binding: prove the chain recorded this beacon
  "header": {
    "hash": bytes32,          # block that finalized this round
    "height": u64,
    "rand_root": bytes32      # commitment where BeaconOut lives (or direct beacon field)
  },

  # Link from trusted checkpoint → header (choose one shape; implementations may extend):
  "link": {
    "type": "hash_chain",     # compact parent-hash chain
    "from_hash": bytes32,     # trusted checkpoint header hash
    "chain": [ bytes32... ]   # sequence of next block hashes up to header.hash
  },

  # Inclusion: prove that (round_id, beacon, prev_beacon) is bound to rand_root
  "beacon_proof": {
    "scheme": "merkle" | "inline",
    "key": u64,               # round_id index if merklized
    "value": { "beacon": bytes32, "prev_beacon": bytes32 },
    "branch": [ bytes32... ]  # merkle branch if scheme == "merkle"
  }
}

The exact encoding is implemented in randomness/beacon/light_proof.py. Some networks may set scheme = "inline" if the header directly carries beacon.

4.2 Verification (pseudocode)

def verify_light_beacon(proof: LightBeaconProof, trusted_hash: bytes32) -> (bool, bytes32):
    assert proof["version"] == 1

    # 1) Parameter pinning
    expect_params = get_params_for_network()          # from local config / chain params snapshot
    if H(params_to_bytes(expect_params)) != proof["params_hash"]:
        return False, b""

    # 2) Header link from trusted checkpoint
    h = proof["link"]["from_hash"]
    if h != trusted_hash:
        return False, b""
    for nxt in proof["link"]["chain"]:
        # check block(nxt).parent == h and header validity (hash function, minimal checks)
        if header(nxt).parent_hash != h:              # light header checks (hash only)
            return False, b""
        h = nxt
    if h != proof["header"]["hash"]:
        return False, b""

    # 3) Inclusion: beacon fields are bound to header.rand_root (or directly embedded)
    if proof["beacon_proof"]["scheme"] == "merkle":
        root = proof["header"]["rand_root"]
        leaf = encode_leaf(proof["beacon_proof"]["key"], proof["beacon_proof"]["value"])
        if merkle_root(leaf, proof["beacon_proof"]["branch"]) != root:
            return False, b""
        (beacon, prev_beacon) = (
            proof["beacon_proof"]["value"]["beacon"],
            proof["beacon_proof"]["value"]["prev_beacon"],
        )
    else:
        (beacon, prev_beacon) = (
            proof["beacon_proof"]["value"]["beacon"],
            proof["beacon_proof"]["value"]["prev_beacon"],
        )

    # 4) VDF proof: confirm transcript leads to y and to the same beacon
    ok = wesolowski_verify(
        params=expect_params,
        x=proof["seed_x"],
        T=proof["T"],
        y=proof["y"],
        pi=proof["pi"],
    )
    if not ok:
        return False, b""

    # 5) Hash-to-beacon binding
    computed = H(b"rand/beacon" | proof["round_id"].to_bytes(8,"big") |
                 proof["params_hash"] | proof["seed_x"] | proof["y"] | prev_beacon)
    if computed[:32] != beacon:
        return False, b""

    return True, beacon

Notes
	•	The seed_x is derived per BEACON.md from (prev_beacon, aggregate_reveals, transcript parameters) and bound via params_hash and round_id.
	•	For networks where the header carries beacon directly (not merklized), rand_root is omitted and scheme="inline" ties the value to a fixed header field.

⸻

5) Data Shapes

5.1 BeaconOut (as seen by RPC & contracts)

{
  "round_id": 12345,
  "beacon": "0x…32bytes…",
  "prev_beacon": "0x…32bytes…",
  "finalized_height": 424242
}

5.2 Params hash

params_hash = H( n_bits | l_bits | T | backend_id | domain_tags | modulus_hash? )
The exact layout is defined in randomness/vdf/params.py and referenced by LIGHT_CLIENT.md.

⸻

6) Security Considerations
	•	Delay and soundness: Choose T and l per VDF_PARAMS.md. Underprovisioning can enable opportunistic bias via faster provers.
	•	Header link: Light verifiers must anchor to a trusted checkpoint (e.g., hard-coded or periodically refreshed).
	•	Replay protection: Domain separation and params_hash ensure that beacons from different networks/params cannot be transplanted.
	•	Contract usage: For lotteries, auctions, or leader election, combine beacon with unpredictable per-user commitments or the next round to avoid last-look advantages.

⸻

7) Example (Contract)

from stdlib import syscalls, abi

# A simple raffle that draws an index in [0, N)
def draw_winner(N: int, salt: bytes = b"") -> int:
    meta = syscalls.random.latest()
    r = syscalls.random.bytes(32, nonce=salt)
    # map to range using unbiased rejection
    while True:
        x = int.from_bytes(r, "big")
        if x < (1 << 256) - ((1 << 256) % N):
            return x % N
        # re-derive with different nonce to avoid bias
        salt = abi.sha3_256(b"retry|" + salt)
        r = syscalls.random.bytes(32, nonce=salt)


⸻

8) Example (Light client)

ok, beacon = verify_light_beacon(proof, TRUSTED_CHECKPOINT_HASH)
if not ok:
    raise ValueError("invalid beacon proof")
# Use beacon as seed material


⸻

9) Versioning & Compatibility
	•	Beacon API v1 is stable across minor releases.
	•	Additive fields in LightBeaconProof must be optional and guarded by "version".
	•	params_hash change (e.g., modulus rotation, T change) will not break clients that respect the hash pin.

⸻

10) References
	•	randomness/specs/BEACON.md
	•	randomness/specs/VDF.md and docs/randomness/VDF_PARAMS.md
	•	randomness/beacon/light_proof.py (encoding & checks)
	•	randomness/rpc/methods.py (RPC/WS surface)
	•	execution/specs/GAS.md (gas costs for syscalls/random)
