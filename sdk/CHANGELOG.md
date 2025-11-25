# Changelog
All notable changes to the Animica SDKs are documented here.

This file covers **cross-SDK** updates that apply to one or more of:
- **Python** (`omni_sdk`)
- **TypeScript** (`@animica/sdk`)
- **Rust** (`animica-sdk`)
- **Common** (specs, schemas, codegen IR, shared test vectors)

We follow [Semantic Versioning](https://semver.org/) and the
[Keep a Changelog](https://keepachangelog.com/) format.

---

## [Unreleased]
### Added
- ABI/codegen: output stable event/topic selectors across PY/TS/RS targets.
- Contracts client: `overrides.fee` and `overrides.nonce` control on all languages.
- DA client: optional preflight `head_at_submission` to guard against stale heads.
- Randomness client: light-proof verification helper exposed at top-level for each SDK.
- Test vectors: expanded `aicf_jobs.json` and `headers.json` with edge cases.

### Changed
- Tx encoding: canonical CBOR map key ordering clarified in docs and enforced in tests.
- Retry policy: default backoff jitter tuned (shorter initial delay, capped total).

### Fixed
- Event decode: bytes vs hex normalization for indexed parameters.
- WebSocket reconnect: rare double-subscribe after transient disconnects.

### Deprecated
- Python: `omni_sdk.tx.encode.sign_bytes_v0` (use `encode.sign_bytes` v1 domain).

---

## [0.2.0] — 2025-09-26
### Added
- **AICF**: job enqueue/read clients across PY/TS/RS with shared request/receipt schemas.
- **Randomness**: commit/reveal helpers; beacon fetch; light client verification.
- **Codegen**: Python/TS/Rust generators accept ABI → typed clients; templates and examples.
- **Contracts**: event decoder utilities and log filters.
- **Common**: `abi.schema.json`, `openrpc.json`, and shared vectors (txs, proofs, headers).

### Changed
- PQ signers default to **Dilithium3** (SPHINCS+ optional); domain separation clarified.
- HTTP clients: uniform error model (`code`, `message`, `data`) across languages.
- WS clients: unified events (`newHeads`, `pendingTxs`) naming and payload shape.

### Fixed
- CBOR big-int round-trip parity across PY/TS/RS.
- TypeScript: `sendAndWait` race when receipt polling overlaps WS confirmation.
- Rust: `bech32` address checksum mismatch on mixed-case inputs.

### Security
- Enforced `chainId` in sign-bytes (breaking for 0.1.x—see migration).

### Migration
- Rebuild any signed test vectors with 0.2.0 sign-bytes domain.
- TS: replace `sendAndWatch` with `sendAndWait`.
- PY: import `ContractClient` from `omni_sdk.contracts.client` (path change).

---

## [0.1.1] — 2025-08-30
### Added
- DA client (pin/get) stubs in TS/Rust to match Python feature parity.
- Retry helpers with exponential backoff + jitter (shared logic mirrored per language).

### Fixed
- Python: `uvarint` encoding for values ≥ 2^32.
- TS: browser build exports (ESM) for tree-shaking.
- Rust: `serde` config for `Block` timestamps (seconds → integer).

---

## [0.1.0] — 2025-08-12
### Added
- Initial public preview of Animica SDKs:
  - Core JSON-RPC/WS clients.
  - Tx build/encode (deterministic CBOR) and send/await helpers.
  - Wallets: mnemonic → seed, PQ signers (Dilithium3, SPHINCS+ feature-gated).
  - Contract ABI loader/validator and generic contract clients.
  - Examples and minimal tests per language.
- Shared schemas and test vectors committed under `sdk/common/`.

---

## Notes
- **Breaking changes** are listed under “Changed/Security/Migration” sections of each release.
- For language-specific granular changes, see the per-package CHANGELOGs (if present) or git history.

[Unreleased]: https://example.com/animica/compare/v0.2.0...HEAD
[0.2.0]:     https://example.com/animica/compare/v0.1.1...v0.2.0
[0.1.1]:     https://example.com/animica/compare/v0.1.0...v0.1.1
[0.1.0]:     https://example.com/animica/releases/tag/v0.1.0
