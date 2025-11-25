# Releases — Versioning, Changelogs & Signing

This document describes how we **version**, **cut**, **sign**, and **publish** releases across the Animica monorepo. It also lists verification steps for downstream users/operators.

> Related docs:
> - `docs/CHANGELOG.md` — user-facing highlights
> - `installers/` — platform packagers & CI
> - `website/docs/DEPLOYMENT.md` — site deploy (out of band from versioned binaries)
> - `installers/ci/github/*.yml` — release automation pipelines

---

## 0) Release Philosophy

- **Safety > speed**. All releases must pass unit, integration, e2e, and security checks before signing.
- **Reproducibility**. Lock toolchains and dependencies; publish exact hashes.
- **Auditability**. Every binary/installer is traceable to a signed git tag and SBOM (optional).
- **Gradual rollout**. We ship to **beta** first, then promote to **stable**.

---

## 1) Versioning

We use **SemVer** (`MAJOR.MINOR.PATCH`) with **pre-release** tags (`-alpha.N`, `-beta.N`, `-rc.N`) when appropriate.

### 1.1 Release Train vs Package SemVer

- We tag the monorepo with a **train version**, e.g. `v0.8.3`.  
- Major packages also carry **per-package** versions in their own files, bumped together unless noted:
  - Core services: `core/`, `rpc/`, `consensus/`, `proofs/`, `da/`, `execution/`, `p2p/`, `mempool/`, `randomness/`.
  - Tooling: `wallet-extension/`, `sdk/` (py/ts/rs), `studio-wasm/`, `studio-services/`, `studio-web/`, `zk/`.
  - Native: `zk/native` crate (`animica_zk_native`).

**Breaking changes** that affect **on-chain formats**, consensus, or RPC **increase MINOR or MAJOR** (never PATCH).  
**Soft-gated features** (feature flags) can ship in MINOR with defaults off.

### 1.2 Tagging & Branching

- `main` is always releasable. We cut `release/x.y` when stabilizing a minor.
- Tags (`git tag -s`) are **annotated and signed** by a release manager (GPG or SSH signing).

