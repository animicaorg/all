# Animica SDK — Security Guide

This document summarizes the **threat model**, **key-handling practices**, **post-quantum (PQ) notes**, and **supply-chain guidance** for the Animica SDKs (Python, TypeScript, Rust) and related tooling. It is intended for developers, auditors, and operators deploying apps and services that interact with Animica networks.

> **Principles**
> - **Client-side signing only.** Private keys never leave the user’s device.
> - **Determinism & domain separation.** All sign bytes are canonical and include `chainId` and domain tags to prevent replay and ambiguity.
> - **Algorithm agility.** PQ algorithms and policies are upgradable via the spec’d alg-policy tree.

---

## 1) Threat Model (high level)

**In scope**
- Theft of keys at rest or in memory on client machines/browsers.
- Replay/signing confusion (wrong chain, wrong domain, malleable encodings).
- Malicious dapps (phishing, transaction surreptitious signing).
- Supply-chain compromise in dependencies/build pipelines.
- Integrity of DA (Data Availability) commitments and light proofs.
- Tampering of WASM/Pyodide assets used for local simulation.

**Out of scope**
- Compromise of end-user OS/root store and fully-privileged malware.
- Side-channel leakage on shared hardware we do not control.
- Economic/consensus faults beyond SDK scope (handled by node/consensus layers).

---

## 2) Keys, Wallets & Addresses

### 2.1 Generation & Derivation
- SDKs support mnemonics (BIP-39–like) → **seed** using **PBKDF/HKDF-SHA3**, then derive PQ keypairs per account index and **algorithm id** (e.g. `dilithium3`, `sphincs_shake_128s`).
- **RNG**: uses OS-backed CSPRNGs. Deterministic seeds exist only for tests/vectors.

> **Do**
> - Generate mnemonics on trusted devices, **offline if possible**.
> - Prefer hardware entropy when available.
>
> **Don’t**
> - Check mnemonics into source control, CI logs, or issue trackers.

### 2.2 Storage & Unlock
- Keystores use **AES-GCM** with per-vault random nonces. Passwords feed a KDF; keys are unsealed in memory **only for the session**.
- In memory, keys are kept in short-lived objects. Where supported, buffers are zeroized on drop.
- **Backups**: export encrypted keystores only; never raw private keys.

### 2.3 Addressing
- Addresses are **bech32m** strings with HRP `anim` (e.g., `anim1…`).
- Payload = `alg_id || sha3_256(pubkey_bytes)`.
- SDKs provide `encode_address()`, `decode_address()`, `is_valid()` helpers.

### 2.4 Signing Domains & Anti-Replay
All critical signatures use **canonical SignBytes**:
- **Encoding**: deterministic CBOR; map ordering is canonical.
- **Anchors**: `chainId` and a domain tag (e.g., `"tx"`, `"cap"`, `"rand"`) are **always** included.
- **Effect**: transactions for one chain or domain **cannot** be replayed on another.

---

## 3) Post-Quantum (PQ) Notes

### 3.1 Algorithms
- **Signatures**: `Dilithium3` (default), `SPHINCS+ SHAKE-128s`.
- **KEM/Handshake**: `Kyber-768` + **HKDF-SHA3-256** → AEAD (`ChaCha20-Poly1305` or `AES-GCM`) for P2P.
- **Trade-offs**: SPHINCS+ signatures are larger and slower; Dilithium3 is faster with smaller sigs.

### 3.2 Implementations & Fallbacks
- Python/TS/Rust SDKs favor vetted libs; optional `liboqs` backends where available.
- **Graceful fallbacks** exist for dev environments; **do not use fallbacks in production** if they are educational/slow paths.

### 3.3 Algorithm Agility
- Networks pin a **Merkle-rooted alg-policy** (see `spec/alg_policy.schema.json`).
- Clients **verify** policy roots and **reject** disabled/deprecated algorithms.
- Migration path: enable new algs with thresholds, then deprecate unsafe ones.

---

## 4) Browser & Extension (MV3) Security

- **No server-side signing.** The wallet extension signs **in the MV3 service worker**.
- **Origin-scoped permissions**: per-site sessions; explicit **Connect** and **Approve** prompts.
- **Content script isolation**: provider is injected into an isolated world.
- **Display exact fields** before signing: `to`, `amount`, `nonce`, `gas`, `chainId`, function & args, plus the **domain tag**.
- **WASM/Pyodide integrity**: pin versions and verify checksums; prefer **Subresource Integrity (SRI)** or local bundling.

> **Phishing hygiene**
> - Show full origin and chain in all approval UIs.
> - Block/flag unicode confusables in dapp origins where practical.
> - Enforce **strict CORS** and allowlists for RPC endpoints the UI hits.

