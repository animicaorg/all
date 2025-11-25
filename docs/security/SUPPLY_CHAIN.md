# Supply Chain Security
_Dependencies, SBOM, signing, and SLSA for the Animica stack._

This document defines how we **pin, build, attest, sign, and verify** everything we ship—from Python services and Rust crates to TypeScript apps, Flutter binaries, Tauri desktop shells, Docker images, and installers.

> See also: `docs/security/THREAT_MODEL.md`, `docs/security/DOS_DEFENSES.md`, and `website/docs/SECURITY.md`. Build determinism tips live in `zk/docs/REPRODUCIBILITY.md`.

---

## 0) Goals

1. **Reproducible & auditable builds** across languages.
2. **Complete SBOMs** (CycloneDX + SPDX) published next to every artifact.
3. **End-to-end signing & provenance** (SLSA-aligned, Sigstore keyless where possible).
4. **Automated vulnerability & license scanning** with enforced policies.
5. **Tamper-evident releases** (transparency logs, notarization, timestamping).
6. **Fast, documented verification** for users and integrators.

---

## 1) Surfaces & Package Managers

| Surface                    | Tech                      | Lockfile                    | Build Host                 |
|---------------------------|---------------------------|-----------------------------|----------------------------|
| Core services/libs        | Python (PEP 621)          | `pyproject.toml` + `uv.lock`/`poetry.lock` or pinned `requirements.txt` | Linux CI (x64)             |
| Native/ZK fast paths      | Rust + pyo3               | `Cargo.lock`                | Linux/macOS/Windows CI     |
| Web/Wallet/Studio         | TypeScript (pnpm)         | `pnpm-lock.yaml`            | Node LTS on Linux/macOS    |
| Website                   | Astro (Node)              | `pnpm-lock.yaml`            | Node LTS                   |
| Wallet (desktop/mobile)   | Flutter (Dart/Gradle)     | `pubspec.lock` + Gradle wrapper | macOS/Windows/Linux CI  |
| Explorer desktop          | Tauri (Rust + Node)       | `Cargo.lock` + `pnpm-lock.yaml` | macOS/Windows/Linux CI |
| Containers (optional)     | OCI/Docker                | `Dockerfile` (ARG pinned)   | Linux CI                   |

**Rules:**
- Lockfiles are **committed**. PRs updating deps must include lockfile diffs.
- Toolchains pinned via `asdf`/`mise` or CI matrix (Go/Node/Python/Rust versions).
- Vendored binaries (WASM, PQ libs, Pyodide) must have **checksums** in repo.

---

## 2) Dependency Hygiene

- **Pin exact versions.** No floating ranges.
- **Checksum pinning:** when managers support it (e.g., `pip --require-hashes`, pnpm integrity, Cargo checksums).
- **Replace deprecated packages** automatically via Renovate/Dependabot PRs.
- **License allowlist:** (MIT/Apache-2.0/BSD/ISC/MPL-2.0) → CI breaks on violations.
- **No post-install exec hooks** (`preinstall`, `install` scripts) unless reviewed and explicitly allowed.

### Auditors (CI gates)
- Python: `pip-audit` + `safety` (CVE/Sonar).
- Rust: `cargo audit` + `cargo-deny` (advisories & licenses).
- Node: `pnpm audit --prod` + `license-checker-rseidelsohn`.
- Containers: `trivy`/`grype` for base images and layers.

---

## 3) SBOM (Software Bill of Materials)

We produce SBOMs **per component** (build-time) and a **roll-up** per release.

### Formats
- **CycloneDX JSON** (primary)
- **SPDX JSON** (secondary, where feasible)

### Tools
- **Syft** for general SBOMs (Python, Node, Rust, container images).
- **cargo-cyclonedx** for Rust crates.
- **cyclonedx-npm** and **cyclonedx-py** (optional) for language-native views.

### Layout
SBOMs are stored under `artifacts/sbom/<component>/<version>/` and attached as **Sigstore attestations** to OCI images or published with GitHub Releases.