```bash
# Bump versions in package files (see §2.1), commit:
git commit -m "release: v0.8.3"

# Signed tag:
git tag -s v0.8.3 -m "Animica v0.8.3"

# Push branch & tag:
git push origin release/0.8 --follow-tags
git push origin v0.8.3


⸻

2) Changelogs

2.1 Sources of Truth
	•	User-facing: docs/CHANGELOG.md
	•	Per-package (optional but recommended): */CHANGELOG.md
	•	Fragments: installers/ci/github/release-note-fragments.md (PR authors add bullets)

2.2 Preparing the Notes
	•	Aggregate PR fragments (label: release-note) since last tag.
	•	Group into Added / Changed / Fixed / Removed / Security.
	•	Link to issues/PRs; include migration notes when relevant (mempool policy, RPC params, etc.).

⸻

3) Artifacts

Artifact	Where	Notes
macOS .dmg & .pkg (Wallet/Explorer)	installers/wallet/macos/…, installers/explorer-desktop/macos/…	codesign + notarize + staple
Windows .msix / .exe (Wallet/Explorer)	installers/wallet/windows/…, installers/explorer-desktop/windows/…	Authenticode + TSA
Linux AppImage / Flatpak / DEB / RPM	installers/wallet/linux/…, installers/explorer-desktop/linux/…	GPG repo signing optional
Native ZK crate (animica_zk_native)	zk/native	release build + benches
SDKs (Py/TS/Rust)	sdk/python, sdk/typescript, sdk/rust	publish to PyPI, npm, crates.io as applicable
Browser extension	wallet-extension/	store uploads (manual or CI)
Website	website/	out-of-band; not version-locked

We ship SHA256 sums for all binary artifacts and update appcasts (macOS Sparkle) for beta and stable channels.

⸻

4) Signing & Verification

4.1 macOS (codesign + notarization + staple)
	•	Inputs:
	•	Team ID: installers/signing/macos/team_id.txt
	•	Notary creds: installers/wallet/macos/notarytool.json
	•	CI keychain scripts:
	•	installers/scripts/setup_keychain_macos.sh
	•	installers/scripts/import_p12_macos.sh
	•	Build & sign (wallet example; Explorer similar):

bash installers/wallet/macos/scripts/build_release.sh
bash installers/wallet/macos/sign_and_notarize.sh out/Animica-Wallet.dmg
bash installers/wallet/macos/scripts/staple.sh out/Animica-Wallet.dmg

	•	Verify locally:

bash installers/qa/tools/notarization_check.sh out/Animica-Wallet.dmg
spctl -a -vv out/Animica-Wallet.app

	•	Sparkle appcast signing:

python installers/updates/scripts/update_appcast.py \
  --channel stable --app wallet --file out/Animica-Wallet.dmg

bash installers/updates/scripts/sign_appcast_macos.sh \
  installers/wallet/macos/sparkle/ed25519_private.pem.enc \
  installers/updates/wallet/stable/appcast.xml

4.2 Windows (Authenticode)
	•	Cert subject: installers/signing/windows/cert_subject.txt
	•	TSA URLs: installers/signing/windows/timestamp_urls.txt
	•	Sign:

powershell -File installers/wallet/windows/codesign.ps1 `
  -FilePath out\Animica-Wallet.msix `
  -Thumbprint "<CERT_THUMBPRINT>"

	•	Verify:

powershell -File installers/qa/tools/msix_verify.ps1 -Path out\Animica-Wallet.msix

4.3 Linux
	•	AppImage: signed payload (optional), publish SHA256.
	•	Flatpak: built via manifest installers/wallet/linux/flatpak/*.yml.
	•	APT/YUM repo signing (optional):
	•	See installers/signing/linux/gpg/README.md.

4.4 Hashes

Always publish SHA256SUMS.txt and SHA256SUMS.txt.sig alongside releases:

(cd out && shasum -a 256 * > SHA256SUMS.txt)
gpg --sign --armor --output out/SHA256SUMS.txt.sig out/SHA256SUMS.txt


⸻

5) CI Pipelines (overview)

See:
	•	Wallet: installers/ci/github/wallet-{macos,windows,linux}.yml
	•	Explorer: installers/ci/github/explorer-{macos,windows,linux}.yml

Stages:
	1.	Build (cache toolchains; lockfiles verified)
	2.	Test (unit, integration, e2e; benches smoke)
	3.	Sign (on protected env; short-lived secrets)
	4.	Notarize (macOS)
	5.	Verify (signature checks)
	6.	Publish (GitHub Release assets; optionally stores/registries)
	7.	Appcast (update + sign for macOS channels)

Secrets: see installers/ci/github/secrets.example.md and installers/scripts/store_signing_secret_ci.md.

⸻

6) Reproducibility
	•	Lock files:
	•	Python: pyproject.toml + requirements*.txt (pinned when used)
	•	Node: package-lock.json / pnpm-lock.yaml
	•	Rust: Cargo.lock
	•	Toolchains pinned in CI images (matrix documented in docs/dev/BUILD_FROM_SOURCE.md).
	•	Embed build info:
	•	Git describe: core/version.py, rpc/version.py, etc.
	•	Show in --version and in About screens.
	•	SBOM (optional, recommended):
	•	Generate via cargo auditable / syft / cyclonedx.

⸻

7) Channels & Promotion
	•	beta: early adopters; appcast at installers/updates/*/beta/appcast.xml
	•	stable: promoted after 48–72h with no P0 issues; appcast at installers/updates/*/stable/appcast.xml

Promotion = re-sign updated appcast (no binary changes).

⸻

8) Hotfixes & Backports
	•	Branch: hotfix/x.y.z
	•	Keep diffs minimal; bump PATCH; tag vX.Y.Z+hotfix1 optional in notes.
	•	Repeat signing and verification; update appcasts.

⸻

9) Security Releases
	•	Coordinate via private channel; limited reviewers.
	•	Use embargo labels; do not mention exploit details pre-patch.
	•	Post-release write-up with CVE if issued; include mitigations and config flags.

⸻

10) Release Checklist (Manager)
	1.	Freeze: cut release/x.y, stop feature merges.
	2.	Version bump: update versions in:
	•	*/version.* files, package.json, Cargo.toml, pyproject.toml, wallet-extension/package.json, zk/native/Cargo.toml.
	3.	CHANGELOG: update docs/CHANGELOG.md.
	4.	CI green across:
	•	unit/integration/e2e, benches smoke, installers build & verify, website build.
	5.	Tag & sign: git tag -s vX.Y.Z.
	6.	Build artifacts (if not CI-built).
	7.	Sign/Notarize (macOS); Codesign (Windows); Hashes & optional GPG.
	8.	Verify: run installers/scripts/verify_signatures.sh plus platform checks.
	9.	Publish: create GitHub Release, upload artifacts + checksums.
	10.	Appcasts: update + sign (beta first).
	11.	Announce: update website (blog), release notes; link to hashes; share rollback steps.
	12.	Monitor: crash/error rates, RPC SLOs, user reports; decide on promotion to stable.

⸻

11) User Verification Guide (Copy-paste Friendly)

macOS

# Gatekeeper/Notarization:
spctl -a -vv "Animica Wallet.app"
codesign --verify --deep --strict --verbose=2 "Animica Wallet.app"

# Staple check:
xcrun stapler validate "Animica Wallet.dmg"

# Hash:
shasum -a 256 "Animica Wallet.dmg"

Windows (PowerShell)

Get-AuthenticodeSignature .\Animica-Wallet.msix
powershell -File installers/qa/tools/msix_verify.ps1 -Path .\Animica-Wallet.msix

Linux

sha256sum Animica-Wallet.AppImage
flatpak install --user io.animica.Wallet --assumeyes
rpm -K animica-wallet-*.rpm    # signature check, if repo-signed


⸻

12) Appendix — Package Publishing (Optional)

PyPI

cd sdk/python
python -m build
twine upload dist/*

npm

cd sdk/typescript
npm version 0.8.3
npm publish --access public

crates.io

cd sdk/rust
cargo publish

Native crate (binary artifacts)

cd zk/native
cargo build --release
cargo bench --bench verify_bench -- --warmup 2


⸻

13) Key Rotation & Policy

See installers/signing/policies.md for rotation cadence, revocation handling, and short-lived CI tokens.
Public signing material:
	•	Sparkle feed key (pub): installers/wallet/macos/sparkle/ed25519_public.pem
	•	macOS Team ID: installers/signing/macos/team_id.txt
	•	Windows TSA list: installers/signing/windows/timestamp_urls.txt

⸻

All releases must be reproducible, signed, and verifiable by end users with public documentation of steps and hashes.