---

## 5) RPC, Rate Limits & Input Validation

- Clients talk to JSON-RPC over HTTPS/WSS when public; **rate-limit** and **CORS-restrict** server endpoints.
- Nodes/services must **validate**: types, sizes, gas/fee bounds, chainId matches, signature domains.
- WebSocket subscriptions should enforce per-IP/key **token buckets**.

---

## 6) Data Availability (DA) Integrity

- Blobs are chunked/erasure-coded and committed via **Namespaced Merkle Trees (NMT)**.
- **Do not store secrets** in DA: contents are public or retrievable by design.
- Light clients verify availability using **sample proofs** and the DA root in headers.

---

## 7) AICF (AI/Quantum) Security

- **Client** enqueues jobs; **results** are consumed deterministically **in the next block** via receipts.
- Providers must present **attestations** (TEE/QPU) that are verified into on-chain proofs; **SLA & slashing** deter equivocation and low QoS.
- Limit prompts/circuits and sanitize inputs to mitigate abuse.

---

## 8) Randomness (Commit→Reveal→VDF)

- Users commit `C = H(domain | addr | salt | payload)` within the **commit window**, then reveal; an aggregate feeds a **VDF**.
- SDKs expose helpers for `commit`, `reveal`, and beacon queries.
- **Anti-bias**: late/missing reveals reduce influence; VDF prevents manipulation post-aggregate.

---

## 9) Supply-Chain & Build Integrity

### 9.1 Dependency Hygiene
- **Pin** versions:
  - Python: `pyproject.toml` + lock/pip-compile; record hashes.
  - TypeScript: `package-lock.json` (or `pnpm-lock.yaml`); enable `npm audit`.
  - Rust: `Cargo.lock`; run `cargo audit`.
- Vendor minimal, security-critical code paths (e.g., bech32, CBOR canonicalization) to reduce transitive risk.

### 9.2 Provenance & Signing
- Generate **SLSA-style provenance** for releases.
- Sign artifacts with **Sigstore/cosign** or GPG; publish **checksums**.
- Reproducible builds where feasible; CI runners should be ephemeral with no long-lived secrets.

### 9.3 WASM/Pyodide
- Pin exact file checksums; host locally when possible.
- Enable **Content-Security-Policy (CSP)** to limit script/worker origins.

---

## 10) Operational Hardening Checklist

**Client apps**
- [ ] Use official SDK APIs for sign bytes (no ad-hoc encodings).
- [ ] Validate addresses with `is_valid` before building transactions.
- [ ] Always include **explicit** `chainId` and domain tag.
- [ ] Present human-readable previews for sign/approve.

**Wallets**
- [ ] AES-GCM keystores; strong KDF; lock on idle.
- [ ] Zeroize key material after use; avoid logging sensitive buffers.
- [ ] Block signing if `chainId` or domain mismatch.

**Services/Nodes**
- [ ] Strict CORS; token buckets on HTTP/WS.
- [ ] Enforce size/gas/TTL limits; canonical CBOR decode only.
- [ ] Monitor and rotate API keys; separate roles for write vs read.

**Supply chain**
- [ ] Lockfiles + audits (`pip-audit`, `npm audit`, `cargo audit`).
- [ ] Provenance for releases; artifact signatures; checksum verification in CI.

---

## 11) Incident Response & Disclosure

- **Vulnerability reports**: please email **security@yourdomain.tld** with details and a working PoC if possible. You may use our PGP key (see `SECURITY.txt.asc`) for encrypted reports.
- We follow **coordinated disclosure**. We’ll acknowledge within 72 hours and provide a remediation timeline.
- For on-chain incidents, coordinate with validators and service operators through the established security channels.

---

## 12) Notes for Auditors

- Cross-check that:
  - Sign bytes include `chainId` and domain tags consistently across SDKs.
  - CBOR canonicalization matches the spec; no alternate encodings accepted.
  - PQ implementations are bound to vetted libs in production builds; fallbacks never used in prod.
  - Address encoding/decoding round-trips and rejects invalid HRP/lengths.
  - Event decoders are length-checked; topic hashes match ABI.
  - DA proofs and light-client verifiers enforce namespace and index bounds.

---

## 13) References (specs in this repo)
- `spec/alg_policy.schema.json` — PQ policy objects & hashing.
- `spec/tx_format.cddl`, `spec/header_format.cddl` — canonical CBOR schemas.
- `spec/domains.yaml` — signing domain tags and separators.
- `spec/openrpc.json` — RPC surface and data types.

> If you find inconsistencies between SDK behavior and the specs, please file an issue and/or contact the security team.

---

*Last updated:* Keep this file in sync with SDK changes, dependency updates, and policy root rotations.
