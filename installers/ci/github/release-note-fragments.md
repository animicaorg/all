# Release Note Fragments — What to Include for Tagged Releases

This document is a **maintainer checklist + template** for writing the human-curated notes that accompany tagged releases (e.g., `wallet-vX.Y.Z`, `explorer-vX.Y.Z`, `animica-vX.Y.Z`). Use it when preparing the text for:
- GitHub Releases created by CI,
- Update feeds (Sparkle appcast for macOS, WinGet metadata, etc.),
- Internal CHANGELOG aggregation.

The goal is **clear upgrade guidance**, **supply-chain transparency**, and **verification commands** for users.

---

## TL;DR (Checklist)

- [ ] **Version & date** (SemVer; channel = stable/beta).
- [ ] **Highlights** (3–7 bullet points max).
- [ ] **Breaking changes** (explicit callouts + migration steps).
- [ ] **Security** (key rotations, notarization/signing changes, CVEs, dependencies).
- [ ] **Compatibility** (supported OS/browser/tooling, protocol/chain compatibility).
- [ ] **Artifacts table** (names, sizes, SHA-256/512 checksums, signature model).
- [ ] **Verify instructions** per OS (copy-paste commands).
- [ ] **Component changes** (Wallet, Explorer, SDKs, Node/Core, ZK, DA, P2P, VM, Installers).
- [ ] **Performance** (before/after metrics; benches).
- [ ] **Known issues** and workarounds.
- [ ] **Acknowledgements** (contributors, external deps).
- [ ] **Appcast/feeds** updated (stable/beta).
- [ ] **Links** (docs, migration guide, issues).

---

## Fragment Conventions

- **SemVer**:  
  - **MAJOR**: protocol/RPC breaking changes; incompatible wallet/SDK APIs; installer packaging/id changes.  
  - **MINOR**: features and non-breaking enhancements.  
  - **PATCH**: fixes and dependency bumps without behavior changes.

- **Tone**: concise, operator/developer friendly; link detailed docs for depth.

- **Scoping**: Group by top-level area:
  - **Wallet (MV3)**, **Explorer (Tauri)**, **SDK** (Python/TypeScript/Rust), **Node/Core/Consensus/Execution**, **ZK** (verifiers & registry), **DA**, **P2P**, **Randomness**, **AICF/Capabilities**, **Installers/CI**.

- **Attribution**: reference PRs like `[#1234]` and credit authors when practical.

- **Security notes**: always include whether signing identities, appcast keys, or notarization flows changed.

---

## Release Notes Template (Copy/Paste)

```markdown
## Version
**vX.Y.Z** — YYYY-MM-DD — **Channel:** stable|beta

### Highlights
- ✨ …
- 🚀 …
- 🧰 …

### Breaking Changes
- …
**Migration:**  
1) …  
2) …  
**Impact:** which components/users are affected (RPC consumers, node operators, wallet users).

### Security
- Signing/notarization: (macOS team id / Windows cert subject / Linux repo/GPG) — changed|unchanged.
- Dependency updates with security relevance (CVE references).
- Sparkle appcast signing key rotation? (yes/no; fingerprint).

### Compatibility
- **Protocol/Chain**: compatible with chain ids: …; requires node ≥ X.Y.Z.
- **SDKs**: minimal versions: py `x`, ts `y`, rs `z`.
- **OS/Runtime**: macOS ≥ 12, Windows ≥ 10 21H2, Linux glibc ≥ 2.31. Browser: Chrome ≥ 114, Firefox ≥ 115.
- **Tooling**: Tauri/Flutter/Rust/Node versions used by CI.

### Artifacts
| File | Size | SHA-256 | Notes |
|------|------|---------|-------|
| Animica-Explorer-vX.Y.Z-macos-universal.dmg |  |  | Signed + notarized |
| Animica-Explorer-vX.Y.Z-windows-x64.msi |  |  | Signed (SHA256, RFC3161 TSA) |
| Animica-Explorer-vX.Y.Z-linux-x86_64.AppImage |  |  |  |
| … |  |  |  |

**SHA512** checksums: see attached `SHA512SUMS.txt`.

### Verify Signatures / Checksums

**macOS**
```bash
shasum -a 256 Animica-Explorer-vX.Y.Z-macos-universal.dmg
spctl -a -vv Animica-Explorer-vX.Y.Z-macos-universal.dmg
codesign --verify --deep --strict --verbose=2 /Applications/Animica\ Explorer.app
xcrun stapler validate Animica-Explorer-vX.Y.Z-macos-universal.dmg

