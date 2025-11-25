# Studio Web — Security Model

This document explains the security model and recommended operational posture
for **Studio Web**, with special focus on:

- **No server-side signing** (keys never leave the user’s device)
- **Content integrity** and artifact immutability
- **Strict CORS** and origin controls for Studio Services

> **TL;DR**
>
> - Signing happens **only** in the browser via the user’s wallet provider
>   (`window.animica`) or other client-side signers. Server-side signing is
>   explicitly **not supported**.
> - All code/artifacts are **content-addressed** and integrity-checked.
> - **Studio Services** are protected by **CORS allowlists**, **API keys** (for
>   sensitive routes like faucet), and **rate limits**.

---

## 1) Threat Model & Goals

**Primary goals**

1. **Key isolation:** Private keys remain client-side; services never receive
   raw keys or seed phrases.
2. **Integrity:** Transactions and artifacts are canonicalized and hashed to
   detect tampering.
3. **Least privilege:** Services provide optional conveniences (deploy relay,
   verify, artifacts, faucet) without ever gaining signing capabilities.
4. **Origin trust:** Only explicitly allowed front-ends may call Studio
   Services, enforced by strict CORS and API-key gating.

**Out of scope**

- Compromised end-user machines or malicious extensions can still exfiltrate
  keys. Users must keep their environments secure.
- If a user authorizes a malicious front-end origin in their wallet, the
  wallet may sign attacker-provided payloads.

---

## 2) No Server-Side Signing

**Design principle:** All signing is initiated by the user’s wallet provider
in the browser. Studio Services **never** sign on behalf of users.

- **SignBytes:** The SDK builds **canonical CBOR** sign-bytes for domain
  separation and deterministic hashing.
- **Domain separation:** SignBytes embed:
  - `chainId` (anti-replay across chains),
  - `nonce`/`sequence` (anti-replay per account),
  - `gas`/`fee`/`expiry` (bounds and timebox),
  - `type` (transfer / call / deploy) to segregate intents.
- **PQ Signatures (optional):** Wallets may offer post-quantum schemes (e.g.,
  Dilithium3, SPHINCS+). The SDK feature-gates these and tags signed payloads
  with the algorithm identifier so verifiers can check the correct scheme.

**Implications**

- Studio Services *relay* signed transactions, but cannot forge them.
- If Services are compromised, attackers cannot sign user transactions.

---

## 3) Transaction Integrity

- **Canonical CBOR:** All sign-bytes use canonical CBOR ordering and encoding.
  Any transformation would invalidate signatures.
- **Hashing:** Transaction IDs derive from a stable hash of the signed bytes.
- **Chain match:** The SDK and Services validate that the `chainId` matches the
  configured network to prevent replay to other networks.

**Best practices**

- Always present a human-verifiable preview of `to`, `value`, `fee`, and
  `chainId` in the UI prior to signing.
- Wallet UIs should display the same fields to avoid “blind signing”.

---

## 4) Content Integrity (Artifacts & Verification)

- **Content addressing:** Artifacts (source, manifest, compiled code) are stored
  under their **digest** (e.g., `sha256`), ensuring **write-once** semantics.
- **Code hash:** Verification recomputes code hash from sources+manifest and
  compares against the on-chain or expected value.
- **Immutability:** Attempted overwrites under the same digest are rejected.

**User flow**

1. Compile locally (WASM) → preview **code hash**.
2. Deploy (signed by user) → resulting address and code hash recorded.
3. Verify via Services → Services recompile; if the recomputed hash matches,
   the result is persisted and publicly referenceable.

---

## 5) Strict CORS & Origin Policy

**Studio Services** enforce **strict CORS**:

- **Allowlist:** Only configured origins may access the API from browsers.
- **Credentials:** Cross-site credentials are disabled unless explicitly needed.
- **Headers:** CORS preflight is pinned to explicit methods/headers used by the
  SDK and app.

**Recommendations**

