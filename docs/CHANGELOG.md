# Animica — Changelog
All notable **user-facing** changes to this repository will be documented here.

This project adheres to **[Semantic Versioning](https://semver.org/)** and follows the **Keep a Changelog** format. Dates are `YYYY-MM-DD`.  
Module-scoped, low-level tweaks that don’t affect the user experience live in per-module CHANGELOGs or commit history.

> Tip: For upgrade steps, search for **Migration** blocks and **Breaking** notes.

---

## [Unreleased]
### Added
- Draft **L2 bundle mode** in Studio Web (behind flag).
- Optional **light client** proof checks in Explorer (feature-gated).

### Changed
- Increased default RPC request timeout from **10s → 15s** in SDKs.
- Minor copy and accessibility improvements on website.

### Fixed
- Wallet: resilient reconnect to WS `newHeads` after system sleep.

---

## [0.10.0] — 2025-10-01 — “Install & Ship”
### Added
- **Installers** for Wallet (macOS/Windows/Linux) and **Explorer Desktop (Tauri)**.
- **Auto-update appcasts** (stable/beta) and signing scripts.
- CI pipelines for all desktop targets (notarize/codesign/staple where applicable).

### Changed
- Website **Downloads** page wired to CI artifacts and appcasts.

### Migration
- Mac: ensure Apple API key (App Store Connect) is configured per `installers/signing/macos/*`.
- Windows: import organization code-signing cert and TSA endpoints per docs.

---

## [0.9.0] — 2025-09-10 — “Studio”
### Added
- **Studio Web** (edit → simulate → deploy → verify) with WASM Python VM.
- **Studio Services** (deploy/verify/faucet/artifacts) with strict CORS & rate limits.
- End-to-end examples and Playwright tests.

### Changed
- Shared **chain metadata** now feeds Website + Studio via unified `/chains/`.

### Migration
- Set `PUBLIC_STUDIO_URL`/`PUBLIC_EXPLORER_URL` in `website/.env` for correct deep links.

---

## [0.8.0] — 2025-08-18 — “ZK Arrives”
### Added
- **ZK subsystem**:
  - Verifiers: **Groth16 (BN254)**, **PLONK+KZG (BN254)**, **Poseidon**, **Ate pairing**, **KZG opening**, tiny **STARK/FRI** (toy).
  - **Adapters** for `snarkjs`, `plonkjs`, STARK JSON; **envelope** format; **policy** and **registry** with VK cache.
  - Optional **Rust native** fast paths via `animica_zk_native` (pyo3).
- **Benchmarks** and **tests** for verifiers and VK cache integrity.

### Migration
- Pin VKs in `zk/registry/vk_cache.json` and update signatures when adding circuits.

---

## [0.7.0] — 2025-07-20 — “Wallets”
### Added
- **Browser wallet (MV3)** with PQ keys (Dilithium3/SPHINCS+), connect/send/call.
- **RPC subscriptions** surfaced in wallet UI; simulation helpers.

### Changed
- Address codec consolidated across SDKs and wallet.

### Breaking
- Address bech32m HRP standardized to **`anim`**; previous `animica` HRP deprecated.

---

## [0.6.0] — 2025-06-25 — “Peers & Gossip”
### Added
- **P2P**: PQ handshake (Kyber768 + HKDF), gossip topics (blocks/txs/shares/blobs), header/blocks sync.
- **DoS** protections: per-peer token buckets and scoring.

### Fixed
- Deterministic tie-break in fork-choice under equal weight.

---

## [0.5.0] — 2025-05-30 — “Useful Compute”
### Added
- **AICF** (AI Compute Fund): provider registry, staking, SLA, payouts/slashing.
- **Capabilities**: contract syscalls for `ai_enqueue`, `quantum_enqueue`, `blob_pin`, `zk_verify` (pluggable).
- End-to-end proofs→claims→payouts path.

### Migration
- Configure provider stakes and allowlists before enabling jobs on public nets.

---

## [0.4.0] — 2025-05-05 — “Data Availability”
### Added
- **DA module**: Namespaced Merkle Trees, Reed-Solomon erasure, DAS sampling, retrieval API.
- **Header integration**: DA roots into block headers; light-client verification helpers.

### Changed
- Blob cost/size checks hooked into execution adapters (feature-gated).

---

## [0.3.0] — 2025-04-12 — “Contracts (Py VM)”
### Added
- **Deterministic Python VM**: validator, compiler, IR, runtime, stdlib, gas model.
- **Counter** and **Escrow** examples with tests.
- SDK **contract clients** and **codegen** (TS/Py/Rust).

### Breaking
- ABI encoding switched to canonical length-prefixed form; update custom tools.

---

## [0.2.0] — 2025-03-20 — “RPC + SDK”
### Added
- **JSON-RPC & WS** (FastAPI): params/head/blocks/tx/state/receipts.
- **SDKs**: Python, TypeScript, Rust with wallet/signing, tx build/send, events, DA/Randomness clients.

### Fixed
- Canonical CBOR map ordering in core encoder.

---

## [0.1.0] — 2025-02-28 — “Genesis”
### Added
- **Core**: headers/blocks/state DB; genesis loader; deterministic state root.
- **Consensus**: PoIES scorer & Θ retarget; fork choice.
- **Proofs**: HashShare, AI/Quantum/Storage/VDF skeletons + vectors.
- **Mining**: CPU hash searcher; Stratum & WS getwork.
- **Randomness**: commit→reveal→VDF beacon prototype.
- **Website** scaffold and initial docs.

---

## Changelog Conventions

- **Added / Changed / Fixed / Removed / Security / Deprecated / Breaking / Migration** headings.
- When applicable, include **config keys** and **expected impacts**.
- Prefer **human-oriented** summaries; implementation detail belongs in PRs.

## Release Process (summary)
1. Land changes with tests and docs.
2. Update `docs/TOC.md` if new docs/surfaces appear.
3. Bump versions across touched packages.
4. Tag `vX.Y.Z` and generate release notes from this file.
5. Publish installers/appcasts if desktop apps are affected.

---

[Unreleased]: https://github.com/animica-labs/animica/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/animica-labs/animica/releases/tag/v0.10.0
[0.9.0]: https://github.com/animica-labs/animica/releases/tag/v0.9.0
[0.8.0]: https://github.com/animica-labs/animica/releases/tag/v0.8.0
[0.7.0]: https://github.com/animica-labs/animica/releases/tag/v0.7.0
[0.6.0]: https://github.com/animica-labs/animica/releases/tag/v0.6.0
[0.5.0]: https://github.com/animica-labs/animica/releases/tag/v0.5.0
[0.4.0]: https://github.com/animica-labs/animica/releases/tag/v0.4.0
[0.3.0]: https://github.com/animica-labs/animica/releases/tag/v0.3.0
[0.2.0]: https://github.com/animica-labs/animica/releases/tag/v0.2.0
[0.1.0]: https://github.com/animica-labs/animica/releases/tag/v0.1.0
