# Randomness — Test Vectors

This folder documents (and, in follow-ups, will include) **canonical test vectors** for the `randomness` module:
commit→reveal, aggregation, VDF input/verification, beacon finalization, and light-client proofs.

These vectors are meant to be:
- **Deterministic** — reproducible from the spec and code paths.
- **Minimal** — only what's necessary to validate correctness.
- **Portable** — encoded as JSON (or small binary blobs when required).

---

## Conventions

- **Encoding:** Unless stated otherwise, byte strings are **lowercase hex** with a `0x` prefix; integers are JSON numbers.
- **Hashing:** SHA3-256 / SHA3-512 as defined in `randomness/utils/hash.py` (domain-separated with the constants in `randomness/constants.py`).
- **Rounds:** `round` (aka `RoundId`) is a monotonically increasing integer.
- **Addresses:** When present, addresses are 32-byte hashes (hex). UI/CLI may show Bech32m (`anim1…`), but vectors keep raw bytes for portability.
- **VDF:** Wesolowski proof per `randomness/vdf/verifier.py` with parameters from `randomness/vdf/params.py` (referenced by `params_id`).

---

## Files & Shapes (planned)

> File names are stable; fields mirror the Python types in `randomness/types`.

### 1) `commit_reveal.json`
Validates commitment construction and reveal checking.

```json
{
  "cases": [
    {
      "round": 42,
      "addr": "0x4e0c…",                 // 32 bytes
      "salt": "0x1f2e…",                 // arbitrary bytes
      "payload": "0xaabbcc",             // arbitrary bytes
      "commitment": "0x5d7c…",           // C = H(domain | addr | salt | payload)
      "reveal_ok": true
    }
  ]
}

2) aggregate.json

Bias-resistant aggregation of reveals within a round.

{
  "round": 42,
  "reveals": ["0x…", "0x…", "0x…"],
  "aggregate": "0x…"                     // hash-xor fold per commit_reveal/aggregate.py
}

3) vdf.json

VDF input derivation and verification vectors.

{
  "params_id": "wesolowski_devnet_v1",
  "cases": [
    {
      "round": 42,
      "prev_beacon": "0x…",
      "aggregate": "0x…",
      "vdf_input": "0x…",                // derived seed
      "iterations": 1048576,
      "y": "0x…",                         // output
      "pi": "0x…",                        // proof
      "valid": true
    }
  ]
}

4) beacon.json

End-to-end beacon finalization.

{
  "cases": [
    {
      "round": 42,
      "prev_beacon": "0x…",
      "aggregate": "0x…",
      "vdf": { "y": "0x…", "pi": "0x…", "iterations": 1048576, "params_id": "wesolowski_devnet_v1" },
      "qrng_mix": null,                  // or "0x…" when QRNG is used
      "beacon_out": "0x…",               // final output per beacon/finalize.py
      "light_proof": {                   // compact proof for light clients
        "round": 42,
        "hash_chain": ["0x…","0x…"],
        "vdf": { "y": "0x…", "pi": "0x…" }
      }
    }
  ]
}


⸻

Verifying Locally (informative)

The vectors are designed to be checked with the provided CLIs:

# 1) Commit/reveal (replay a case)
omni rand commit --salt <hex> --payload <hex>
omni rand reveal --salt <hex> --payload <hex>

# 2) VDF verification
omni rand verify_vdf --round <id> --y <hex> --pi <hex> --iters <N> --params-id wesolowski_devnet_v1

# 3) Beacon / light-proof inspection
omni rand get_beacon

For programmatic checks, see:
	•	randomness/commit_reveal/verify.py
	•	randomness/vdf/verifier.py
	•	randomness/beacon/finalize.py
	•	randomness/beacon/light_proof.py

⸻

Reproducibility Notes
	•	All hashes are domain-separated; mismatched domain tags will fail vector checks.
	•	When QRNG mixing is enabled, the transcript must include provider identity and bytes length; otherwise qrng_mix is null.
	•	VDF parameters are referenced by params_id to avoid embedding large moduli directly in JSON.

⸻

Adding New Vectors
	1.	Keep payload sizes small; prefer one-line hex for readability.
	2.	Include a case_id if multiple corner cases are grouped together.
	3.	Validate with pytest randomness/tests -q before submitting.