- Maintain a **short allowlist** (e.g., production app origin, staging origin).
- Keep **wildcards (`*`) disabled** in production.
- Version/commit IDs may be echoed via a `Server`/`X-App-Version` header for
  traceability (not for auth).

---

## 6) Authentication, Authorization & Rate Limits

- **API keys:** Sensitive endpoints (e.g., **faucet**) require API keys passed
  via Bearer token or query param (only over HTTPS).
- **Token bucket limits:** Per-IP and per-key buckets mitigate abuse.
- **Replay-resistant requests:** Nonces or idempotency keys are encouraged for
  state-changing routes (e.g., deploy relay job submission).

**Operational advice**

- Rotate keys periodically; store them securely (secrets manager).
- Use separate keys per environment (dev/staging/prod) with scoped permissions.
- Monitor Prometheus `/metrics` and structured logs for anomalies.

---

## 7) Transport & Browser Hardening

- **TLS required:** All public endpoints must be served via HTTPS with modern
  ciphers. HSTS is recommended.
- **Content Security Policy (CSP):** Lock down script and worker sources to
  trusted CDNs and your own origins. Avoid `unsafe-inline`/`unsafe-eval`.
- **Cross-Origin Isolation (COOP/COEP):** Recommended for optimal WASM/worker
  behavior and future security features.
- **No Service Worker signing:** Any PWA features must **not** intercept or
  transform SignBytes or signed payloads.

---

## 8) WASM Simulator Integrity

- **Pinned versions:** Pyodide, VM package, and stdlib are version-pinned with
  checksums; builds record a lock file.
- **Determinism:** The VM avoids nondeterministic APIs for repeatable results.
- **No network:** The worker has no direct network I/O for simulation calls.

**Supply-chain notes**

- Vendor WASM assets are fetched with locked versions and checksum verification.
- Consider **subresource integrity (SRI)** when serving from CDNs.

---

## 9) Server Storage & Privacy

- **Storage backends:** File system or S3-compatible storage; artifacts are
  content-addressed and immutable.
- **PII minimization:** Services store only operational metadata (request IDs,
  digests, timestamps). Do not store wallet addresses unless required for
  verification linkage or user-facing features.
- **Log redaction:** Never log SignBytes or private data. Parameter redaction is
  enforced in structured logs.

---

## 10) Known Limitations & User Responsibilities

- **Endpoint phishing:** Users must verify they are interacting with the
  correct front-end origin and RPC URL.
- **Extension security:** Users must review wallet prompts and permissions;
  malicious extensions can still sign malicious payloads if granted access.
- **PQ caveats:** Where PQ algorithms are used, ensure both signers and
  verifiers implement the same scheme and domain tags.

---

## 11) Incident Response & Responsible Disclosure

- Publish per-environment status pages and changelogs.
- Roll keys and revoke origins promptly if compromise is suspected.
- We welcome responsible disclosure. Please report vulnerabilities to the
  security contact in the repository root `SECURITY.md` or via the listed
  security email/channel.

---

## 12) Checklist (Production)

- [ ] Server-side signing **disabled** (no code path exists).
- [ ] **CORS allowlist** contains only approved origins.
- [ ] **API keys** enabled on sensitive routes; secrets rotated and scoped.
- [ ] **Rate limits** configured per-IP and per-key.
- [ ] **TLS/HSTS** enabled; modern cipher suites; certificates monitored.
- [ ] **CSP/COOP/COEP** configured for the app and docs sites.
- [ ] **Artifact storage** is content-addressed; overwrites disabled.
- [ ] **Logs** redact sensitive fields; metrics exposed at `/metrics`.
- [ ] **WASM/Pyodide** versions pinned; SRI checks (if CDN used).
- [ ] **Backups** and disaster recovery tested for storage and configuration.

---

*This document covers Studio Web’s front-end and the companion Studio Services.
For SDK and simulator-specific guidelines, see their respective SECURITY notes.*
