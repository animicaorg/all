# proofs/fixtures — provenance notes (non-secrets)

This folder contains **illustrative, non-secret** artifacts used by unit tests, vectors, and CLI demos in `proofs/`.  
They are **not** production trust roots or real attestation materials. Treat everything here as **mock** or **heavily redacted**.

> TL;DR: These files help you run tests end-to-end without contacting vendor services or exposing credentials.  
> They are safe to commit. They must not be used to make security decisions.

---

## What’s included

| File | Purpose | Provenance / How it was made | Security posture |
|---|---|---|---|
| `sgx_quote.bin` | Example Intel SGX/TDX quote blob for parser tests. | Synthetic, structure-correct demo built from the public quote layout. No live PCK chain, QE identity, or platform TCB embedded. | **Mock**; for decode/negative-path tests only. |
| `sev_snp_report.bin` | Example AMD SEV-SNP attestation report for parser tests. | Constructed from public SNP report fields; signatures replaced with fixed test bytes. | **Mock**; for parser & failure-mode tests only. |
| `cca_token.cbor` | Example Arm CCA Realm Attestation Token. | COSE/CBOR structure with random nonces and placeholder claims; not signed by real roots. | **Mock**; for schema & COSE decoding tests. |
| `qpu_provider_cert.json` | Example QPU provider identity certificate. | JSON model matching `proofs/quantum_attest/provider_cert.py` schema with a self-signed dev key. | **Mock**; intended for format/validation tests. |
| `trap_seed.json` | Deterministic seed for trap-circuit simulations. | Random seed generated offline and fixed in this repository for reproducible tests. | **Benign**; used to reproduce trap outcomes. |

> Real trust roots live under `proofs/attestations/vendor_roots/` and should be fetched from official vendor endpoints following the instructions in that directory.

---

## Design constraints for fixtures

1. **No sensitive data**  
   - No real platform identities, device serials, FMSPCs tied to actual hardware, or private keys.  
   - Any signatures present are dummy bytes or signatures over dummy keys **bundled here**.

2. **Structure-correct**  
   - The byte layouts match the relevant public specs enough to exercise decoders, schema validators, and negative tests.

3. **Deterministic**  
   - Where randomness is required (e.g., trap simulations), we pin seeds (`trap_seed.json`) so tests are repeatable.

4. **Traceable**  
   - Each artifact has a documented, reproducible recipe (below). If you can’t reproduce, open an issue.

---

## Reproduce / regenerate fixtures

> You do **not** need to regenerate these for normal development. This is only for maintainers or auditors.

### 1) SGX/TDX quote (`sgx_quote.bin`)
- Compose a quote-like blob with correct field sizes and a dummy signature.
- Ensure parsers fail appropriately when vendor roots are not present.
- Quick sanity:
  ```bash
  python - <<'PY'
  import pathlib, struct, os
  p = pathlib.Path("proofs/fixtures/sgx_quote.bin")
  # Write a minimal header + dummy signature area; values are placeholders.
  p.write_bytes(b'QOUT' + struct.pack("<I", 0x00010000) + os.urandom(432))
  print("wrote", p, "len", p.stat().st_size)
  PY

2) SEV-SNP report (sev_snp_report.bin)
	•	Fill a buffer matching the public SNP report layout, with random report_data and zeroed signature.
	•	Negative tests should confirm real validation fails without vendor roots.

3) Arm CCA token (cca_token.cbor)
	•	Construct a CBOR/COSE structure with plausible claims. Do not sign with real keys.
	•	Validation tests should succeed for schema/field presence and fail for trust-chain checks.

4) QPU provider cert (qpu_provider_cert.json)
	•	Generate an ephemeral signing key (Ed25519 or PQ-hybrid if desired).
	•	Emit JSON matching the schema in proofs/schemas/quantum_attestation.schema.json.
	•	Mark "trust_level": "dev-fixture" so verifiers never elevate it to production trust.

5) Trap seed (trap_seed.json)
	•	Generate once with a CSPRNG and pin:

python - <<'PY'
import os, json, secrets, pathlib
seed = secrets.token_hex(32)
out = pathlib.Path("proofs/fixtures/trap_seed.json")
out.write_text(json.dumps({"seed_hex": seed, "note":"deterministic traps seed for tests"}, indent=2))
print("wrote", out)
PY



⸻

Verifying integrity (checksums)

After cloning or updating fixtures, record checksums:

cd proofs/fixtures
sha256sum sgx_quote.bin sev_snp_report.bin cca_token.cbor qpu_provider_cert.json trap_seed.json > SHA256SUMS
sha256sum -c SHA256SUMS

Commit SHA256SUMS with any fixture changes.

⸻

Updating policy
	•	Never replace these with real attestation objects or customer data.
	•	If you need new fields to exercise validators, extend the fixture schema-correctly and refresh vectors.
	•	For new vendors or formats, add:
	•	A short provenance note here,
	•	Mock artifacts in this folder,
	•	Schema updates in proofs/schemas/,
	•	Unit tests in proofs/tests/.

⸻

Licensing & attribution
	•	These fixtures are original mock data produced for testing.
	•	Any format descriptions or field names are derived from public documentation of respective vendors; no proprietary blobs are included.
	•	See proofs/attestations/vendor_roots/README.md for guidance on fetching official trust roots at build/runtime.

⸻

Safety checklist (maintainers)
	•	No secret keys or real platform IDs.
	•	Checksums updated (SHA256SUMS).
	•	Unit tests cover success/failure paths using these fixtures.
	•	CI passes offline (no external calls required).

