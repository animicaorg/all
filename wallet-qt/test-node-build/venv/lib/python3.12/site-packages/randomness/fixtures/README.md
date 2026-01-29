# Randomness — Fixtures

This directory contains **sample inputs/outputs** used by docs, CLIs, and basic integration tests of the randomness subsystem (commit→reveal→VDF→beacon). These are *demonstration* artifacts, not consensus vectors.

> For normative, deterministic **test vectors**, see unit tests under `randomness/tests/` and the light-client spec in `randomness/specs/LIGHT_CLIENT.md`.

---

## What lives here

Typical files you may find or generate locally:

- `commits.jsonl` — one JSON object per line with `{round, address, salt, payload, commitment}`.
- `reveals.jsonl` — `{round, address, salt, payload}` objects that match a prior commitment.
- `vdf_input.bin` — 32-byte VDF input `X_r` for a sample round (raw bytes).
- `vdf_proof.json` — Wesolowski proof record `{round, iterations, output_hex, proof_hex}`.
- `beacon.json` — compact beacon snapshot `{round, header_hash, vdf_output, light_proof}`.

None of the above are consensus-critical; they are here to make it easy to try the tools and to illustrate formats.

---

## Reproducing fixtures (devnet)

1) **Commit** (choose deterministic salts/payloads for repeatability):
```bash
# Replace <HEX> with even-length hex strings.
omni rand commit --salt 00000000000000000000000000000000 --payload 1111

	2.	Reveal (in the reveal window):

omni rand reveal --salt 00000000000000000000000000000000 --payload 1111

	3.	Prove VDF (after the round closes):

# Produces a proof file for the current round using the reference prover.
omni rand prove_vdf --out fixtures/vdf_proof.json

	4.	Verify / Inspect:

omni rand verify_vdf --proof fixtures/vdf_proof.json
omni rand get_beacon > fixtures/beacon.json
omni rand inspect_round

If you’re running without a devnet, you can still inspect the light proof shape and VDF parameters via randomness/specs/* and randomness/beacon/light_proof.py.

⸻

Notes
	•	These fixtures are illustrative and may change as parameters (e.g., VDF iterations) evolve.
	•	Do not treat fixture values as stable commitments across networks; they’re environment-dependent.
	•	Domain separation strings are documented in the specs and enforced in code; see:
	•	randomness/utils/hash.py
	•	randomness/specs/VDF.md
	•	randomness/specs/LIGHT_CLIENT.md