Windows (PowerShell)

Get-FileHash .\Animica-Explorer-vX.Y.Z-windows-x64.msi -Algorithm SHA256
signtool verify /pa /v .\Animica-Explorer-vX.Y.Z-windows-x64.msi

Linux

sha256sum Animica-Explorer-vX.Y.Z-linux-x86_64.AppImage
rpm -K *.rpm   # RPM only
# .deb: verify repo/signing if distributed via apt; or compare checksum

Component Changes

Explorer (Tauri)
	•	…

Wallet (MV3)
	•	…

SDKs
	•	Python: …
	•	TypeScript: …
	•	Rust: …

Node / Core / Consensus / Execution
	•	…

ZK (verifiers / registry)
	•	VK cache: added/updated circuit ids: … (hashes pinned).
	•	Verifiers: Groth16/PLONK/STARK changes …
	•	Native fast-paths (pairing/KZG): …

DA / P2P / Randomness / AICF / Capabilities
	•	…

Installers & CI
	•	macOS pipeline: …
	•	Windows pipeline: …
	•	Linux pipeline: …
	•	Appcasts updated: installers/updates/**/appcast.xml

Performance
	•	Groth16 verify: X → Y µs on M2/Win11/Ubuntu (see zk/bench/verify_speed.py).
	•	Explorer startup: X% faster; bundle size reduced by Y MB.
	•	Node throughput / latency: …

Known Issues
	•	…
Workaround: …

Acknowledgements

Thanks to @… @… and external projects: Tauri, py_ecc, pairing, kzg, etc.

Links
	•	Docs: …
	•	Migration guide: …
	•	Full CHANGELOG: …
	•	Issues: …

---

## How to Assemble Notes for a Tag

1. **Start from fragments** in PRs since the previous tag:
   - Scan merged PRs labeled `release-notes` or `changelog`.
   - Prefer imperative present tense ("Add", "Fix", "Deprecate").

2. **Aggregate security & signing**:
   - macOS: Team ID, notarization status, appcast Ed25519 pubkey.
   - Windows: Code signing cert subject + TSA URLs.
   - Linux: Package signing / repository info if applicable.

3. **Generate checksums**: CI already emits `SHA256SUMS.txt` and `SHA512SUMS.txt`. Paste the **artifact table** and keep the sums as downloadable attachments.

4. **Confirm feeds**:
   - macOS Sparkle appcast updated (stable/beta).
   - WinGet manifest updated (stable).
   - Any custom update endpoints reflect the new version.

5. **Sanity review**:
   - Verify commands are copy-pasteable on each OS.
   - No internal paths/secrets appear.
   - All links are public and versioned.

---

## Example Fragments

**Feature**
- Explorer: add deep-link handler for `animica://tx/<hash>` ([#1234] by @alice).

**Fix**
- Wallet: prevent duplicate `tx.send` on popup re-render ([#1256] by @bob).

**Security**
- Rotate Sparkle appcast key; new pubkey fingerprint: `ed25519:ABCD…` ([#1270]).

**Breaking**
- RPC: `state.getBalance` now requires `chainId` parameter; SDKs updated ([#1299]).  
  _Migration_: update SDK to py `>=0.7.0`, ts `>=0.6.0`, rs `>=0.5.0`.

---

## Notes for Channels

- **stable**: exhaustive notes; upgrade steps tested on all platforms.
- **beta**: shorter highlights + specific testing callouts; mark known limitations.

---

## Provenance & Reproducibility

- Record build toolchain versions: **Rust**, **Node**, **Tauri**, **Flutter** (if wallet), **Cargo.lock**, **package-lock.json**.
- Link to `zk/docs/REPRODUCIBILITY.md` and include VK cache hash if ZK circuits changed.

---

## Pitfalls to Avoid

- ❌ Auto-generated wall of commits with no curation.  
- ❌ Missing verification commands or checksums.  
- ❌ Omitting breaking changes and migrations.  
- ❌ Referencing private links or internal runners/secrets.

---

*Maintainers:* keep this file close to the CI release workflows in `installers/ci/github/` so it’s visible when drafting notes on tags.
