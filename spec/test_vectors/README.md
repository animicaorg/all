# Test Vectors — Usage & Canonicalization

This folder contains the **canonical, repository-wide** interoperability vectors used by core, proofs, VM, DA, RPC, and SDKs. They are the primary source of truth for how objects are **encoded, hashed, signed, verified, and executed**.

> Files here are referenced by multiple test suites. Changing them requires care (see **Updating vectors**).

## What lives here

- `txs.json` — Transactions (transfer/deploy/call), signatures, access-lists, receipts.
- `headers.json` — Headers (roots, Θ, nonce/mixSeed domain, policy roots, chainId).
- `proofs.json` — PoIES proofs (HashShare, AI, Quantum, Storage, VDF) + expected ψ inputs.
- `vm_programs.json` — Python-VM programs (source + manifest) and expected call traces.

Each JSON entry includes **inputs**, **canonical encodings** (CBOR bytes as hex), and **expected outputs** (hashes, accept/reject flags, state roots, logs, ψ breakdowns).

---

## Canonicalization rules

### A. JSON (vector files themselves)
Vectors are JSON for human diffs, but they describe exact CBOR bytes.

- **Encoding**: UTF-8, Unix newlines (`\n`), no BOM.
- **Whitespace**: Minimized; a single trailing newline at EOF.
- **Key order**: **Lexicographic (ascending, Unicode code point)** in every object.
- **Numbers**: Base-10 integers, no leading zeros; no floats anywhere.
- **Booleans / null**: `true`, `false`, `null` (lowercase).
- **Byte strings**: Lowercase hex **with `0x` prefix** (e.g., `0xdeadbeef`).
- **Addresses**: Bech32m strings (`anim1…`) where applicable.
- **Algorithm IDs**: As declared in `spec/pq_policy.yaml` / `pq/alg_ids.yaml`.

> Rationale: JSON is not used for consensus; it’s only for vectors. The **CBOR** encodings inside the vectors are the normative bytes.

### B. CBOR (normative encodings described by vectors)
We use **deterministic CBOR** across the codebase. Unless a specific schema says otherwise:

- **Definite length** arrays/maps/strings only (no indeterminate length).
- **Map key ordering**: strictly increasing **by the encoded key bytes**.
- **Integers**: smallest-length encoding (major type 0/1), no floats.
- **Byte/String**: shortest definite-length representation, UTF-8 for text.
- **Tags**: none, unless explicitly specified by a CDDL rule.
- **No NaN/float** anywhere in consensus objects.

Schemas live in `spec/*.cddl` and `spec/*.schema.json`.

---

## Hashing & domains

- **Hash functions**: SHA3-256 / SHA3-512 are the defaults (see `spec/domains.yaml`).
- **Domain separation**: Every hash/signature uses a **domain tag**:
  - `TxSignBytes = H( "animica/tx-sign" | chainId | cbor(tx_signing_view) )`
  - `HeaderHash = H( "animica/header"   | chainId | cbor(header_view) )`
  - Proof nullifiers, DA commitments, and receipts root each have distinct tags.
- **SignBytes** views exclude non-signed fields per schema comments.

---

## How to run the vectors

### 1) Install deps
```bash
# from repo root
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

2) Run the full test suite (consumes these vectors)

pytest -q
# or focused:
pytest spec -q
pytest core proofs vm_py -q

3) Sanity smoke (optional quick checks)

Most modules include dedicated tests that round-trip these files:
	•	Tx round-trip: JSON → object → CBOR → re-decode → hash/sign → compare tx_hash.
	•	Header round-trip: JSON → CBOR → hash → equality with header_hash.
	•	Proof verify: JSON → proof body → verify() → ψ inputs match; invalid cases fail.
	•	VM program: compile → simulate calls → match return values, logs, gas.

You can run a few focused suites:

pytest core/tests/test_cbor_roundtrip.py -q         # CBOR canonicalization
pytest proofs/tests/test_*vectors*.py -q            # proof metrics/ψ mapping
pytest vm_py/tests/test_runtime_counter.py -q       # VM example parity

If a given file isn’t present yet in your checkout, run pytest -k vector to discover available tests.

⸻

Round-trip expectations (per file)

txs.json

For each case:
	1.	Build Tx from JSON fields.
	2.	Encode CBOR (deterministic).
	3.	Compare with cbor_hex in the vector.
	4.	Compute tx_hash and signBytes using domains.
	5.	Verify PQ signature per spec/pq_policy.yaml.
	6.	If a receipt_expected is present, apply against a temp state and compare.

headers.json
	1.	Build Header from JSON.
	2.	Deterministic CBOR encode and compare with cbor_hex.
	3.	Re-compute HeaderHash and match.
	4.	Re-run PoIES acceptance on included u and proof ψ-sum (when present).

proofs.json
	1.	Parse envelope by type_id, validate body against CDDL/JSON-Schema.
	2.	Run verifier to obtain ProofMetrics.
	3.	Map metrics → ψ inputs (pre-caps), then apply caps per spec/poies_policy.yaml.
	4.	Compare expected ψ contribution and acceptance flags (when embedded in headers).

vm_programs.json
	1.	Load source + manifest (ABI).
	2.	Validate + compile to VM IR; static gas estimate.
	3.	Simulate listed calls; compare returns, logs, gas used.

⸻

RNG & determinism
	•	Any “random” fields in vectors are generated from fixed seeds:
	•	seed = sha3_256("animica/vector/" | path | case_id)
	•	Derive bytes via HKDF-SHA3-256 when multiple streams are needed.
	•	Never use wall-clock time or OS RNG to regenerate vector material.

⸻

Updating vectors
	1.	Open an issue describing the change (schema drift, new field, bugfix).
	2.	Update the relevant CDDL/JSON-Schema in spec/.
	3.	Regenerate only the impacted entries.
	4.	Keep old cases when possible; add deprecated: true if they remain instructive.
	5.	Run:

pytest -q


	6.	In PR, include:
	•	Reason for change
	•	A diff of CBOR bytes (old vs new)
	•	Cross-module test evidence (core/proofs/vm_py)

Vectors must remain portable: do not embed environment-specific paths or machine outputs.

⸻

Validation checklist
	•	JSON keys sorted, no floats, hex lowercase with 0x.
	•	CBOR bytes match deterministically re-encoded object.
	•	Hashes/signatures recompute under the correct domain.
	•	Proofs verify and ψ inputs agree with policy caps.
	•	VM traces match returns, events, and gas.
	•	Chain IDs and policy roots match spec/chains.json and spec/poies_policy.yaml.

⸻

Pointers
	•	Schemas: spec/tx_format.cddl, spec/header_format.cddl, spec/blob_format.cddl,
spec/abi.schema.json, spec/manifest.schema.json, spec/alg_policy.schema.json
	•	Domains: spec/domains.yaml
	•	PoIES math: spec/poies_math.md
	•	PQ policy: spec/pq_policy.yaml

Happy verifying! Small inconsistencies caught here prevent large consensus bugs later.
