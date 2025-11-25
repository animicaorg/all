# Vendor Roots — provenance & update process

This directory holds **trusted public roots** used by the TEE and Quantum attestation verifiers in `proofs/`. These roots are **public metadata only** (X.509/COSE keys or certificates). There must be **no private keys** or environment-specific secrets here.

Included placeholders (replace with real roots during bring-up):
- `intel_sgx_root.pem` — Intel SGX/DCAP root (PEM, X.509).
- `amd_sev_snp_root.pem` — AMD SEV-SNP ARK/ASK anchor (PEM, X.509).
- `arm_cca_root.pem` — Arm CCA Realm attestation root (PEM, COSE/X.509 depending on release).
- `example_qpu_root.pem` — Example QPU provider root for QuantumProof demos (PEM/X.509 or EdDSA).

The verifiers read these roots **read-only** to build trust chains for attestation evidence.

---

## Security principles

1. **Reproducible provenance:** every root must have a public URL, version, and SHA-256 fingerprint recorded in the manifest (see below).
2. **Explicit rotation:** store versioned files (e.g., `intel_sgx_root_v3.pem`) and keep a `current` symlink or copy named without the version (e.g., `intel_sgx_root.pem`).
3. **Pin by fingerprint, not just name:** tests verify the **fingerprint** and **subject** DN to catch silent upstream changes.
4. **No auto-fetch at runtime:** roots are vendored and updated by a human+CI process only.
5. **Review required:** at least two reviewers sign off on a root change in Git history.
6. **Backwards compatibility:** keep the previous root alongside the new one while networks upgrade (grace window configurable in policy).

---

## Directory layout

vendor_roots/
intel_sgx_root.pem
amd_sev_snp_root.pem
arm_cca_root.pem
example_qpu_root.pem
manifest.json           # authoritative metadata (source URLs, versions, fingerprints)
README.md

> The `.pem` filenames without versions are the *current* roots referenced by code. Store older versions as `name_vN.pem` if needed.

---

## Manifest format

Create/maintain `manifest.json` (kept alongside this README):