```bash
# Example (Python service):
syft dir:./studio-services -o cyclonedx-json > artifacts/sbom/studio-services/${VERSION}.cdx.json

# Rust crate:
cargo cyclonedx --format json --output artifacts/sbom/animica_zk_native/${VERSION}.cdx.json

# Container:
syft registry:ghcr.io/animica/studio-services:${VERSION} -o cyclonedx-json \
  > artifacts/sbom/containers/studio-services-${VERSION}.cdx.json


⸻

4) Reproducible Builds
	•	SOURCE_DATE_EPOCH set from git tag date.
	•	Strip non-determinism (timestamps, absolute paths) in archives and compilers where supported.
	•	Rust: enable -Zremap-cwd-prefix (nightly) when available; otherwise use stable determinism knobs and cargo-strip.
	•	Python wheels: build in clean env with python -m build; prefer pure-Python wheels; for native wheels use manylinux/musllinux images with pinned toolchains.
	•	Node: pnpm fetch --frozen-lockfile → pnpm -r build. No network at compile stage.
	•	Flutter: pin flutter --version, Gradle wrapper and Android build-tools; Xcode version pinned in CI.

We document nuances in zk/docs/REPRODUCIBILITY.md.

⸻

5) Signing & Provenance

5.1 Git & Tags
	•	Signed commits & tags (SSH or GPG). Release tags are mandatory and immutable.

5.2 Sigstore (Keyless)
	•	Use GitHub Actions OIDC to obtain a Fulcio certificate.
	•	Sign and attest build artifacts with cosign; publish to Rekor.
	•	Predicate types:
	•	SLSA Provenance v1 (build recipe, materials).
	•	CycloneDX SBOM (as a cosign attest).
	•	Optional Vuln report (SARIF) as attestation.

# Sign a tarball/wheel/MSIX/DMG:
cosign sign-blob --yes --oidc-provider github ./dist/animica-wallet-${VERSION}.dmg \
  --output-signature ./dist/animica-wallet-${VERSION}.dmg.sig \
  --output-certificate ./dist/animica-wallet-${VERSION}.dmg.pem

# SLSA provenance attestation:
cosign attest --yes --predicate provenance.slsa.json \
  --type slsaprovenancev1 ./dist/animica-wallet-${VERSION}.dmg

5.3 OS-native Signing & Notarization
	•	macOS: codesign + notarytool + staple (see installers/*/macos/).
	•	Windows: signtool with timestamp (primary + backup TSA).
	•	Linux: GPG repo signing (optional for Flatpak/DEB/RPM). AppImage embeds signature.

5.4 Appcast / Updaters
	•	Sparkle feeds are Ed25519 signed (keys in installers/wallet/macos/sparkle/).
	•	Appcasts are generated, then signed and validated in CI.

⸻

6) SLSA (Supply-chain Levels for Software Artifacts)

Targeting SLSA 3 (non-hermetic) initially; path to 3+:
	•	Ephemeral builders (GitHub-hosted runners), no long-lived secrets (OIDC everywhere).
	•	Immutable, versioned build scripts within repo.
	•	Isolated build steps; artifacts come only from workspace and declared inputs.
	•	Provenance generated in CI and attested with Sigstore.

Provenance contents:
	•	Builder id (workflow ref + SHA).
	•	Materials (lockfiles, Dockerfile digest).
	•	Invocation parameters (env, matrix versions).

⸻

7) Release Procedure (TL;DR)
	1.	Merge PR → CI runs audits, tests, builds.
	2.	Create signed tag vX.Y.Z.
	3.	CI builds artifacts for all platforms.
	4.	Generate SBOMs (per component + roll-up).
	5.	Sign artifacts (cosign) + OS-native signing where needed.
	6.	Generate SLSA provenance + SBOM attestations; upload to Rekor.
	7.	Publish GitHub Release with artifacts, checksums, SBOMs; update appcasts.
	8.	Post-release verification job re-downloads artifacts and verifies signatures/attestations.

⸻

8) Verification: How Users Validate

# 1) Verify cosign signature & Rekor entry:
cosign verify-blob --certificate dist/animica-wallet.dmg.pem \
  --signature dist/animica-wallet.dmg.sig dist/animica-wallet.dmg

# 2) Inspect SBOM:
jq '.components[] | {name, version, licenses}' artifacts/sbom/wallet/<ver>.cdx.json | head

# 3) Check notarization / Windows signature:
spctl -a -vv dist/animica-wallet.dmg
signtool verify /pa dist/animica-wallet.msix


⸻

9) Containers (if used)
	•	Base images pinned by digest (FROM ubuntu@sha256:...).
	•	Multi-arch images built via docker buildx bake.
	•	Signed with cosign (keyless); SBOM attached as attestation.
	•	trivy scan gates publish (critical/high CVEs fail build).

⸻

10) Third-party Artifacts & Vendoring
	•	WASM (Pyodide, PQ WASM, snark libs): pinned versions + checksums (.lock.json).
	•	liboqs / crypto backends: prefer building from source in CI; if using prebuilt, require publisher signatures and checksums verified in-script.
	•	Fonts, icons, binaries: must live under /public or vendor/ with LICENSE and hash.

⸻

11) Secrets, Keys, Rotation
	•	Prefer keyless Sigstore; where long-lived keys are required (Apple, Windows), store in CI secrets with least privilege (see installers/scripts/store_signing_secret_ci.md).
	•	Timestamping servers for Windows signatures are pinned (primary + backup).
	•	Rotation policy in installers/signing/policies.md.

⸻

12) Policy as Code (CI)
	•	Pipelines in installers/ci/github/*.yml, website/.github/workflows/*.yml:
	•	Enforce frozen lockfiles.
	•	Run audits and license checks.
	•	Generate SBOMs and publish attestations.
	•	Block release if any gate fails.

⸻

13) Known Gaps & Roadmap
	•	Full hermetic builds (containerized toolchains) for some targets (Flutter/macOS) remain WIP.
	•	Reproducible DMG/MSIX byte-for-byte equality is limited by platform packagers; we still provide attestations and notarization.
	•	Migration to SLSA 3 hardened builders / SLSA 4 when available in our CI provider.

⸻

14) Quick Reference (Commands)

# Generate SBOMs
syft dir:. -o cyclonedx-json > artifacts/sbom/repo-${GIT_SHA}.cdx.json

# Cosign sign & attest (keyless)
cosign sign-blob --yes ./dist/app.dmg \
  --output-signature ./dist/app.dmg.sig --output-certificate ./dist/app.dmg.pem
cosign attest --yes --predicate artifacts/sbom/app.cdx.json --type cyclonedx ./dist/app.dmg

# Rust audits
cargo audit && cargo deny check

# Python audits
pip-audit -r requirements.txt || true  # fail on high/critical per policy

# Node audits
pnpm audit --prod


⸻

Keep this file updated alongside any changes to CI, package managers, or platform signing.
