# Animica Installers — Overview, Platforms, Signing, and CI Flow

This document explains how we produce, sign, verify, and ship installable artifacts for Animica components (node, RPC, P2P, miner, studio-services, and the optional `animica-zk-native` accelerator). It also covers our release channels and the CI pipeline that turns commits into reproducible, verifiable builds.

> TL;DR
> - **Platforms:** Linux (x86_64, aarch64), macOS (arm64, x86_64), Windows (x64)
> - **Artifacts:** OS packages (DEB/RPM/MSI/PKG), tarballs/zip, container images, Python wheels (for `zk/native`)
> - **Signing:** Sigstore (OIDC) attestations for *all* artifacts; OS-native codesigning for MSI/PKG; repository signing for APT/YUM
> - **Provenance:** SLSA provenance + SBOMs (CycloneDX), published with each artifact
> - **Verification:** `cosign verify-blob`/`verify-attestation`, `sigsum`/`rekorc`, OS trust stores for MSI/PKG
> - **Release channels:** nightly → alpha → beta → stable (gates via tests + manual promotion)

---

## 1) Supported Operating Systems & Targets

We build the following targets in CI. Where possible, binaries are **reproducible** and containers are **distroless**.

| OS / Distro | CPU | Packaging | Notes |
|---|---:|---|---|
| Ubuntu 22.04+/Debian 12+ | x86_64, aarch64 | `.deb` + APT repo | systemd unit for `animicad` node; files under `/usr/lib/animica` |
| RHEL 9+/Fedora 39+ | x86_64, aarch64 | `.rpm` + YUM repo | systemd unit; SELinux-friendly defaults |
| macOS 13+ | arm64, x86_64 | signed `.pkg` + notarized | LaunchDaemon for background node; universal tarball as fallback |
| Windows 11 / Server 2022 | x64 | signed `.msi` | Services entry for node; optional Desktop shortcuts for tools |
| Containers | linux/amd64, linux/arm64 | `ghcr.io/animica/*` | distroless images for `node`, `studio-services`, `miner` |
| Python Wheels (optional) | any (CPython 3.8–3.12) | `animica-zk-native-*.whl` | built via `maturin`; manylinux/macosx/win wheels |

**Components included in system packages**
- `animicad` – main node (core+consensus+p2p+rpc)
- `animica-miner` – optional local miner/stratum
- `animica-studio-services` – verify/deploy/faucet FastAPI
- `omni` – umbrella CLI (thin wrappers around module CLIs)

> *Note*: The **wallet extension** and **studio-web** are shipped separately via web stores / static hosting, and are out of scope for OS installers.

---

## 2) Artifact Layout & Installation Paths

- **Linux**  
  - Binaries: `/usr/bin/animicad`, `/usr/bin/animica-miner`, `/usr/bin/omni`
  - Libraries/Data: `/usr/lib/animica/**`  
  - Config: `/etc/animica/*.yaml` (owned by package, `0644`)  
  - Data dir: `/var/lib/animica` (owned by `animica:animica`)  
  - Logs: journald by default; optional `/var/log/animica`

- **macOS**  
  - Payload: `/Library/Animica/**` (immutable binaries/config templates)  
  - LaunchDaemon: `/Library/LaunchDaemons/com.animica.node.plist`  
  - User data (default): `~/.animica`

- **Windows**  
  - Install dir: `C:\Program Files\Animica\`  
  - Service: `AnimicaNode` (Local Service account)  
  - Config: `%PROGRAMDATA%\Animica\config.yaml`  
  - Logs: ETW + `%PROGRAMDATA%\Animica\logs\`

---

## 3) Signing & Notarization Model

We use **two layers** of trust:

### 3.1 Sigstore (all artifacts)
- CI uses GitHub OIDC to obtain short-lived certificates.
- We sign **every produced artifact** (tarballs, zips, deb/rpm, container images, wheels) and attach:
  - **Detached signature** (cosign)
  - **SLSA provenance** (in-toto attestation)
  - **CycloneDX SBOM**
- Transparency: entries submitted to Rekor.

**Verify example (blob):**
```sh
cosign verify-blob \
  --certificate <artifact>.pem \
  --signature <artifact>.sig \
  <artifact>

Verify container:

cosign verify ghcr.io/animica/animicad:1.2.3
cosign verify-attestation --type slsaprovenance ghcr.io/animica/animicad:1.2.3

3.2 OS-native Codesigning
	•	Windows: Authenticode with EV Code Signing key held in an HSM (e.g., Azure Key Vault or SignPath). Timestamping via TSA (RFC 3161). MSI + embedded EXE are signed.
	•	macOS: codesign with Developer ID Installer/Application; notarization via App Store Connect API; stapled tickets ensure spctl --assess passes offline.
	•	Linux repos: APT repo signed with GPG (separate offline-maintained key); RPM repo signed via createrepo_c --update + GPG. Individual .deb/.rpm also get Sigstore attestations as above.

Python wheels are not Authenticode-signed; they are covered by Sigstore (attestations & signatures) and standard PyPI TUF.

