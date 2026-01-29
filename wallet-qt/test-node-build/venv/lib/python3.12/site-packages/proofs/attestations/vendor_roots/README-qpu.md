# QPU Provider Trust Roots (JWKS/PEM)

Most quantum providers issue signed result receipts using standard cloud identities (OIDC/JWT).
Static PEM roots are uncommon; instead, we fetch provider **JWKS** (JSON Web Key Sets) and cache them
for signature verification of provider tokens or EAT/JOSE bundles.

This directory provides:
- `qpu_roots.json` — registry of provider issuers and JWKS URIs.
- `install_official_qpu_roots.sh` — fetches/caches JWKS for known providers; supports extra URIs.
- `qpu_cache/*.jwks.json` — fetched key sets (rotated by provider; safe to refresh).

### Included (official) JWKS
- **IBM Quantum (IBM Cloud IAM)**: issuer discovery at `iam.cloud.ibm.com`; JWKS URI: `/identity/keys`.  
  (See IBM docs and examples that reference `https://iam.cloud.ibm.com/identity/keys`.)
- **Azure Quantum (Azure AD)**: JWKS at  
  `https://login.microsoftonline.com/common/discovery/keys`.
- **Google (Google Cloud IAM / OAuth2)**: JWKS at  
  `https://www.googleapis.com/oauth2/v3/certs`.

> Note: AWS Braket uses AWS SigV4 request signing (not JWKS). Verifying Braket receipts involves
> SigV4 verification, not OIDC/JWT.

### Add other QPU providers
Many vendors use hosted identity (e.g., Auth0, Keycloak, Cognito) and publish a JWKS at:
`https://<issuer>/.well-known/jwks.json`. If you have their issuer, you can add it without changing code:

```bash
QPU_EXTRA_JWKS="https://your-issuer/.well-known/jwks.json,https://another-issuer/.well-known/jwks.json" \
  bash proofs/attestations/vendor_roots/install_official_qpu_roots.sh

This will write additional *.jwks.json files into qpu_cache/.

Wire-up notes
	•	The verifier in proofs/quantum_attest/provider_cert.py should load JWKS from qpu_cache (by slug),
select the correct key by kid/alg, and verify JOSE/EAT signatures.
	•	JWKS keys rotate; schedule a periodic refresh (e.g., hourly) by re-running the installer.

References (for the JWKS endpoints above)
	•	IBM Cloud IAM JWKS endpoint (/identity/keys) is used in IBM client configs/documentation.
	•	Azure AD OpenID Connect jwks_uri: https://login.microsoftonline.com/common/discovery/keys.
	•	Google OAuth2 certs JWKS: https://www.googleapis.com/oauth2/v3/certs.
	•	AWS SigV4 signing process (for Braket): see AWS docs.

