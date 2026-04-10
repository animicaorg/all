# proofs/test_vectors — how to run and cross-check with `spec/test_vectors/proofs.json`

This folder contains per–proof-kind **focused** vectors used by unit/integration tests in `proofs/tests/`.  
The **authoritative, aggregated** canonical set lives at: `spec/test_vectors/proofs.json`.

## What’s here

- `hashshare.json` — u-draws, header binding, micro-target ratio checks
- `ai.json` — TEE evidence (SGX/SEV/CCA), trap receipts, QoS inputs
- `quantum.json` — QPU provider certs, trap outcomes, scaling references
- `storage.json` — heartbeat PoSt windows (with/without retrieval bonus flag)
- `vdf.json` — Wesolowski verifier inputs/outputs
- Fixtures they depend on (in `../fixtures/`): SGX/SEV/CCA samples, QPU cert and deterministic `trap_seed.json`.

## Prereqs

- Python 3.10+ and a local editable install of this repo:
  ```bash
  cd ~/animica
  python -m venv .venv && source .venv/bin/activate
  pip install -U pip wheel
  pip install -e .
  pip install -e ./proofs[dev]  # brings pytest, msgspec/cbor2, etc.

	•	(Optional) Vendor roots present (or the tests will use bundled placeholders):
	•	proofs/attestations/vendor_roots/*.pem (see that folder’s README for fetching commands).
	•	Deterministic runs:

export PYTHONHASHSEED=0



Run the vectors (unit tests)

Fast sanity across all kinds:

pytest -q proofs/tests

Run a single proof-kind:

pytest -q proofs/tests/test_hashshare.py
pytest -q proofs/tests/test_ai_attestation.py
pytest -q proofs/tests/test_ai_traps_qos.py
pytest -q proofs/tests/test_quantum_attest.py
pytest -q proofs/tests/test_storage.py
pytest -q proofs/tests/test_vdf.py

CLI spot-checks (prints metrics and ψ-inputs):

python -m proofs.cli.proof_verify proofs/test_vectors/hashshare.json
python -m proofs.cli.proof_verify proofs/test_vectors/ai.json
python -m proofs.cli.proof_verify proofs/test_vectors/quantum.json
python -m proofs.cli.proof_verify proofs/test_vectors/storage.json
python -m proofs.cli.proof_verify proofs/test_vectors/vdf.json

Cross-check against spec/test_vectors/proofs.json
	1.	Build a local aggregated view from the per-kind JSON files:

jq -n \
  --slurpfile h proofs/test_vectors/hashshare.json \
  --slurpfile a proofs/test_vectors/ai.json \
  --slurpfile q proofs/test_vectors/quantum.json \
  --slurpfile s proofs/test_vectors/storage.json \
  --slurpfile v proofs/test_vectors/vdf.json \
  '{
     version: 1,
     hashshare: $h[0],
     ai:        $a[0],
     quantum:   $q[0],
     storage:   $s[0],
     vdf:       $v[0]
   }' > /tmp/proofs.local.json

	2.	Compare to the canonical spec:

diff -u /tmp/proofs.local.json spec/test_vectors/proofs.json || true
sha256sum /tmp/proofs.local.json spec/test_vectors/proofs.json

The diff must be empty (or only differ by allowed comments/whitespace). Hashes should match.

Canonicalization rules (must hold for both local and spec aggregates)
	•	JSON: UTF-8, Unix newlines, sorted object keys, no trailing commas.
	•	Binary values: hex strings lowercase, 0x-prefixed.
	•	Integers: JSON numbers when they fit 53-bit; otherwise decimal strings.
	•	CBOR: always canonical (major type length minimal; maps sorted by key bytes).
Schemas are in proofs/schemas/*.cddl and enforced by proofs/cbor.py.

Determinism and seeds
	•	AI/Quantum trap sets come from proofs/fixtures/trap_seed.json.
Changing this seed will change trap patterns and thus the vectors. Keep it stable across runs.
	•	For tests that sample ranges (e.g., quantum widths/depths) we fix RNG through HKDF(SHA3-256) with the seed+provider+nonce tuple.

Adding or updating vectors (maintainers)
	1.	Edit per-kind JSON in proofs/test_vectors/….
	2.	Run the unit tests to ensure acceptance and ψ-inputs are stable:

pytest -q proofs/tests


	3.	Re-aggregate with the jq snippet above into /tmp/proofs.local.json.
	4.	If correct, replace spec/test_vectors/proofs.json with the new aggregate and commit both changes.

Common pitfalls
	•	Vendor roots missing: TEE/QPU attestation tests will skip or xfail depending on environment.
Provide the PEMs for full coverage.
	•	Non-canonical hex: ensure lowercase; vectors will fail strict equality otherwise.
	•	Time-varying fields: vectors contain only invariant material (no wall-clock timestamps). If you see time drift, you likely included a non-canonical field.

Mapping to PoIES ψ-inputs

All verifiers emit ProofMetrics (see proofs/metrics.py).
proofs/policy_adapter.py converts those into the ψ-input record consumed by consensus/scorer.py.
Tests test_policy_adapter.py and consensus/tests/test_scorer_accept_reject.py validate the end-to-end pipeline.

⸻

Contact: open an issue with the failing vector name, your environment (Python, OS), and the proofs.cli.proof_verify output.