⸻

4) Reproducibility & Supply Chain
	•	Pinned toolchains: Containerized builds with locked versions (Rust toolchain, Python, Node, Go where applicable).
	•	Deterministic archives: SOURCE_DATE_EPOCH, normalized file mtimes/owners.
	•	SBOM: Generated via syft (or cyclonedx-components) for each artifact.
	•	Provenance: SLSA v1.0 provenance (builder ID = GitHub Actions; source = repo SHA/tag).
	•	Policy: Releases must have matching SBOM + provenance; promotion gates enforce this.

⸻

5) CI Flow (GitHub Actions)

Pipeline lives in .github/workflows/:
	•	test.yml (already in repo): runs verifier unit tests/benches (py-only + native).
	•	release.yml (this folder contains templates/scripts; see below) orchestrates packaging and signing.

5.1 Jobs & Stages

commit → build:test (matrix) → package (os-specific) → sign+attest → publish (draft)
                                              ↘ containers → push+sign
manual promote: nightly → alpha → beta → stable

Key jobs
	•	build_test: run unit/integration tests; build wheels (zk/native) matrix across OS/Python.
	•	package_linux: fpm-based DEB/RPM from hermetic build root; create systemd units; repo metadata.
	•	package_macos: create .pkg, sign, notarize, staple.
	•	package_windows: WiX toolset to produce .msi, sign & timestamp.
	•	containers: build/push ghcr.io/animica/* multi-arch images; cosign sign & attest.
	•	sign_attest: generate SBOMs (CycloneDX), SLSA provenance, cosign signatures/rekor uploads.
	•	publish: upload to GitHub Release (draft); optionally to package repos (APT/YUM) and PyPI for animica-zk-native.

Secrets / OIDC
	•	Sigstore: no long-lived secret (OIDC).
	•	Apple notarization: APPLE_API_KEY, APPLE_API_KEY_ID, APPLE_API_ISSUER.
	•	Windows EV signing: delegated to SignPath/KeyVault; CI holds only the integration credentials.
	•	APT/YUM GPG: short-lived CI key only for repo metadata; long-term root key is offline and used to rotate.

⸻

6) Release Channels & Versioning
	•	Nightly: every merge to main.
	•	Alpha: tagged pre-release vX.Y.Z-alpha.N.
	•	Beta/RC: -beta.N / -rc.N, feature-frozen; candidates run on dogfood testnet.
	•	Stable: vX.Y.Z.
	•	SemVer:
	•	core, consensus, p2p, rpc, miner: share a platform version.
	•	animica-zk-native wheels track their own crate version, pinned in platform release notes.

Promotion requires:
	1.	Green tests across OS matrix (py-only + native).
	2.	Verified SBOM + SLSA provenance for all artifacts.
	3.	Smoke tests on ephemeral devnet via workflow dispatch.
	4.	Manual approval by two maintainers.

⸻

7) Local Packaging (Developers)

Quick local builds (unsigned):

# Linux DEB (unsigned)
./installers/scripts/pkg_linux.sh --deb --out dist/

# macOS PKG (unsigned)
./installers/scripts/pkg_macos.sh --out dist/

# Windows MSI (unsigned)
powershell -File installers\\scripts\\pkg_windows.ps1 -OutDir dist

# Python wheels (animica-zk-native)
cd zk/native
maturin build --release -m Cargo.toml -F "python pairing kzg"

Signing/notarization steps require CI credentials and are intentionally not available locally.

⸻

8) Verifying Downloads

Cosign (recommended for all OSes)

cosign verify-blob \
  --certificate animicad-1.2.3-linux-amd64.tar.gz.pem \
  --signature   animicad-1.2.3-linux-amd64.tar.gz.sig \
  animicad-1.2.3-linux-amd64.tar.gz
cosign verify-attestation --type slsaprovenance animicad-1.2.3-linux-amd64.tar.gz

macOS

spctl --assess --type install Animica-1.2.3.pkg
codesign --verify --verbose=2 /Library/Animica/bin/animicad

Windows

Get-AuthenticodeSignature "Animica-1.2.3-x64.msi"

APT/YUM repo
	•	Import repo GPG key from release notes, then apt update / dnf update.
	•	Packages also carry Sigstore attestations alongside the .deb/.rpm.

⸻

9) Security Notes
	•	Private code-signing keys live in HSM-backed services; CI never sees raw keys.
	•	All attestations are anchored in transparency logs (Rekor); releases are reproducible & auditable.
	•	Report issues via SECURITY.md process (private email / GH security advisories).

⸻

10) Folder Contents

This installers/ directory will accumulate:
	•	scripts/ — packaging scripts (fpm, WiX, pkgbuild), repo publish helpers
	•	templates/ — systemd units, plist files, WiX fragments
	•	ci/ — reusable GitHub composite actions for sign/attest/notarize
	•	README.md — (this file)

If you add a new artifact type, ensure you update:
	1.	CI jobs to build & sign it,
	2.	SBOM/provenance generation,
	3.	This README with verification steps.

⸻

