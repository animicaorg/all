# Security Audit Checklist
_Comprehensive before/after release items for Animica components._

Keep this file with each tagged release in the repo (attach as an artifact in CI).  
For context/specs, see `docs/security/THREAT_MODEL.md`, `docs/security/DOS_DEFENSES.md`, `docs/security/SUPPLY_CHAIN.md`, and `website/public/.well-known/security.txt`.

---

## Release Metadata

- [ ] **Release tag:** `vX.Y.Z`
- [ ] **Commit SHA:** `xxxxxxxx`
- [ ] **Provenance/SLSA attestations** attached (build, source, builder)
- [ ] **SBOM** (CycloneDX/SPDX) generated & uploaded
- [ ] **Chain params hash** (from `spec/params.yaml`): `sha256:...`
- [ ] **Alg-policy root** (from `spec/alg_policy.schema.json` + pq policy): `sha3-512:...`
- [ ] **ZK VK cache digest** (`zk/registry/vk_cache.json`): `sha256:...`
- [ ] **OpenRPC digest** (`spec/openrpc.json`): `sha256:...`
- [ ] **DA schema digests** (`da/schemas/*`): recorded
- [ ] **Native crates versions** (`zk/native`, other Rust): pinned with checksums
- [ ] **Installers appcasts** signed (Sparkle/WinGet metadata updated)

---

## 1) Pre-Release: Architecture & Threat Model

- [ ] Threat model re-read and updated if scope changed (`docs/security/THREAT_MODEL.md`)
- [ ] New trust roots or policy knobs documented (PoIES caps Γ, Θ schedule, escort rules)
- [ ] Any new network-facing endpoints reviewed (RPC, WS, DA REST, studio-services)
- [ ] Rollback strategy defined (schema migrations, feature flags, hotfix channels)

---

## 2) Code Health & Static Checks

- [ ] **Linters & formatters** pass (Python, TS, Rust, Shell)
- [ ] **Type checks** pass (mypy/pyright, TS, Rust)
- [ ] **Secrets scan** (git history & workspace)
- [ ] **Binary allowlist**: no unexpected vendored executables/artifacts
- [ ] **Dead-code & unused deps** review (optional but encouraged)

---

## 3) Dependency & Supply Chain

- [ ] Lockfiles updated (pip/poetry/uv, pnpm/yarn, Cargo)
- [ ] Vulnerability scan (OSV/NVD) and **risk triage** documented
- [ ] Critical libs pinned w/ hashes (py_ecc, blst/paired, kZG libs, CBOR, msgspec)
- [ ] Reproducible builds verified where supported (Rust, Tauri, installers)
- [ ] **SLSA provenance** generated & retained
- [ ] **License compliance** checked (THIRD-PARTY notices updated)

---

## 4) Cryptography & ZK

- [ ] **Pairings (BN254)** test vectors pass (native & Python fallback)
- [ ] **KZG verify** vectors pass (commitment/opening)
- [ ] **Poseidon parameters** match circuits (rate/capacity/MDS/rounds)
- [ ] **Groth16/PLONK** verifiers validate known `snarkjs/plonkjs` fixtures
- [ ] **STARK/FRI** toy verifier gated behind policy; not enabled in prod unless audited
- [ ] **VK cache** entries present for all enabled circuits; digests pinned
- [ ] **omni_hooks** reject proofs > policy size limits; error codes mapped
- [ ] Side-channel risks acceptable for the release targets (no secret keys on server)

---

## 5) Consensus & Proofs

- [ ] **PoIES scoring** unit tests pass (caps, Γ total cap, ψ mapping)
- [ ] **Θ retarget** stability tests pass (EMA clamps, window math)
- [ ] **Nullifier reuse** protections verified (TTL windows)
- [ ] **Proof verifiers** inputs well-formed; schema checks enforced (CBOR/JSON)
- [ ] **Fork choice** deterministic tie-break unit tests green

---

## 6) Execution & VM(Py)

