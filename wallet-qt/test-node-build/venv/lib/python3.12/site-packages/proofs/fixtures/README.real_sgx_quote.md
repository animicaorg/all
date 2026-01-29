# Real SGX Quote Fixture

This repo deliberately does not include a real Intel SGX quote. A genuine DCAP (ECDSA) quote
is bound to a specific platform and leak-identifying. Generate your own locally:

- Requires: An SGX-capable machine/VM (e.g., Azure DCsv3/4 or bare metal), Ubuntu 22.04,
  Intel SGX PSW/AESM + DCAP (QL/QPL/Quote Verify) packages installed, AESM service running,
  and PCK cert provisioned. The script below will try to install what it can.

Outputs:
- proofs/fixtures/sgx_quote.bin — raw ECDSA quote bytes (v4), suitable for parsers/verifiers here.
- proofs/fixtures/sgx_quote.meta.json — tiny metadata (QEID hash, size, timestamp).

Verification:
- The verifier script runs Intel's quote-verify library and prints TCB/QE/Enclave identities.
