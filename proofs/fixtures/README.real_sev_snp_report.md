# Real AMD SEV-SNP Report Fixture

We don't commit a real SEV-SNP report to the repo. A genuine report is bound to
your platform (chip ID, TCB levels, etc.). Generate one locally:

**Requirements**
- An AMD SEV-SNP capable VM/host (e.g., Azure DCasv5/DCedsv5, GCP C3D, or bare metal EPYC Milan/Genoa with SNP).
- Ubuntu 22.04 (Jammy) with `/dev/sev-guest` present (kernel sev-guest driver).
- Ability to install build essentials and linux headers (script will do it).

**Outputs**
- `proofs/fixtures/sev_snp_report.bin`  — raw SNP attestation report (binary).
- `proofs/fixtures/sev_snp_report.meta.json` — metadata (size, sha256, time).
- (Optional) `proofs/fixtures/sev_snp_certs.bin` — extended-report cert blob if available.

**Quickstart**
```bash
proofs/fixtures/tools/gen_real_sev_snp_report.sh
proofs/fixtures/tools/show_sev_snp_report.sh  # prints metadata summary

Verifying cryptographically
	•	Full signature/chain verification is handled by Animica’s verifier (proofs/attestations/tee/sev_snp.py)
using ASK/ARK roots and a VCEK fetched from AMD KDS (chip-id + TCB).
	•	This folder includes only a structural/sanity reader. For full crypto validation, run the
Animica verifier or a vendor tool on the same machine that produced the report.