- [ ] **Gas table** matches `spec/opcodes_vm_py.yaml`
- [ ] **Determinism** guardrails on (forbidden imports, PRNG seeded, no sys I/O)
- [ ] **ABI encoding** stable vs vectors (types, events, receipts)
- [ ] **Capabilities** (blob/compute/zk/random/treasury) bounded & deterministic:
  - [ ] Input size caps
  - [ ] Next-block consumption for results
  - [ ] Treasury accounting hooks tested

---

## 7) Data Availability

- [ ] **NMT** inclusion & namespace-range proofs pass vectors
- [ ] **Erasure** encode/decode recoverability at k/n profiles tested
- [ ] **DAS** sampling math verified vs targets (`p_fail` bounds)
- [ ] **DA root** integrated in headers; light-client checks pass

---

## 8) P2P / RPC / Rate Limits

- [ ] **P2P handshake** (Kyber + AEAD) E2E tests pass; rate-limit tokens enforced
- [ ] **RPC JSON-RPC** schema unchanged or versioned; CORS allowlist honored
- [ ] **WS** subscriptions auth & throttles tested; backpressure OK
- [ ] **DoS defenses** validated (token buckets, payload size caps, per-IP)

---

## 9) Wallets & Apps

- [ ] **Wallet extension (MV3)**: permissions minimal; CSP & COOP/COEP safe
- [ ] **Flutter wallet**: keystore usage, lock screen, no plaintext secrets
- [ ] **Studio-web / Explorer**: CSP, `X-Frame-Options`, no secrets in client

---

## 10) Installers & Updates

- [ ] macOS codesign + notarization + stapling verified
- [ ] Windows MSIX/NSIS signed; timestamped with primary + backup TSA
- [ ] Linux AppImage/Flatpak/DEB/RPM metadata & sandbox perms OK
- [ ] Appcast(s) updated & signed; channels (stable/beta) correct

---

## 11) Docs & Disclosures

- [ ] **CHANGELOG** updated (breaking changes flagged)
- [ ] **SECURITY** docs updated (headers, CSP, rules of engagement)
- [ ] **Responsible Disclosure** contact valid; `security.txt` present
- [ ] New config/flags documented (defaults, risks, migration)

---

## 12) Testing Matrix

- [ ] **Unit** tests (all packages) ≥ target coverage
- [ ] **Integration**: devnet bring-up, mine block, deploy counter
- [ ] **Fuzz/property** tests for parsers/decoders (CBOR/JSON/Wire)
- [ ] **Benchmarks** recorded (pairing/KZG/verify_speed, DAS)
- [ ] **E2E**: RPC, WS, wallet tx flow, DA post/get, randomness beacon

---

## 13) Privacy & Telemetry

- [ ] No PII logged; logs redacted
- [ ] Telemetry (Plausible/PostHog) behind opt-in; endpoints documented

---

## 14) Final Sign-Off

| Area | Owner | Date | Notes |
|---|---|---|---|
| Cryptography/ZK |  |  |  |
| Consensus/Proofs |  |  |  |
| Execution/VM |  |  |  |
| P2P/RPC |  |  |  |
| Wallets/Studio/Explorer |  |  |  |
| Installers/Updates |  |  |  |
| Docs/Website |  |  |  |
| Security Lead |  |  |  |
| Release Manager |  |  |  |

---

## Post-Release (T+0…T+7 days)

- [ ] Canary nodes updated; monitor for regressions
- [ ] Metrics dashboards green (head time, TPS, mempool size, peer counts)
- [ ] Crash/error telemetry monitored; hotfix path ready
- [ ] Incident response on-call rota confirmed
- [ ] Advisory/CVE published if applicable; credits to reporters
- [ ] Bounty processing (if in scope)
- [ ] Appcasts/winget/flatpak repos reflect final artifacts & hashes
- [ ] Backups and artifacts archived (SBOM, provenance, logs)

---

## Appendix: Quick Commands

- **VK cache digest**
  ```bash
  jq -cS . zk/registry/vk_cache.json | sha256sum

	•	OpenRPC digest

jq -cS . spec/openrpc.json | sha256sum


	•	Generate SBOM (examples)

cargo sbom -o target/sbom.cdx.json
pip install cyclonedx-bom && cyclonedx-bom -o python-sbom.json
npx @cyclonedx/cyclonedx-npm --output-file npm-sbom.json