```json
{
  "schema": "animica.vendor_roots.v1",
  "entries": [
    {
      "name": "intel_sgx_root",
      "version": "3",
      "alg": "X509-PEM",
      "source_url": "https://download.01.org/intel-sgx/sgx-dcap/..../Intel_SGX_RootCA.pem",
      "sha256": "xxxxxxxx...64hex",
      "subject": "CN=Intel SGX Root CA, O=Intel Corporation, C=US",
      "not_before": "YYYY-MM-DD",
      "not_after":  "YYYY-MM-DD"
    },
    {
      "name": "amd_sev_snp_root",
      "version": "1",
      "alg": "X509-PEM",
      "source_url": "https://developer.amd.com/.../ark.pem",
      "sha256": "yyyyyy...64hex",
      "subject": "CN=AMD SEV Root CA, O=Advanced Micro Devices, C=US",
      "not_before": "YYYY-MM-DD",
      "not_after":  "YYYY-MM-DD"
    },
    {
      "name": "arm_cca_root",
      "version": "1",
      "alg": "COSE/X509-PEM",
      "source_url": "https://developer.arm.com/.../cca-root.pem",
      "sha256": "zzzzz...64hex",
      "subject": "CN=Arm CCA Attestation Root, O=Arm Limited, C=GB",
      "not_before": "YYYY-MM-DD",
      "not_after":  "YYYY-MM-DD"
    },
    {
      "name": "example_qpu_root",
      "version": "1",
      "alg": "X509-PEM|Ed25519",
      "source_url": "https://provider.example/qpu-root.pem",
      "sha256": "aaaaaaaa...64hex",
      "subject": "CN=Example QPU Root, O=Demo, C=ZZ",
      "not_before": "YYYY-MM-DD",
      "not_after":  "YYYY-MM-DD"
    }
  ]
}

The tests in proofs/tests/test_ai_attestation.py and proofs/tests/test_quantum_attest.py will optionally validate manifest.json if present.

⸻

How to fetch & verify (offline-friendly steps)

Perform these on a build host. Replace URLs with the vendor’s current canonical links.

1) Download roots

# Intel SGX/DCAP root (example URL)
curl -fsSL -o intel_sgx_root_v3.pem "https://download.01.org/intel-sgx/sgx-dcap/...?Intel_SGX_RootCA.pem"

# AMD SEV-SNP ARK/ASK chain (often two certs; anchor by ARK)
curl -fsSL -o amd_sev_snp_root_v1.pem "https://developer.amd.com/.../ark.pem"

# Arm CCA root
curl -fsSL -o arm_cca_root_v1.pem "https://developer.arm.com/.../cca-root.pem"

# Example QPU provider (your partner)
curl -fsSL -o example_qpu_root_v1.pem "https://provider.example/qpu-root.pem"

2) Inspect & fingerprint

for f in *_v*.pem; do
  echo "== $f =="
  openssl x509 -noout -subject -issuer -dates -serial -fingerprint -sha256 -in "$f"
  sha256sum "$f"
done

Confirm:
	•	Subject DN matches expectations.
	•	Validity windows fit your policy.
	•	SHA-256 fingerprint matches vendor documentation (if published).

3) Promote current → unversioned copy

cp intel_sgx_root_v3.pem intel_sgx_root.pem
cp amd_sev_snp_root_v1.pem amd_sev_snp_root.pem
cp arm_cca_root_v1.pem arm_cca_root.pem
cp example_qpu_root_v1.pem example_qpu_root.pem

4) Update manifest.json

Fill version, source_url, sha256 (from sha256sum), subject, not_before/after (from openssl x509 -dates).

5) Run local tests

pytest -q proofs/tests/test_ai_attestation.py::test_sgx_parse_and_chain_ok \
          proofs/tests/test_ai_attestation.py::test_sev_snp_parse_and_chain_ok \
          proofs/tests/test_ai_attestation.py::test_cca_parse_and_chain_ok \
          proofs/tests/test_quantum_attest.py::test_provider_cert_chain_ok


⸻

Rotation policy
	•	Routine check cadence: monthly scan for vendor bulletins; emergency updates as needed.
	•	Overlap window: keep previous root alongside the new root for at least 2 weeks to allow staggered rollouts.
	•	Deprecation: mark old root as deprecated in manifest.json via "deprecatedAfter": "YYYY-MM-DD" and remove after the overlap window passes.

⸻

Compatibility notes
	•	Intel SGX/TDX: DCAP PCK chains usually root to Intel SGX Root CA. Some environments supply intermediate CAs; the verifier accepts them as long as the anchor matches.
	•	AMD SEV-SNP: The ARK (AMD Root Key) anchors; ASK/SEV-SNP specific intermediates may appear in quotes. We anchor by ARK subject/fingerprint.
	•	Arm CCA: Tokens may be COSE with embedded X.509 chain or JOSE/JWK; we store the PEM root for the X.509 path and accept COSE kid if mapped to the same root.
	•	QPU providers: Until a standard emerges, we store each provider’s public root (X.509 or raw Ed25519). The quantum verifier maps provider IDs to the expected root via policy.

⸻

CI enforcement
	•	CI computes sha256sum of each .pem and compares to manifest.json.
	•	CI lints subject contains expected CN= and O= fragments.
	•	CI ensures unversioned .pem exists and matches the latest version entry.

⸻

Contributing an update
	1.	Open a PR with new *_vN.pem, updated unversioned .pem, and manifest.json changes.
	2.	Paste openssl output + sha256sum into the PR description.
	3.	Link vendor advisory/release notes.
	4.	Get at least two approvals before merge.

⸻

What not to commit
	•	Private keys or CSRs.
	•	Environment-specific certs (e.g., data-center internal CA).
	•	Binary blobs without a manifest entry.

⸻

Contact

Security updates or concerns: security@animica.org (PGP preferred)
Maintainers: proofs/attestations owners listed in CODEOWNERS.

