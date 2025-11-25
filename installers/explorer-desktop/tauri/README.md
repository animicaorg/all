# Animica Explorer — Tauri Shell

This crate is the lightweight desktop shell for the Animica Explorer UI. It uses **Tauri v2**, a Rust runtime with a system WebView, so binaries are small, fast to start, and easy to sign/notarize.

- **Modes**: Offline (bundled UI) or URL (remote UI) with optional offline fallback.
- **Security**: No Node in renderer; strict CSP; origin allowlist; single-instance guard.
- **Ops**: Updater disabled by default; code signing handled by platform installers.

---

## Requirements

- **Rust** (stable) and `cargo`.
- **Tauri CLI** (optional convenience): `cargo install tauri-cli` or `npm i -g @tauri-apps/cli`.
- **Node** + your package manager to build the Explorer web UI for offline mode.
- Platform toolchains for packaging (see `installers/*` for signing/notarization details).

---

## Quick Start

### Dev (URL Mode)
Point the shell at your local dev server:

```bash
# 1) Start the web UI dev server (replace with your command)
pnpm dev

# 2) Launch the Tauri shell
cd installers/explorer-desktop/tauri
EXPLORER_MODE=url \
EXPLORER_URL=http://localhost:5173 \
cargo tauri dev

Release (Offline Mode)

Bundle a production build of the Explorer UI into the app binary:

# 1) Build the web UI (outputs to ../dist by convention)
pnpm build

# 2) Build the signed app
cd installers/explorer-desktop/tauri
EXPLORER_MODE=offline \
cargo tauri build

The default distDir is ../dist (see tauri.conf.json and Cargo.toml metadata). Adjust as needed.

⸻

Build Modes
	•	Offline (default)
UI is bundled inside the app. Works fully offline; deterministic UX.
Enable via EXPLORER_MODE=offline or the offline feature (default feature).
	•	URL Mode
App loads a remote URL (e.g., CDN-hosted Explorer). Fast UI iteration; requires network.
Enable via EXPLORER_MODE=url and set EXPLORER_URL.

We recommend offline for stable channel, URL for beta/dev with an offline fallback.

⸻

Configuration

Environment Variables

Var	Example	Purpose
EXPLORER_MODE	offline | url	Choose bundled vs remote UI.
EXPLORER_URL	https://explorer.animica.dev	Remote URL when in URL mode.
EXPLORER_URL_FALLBACK	app://localhost/index.html	Fallback if remote fails.
EXPLORER_ALLOWED_HOSTS	explorer.animica.dev,rpc.animica.dev	Host allowlist for open_external and URL validation.
EXPLORER_DEVTOOLS	1	Open DevTools in debug builds.
EXPLORER_HIDE_ON_CLOSE	1	Hide window on close (instead of quitting).

Defaults for EXPLORER_URL and EXPLORER_URL_FALLBACK are embedded at build time via build.rs.
Values are also read from ../config/app_metadata.json if present.

Cargo Features
	•	offline (default) – build for bundled UI.
	•	url-mode – explicitly prefer URL mode in some pipelines.
	•	updater – enable Tauri’s signed auto-updater (off by default).
	•	Optional plugin features (if you wire them):
	•	shell-open – enables open-in-browser via tauri-plugin-shell.
	•	clipboard – enables clipboard read/write via tauri-plugin-clipboard-manager.

See src/cmd.rs for optional commands wiring.

⸻

Security Hardening
	•	CSP is set in tauri.conf.json (security.csp) and mirrored in Cargo metadata. Tighten as your UI requires.
	•	Origin allowlist: EXPLORER_ALLOWED_HOSTS is enforced in open_external (see src/cmd.rs).
	•	Single instance: subsequent launches focus the existing window and forward deep links.
	•	No Node.js in renderer; IPC surface is minimal and typed.

⸻

Project Layout

tauri/
├─ Cargo.toml            # crate metadata + Tauri bundler config (also see tauri.conf.json)
├─ tauri.conf.json       # explicit config used by Tauri v2
├─ build.rs              # embeds default URLs & marks assets for rebuilds
├─ src/
│  ├─ main.rs            # window creation, single-instance, command registration
│  └─ cmd.rs             # optional commands (open, clipboard) behind feature flags
└─ icons/                # app icons referenced by bundler (optional)

The packaged web UI for offline mode should live adjacent at ../dist by default.

⸻

Updater

The updater is disabled by default in tauri.conf.json and Cargo.toml.
To enable:
	1.	Provide a signed update feed endpoint.
	2.	Set updater.active=true and a public key.
	3.	Build with the updater feature.

⸻

CI / Packaging

Typical pipeline (see repo-level installers):
	1.	Build Explorer UI for production → ../dist with checksums.
	2.	cargo tauri build for each target triple.
	3.	Sign artifacts per-OS:
	•	macOS: codesign + notarize (see installers/wallet/macos/* for scripts).
	•	Windows: signtool for MSIX/MSI (see installers/wallet/windows/*).
	•	Linux: AppImage/Deb/RPM; publish checksums.
	4.	(Optional) Publish updater appcast per channel (stable/beta).

⸻

Troubleshooting
	•	Blank screen: Verify EXPLORER_MODE and the devPath/distDir paths. For URL mode, check network/CORS.
	•	Dev server not loading: Confirm devPath matches your dev server URL and that it serves an index.
	•	External links blocked: Add host to EXPLORER_ALLOWED_HOSTS.
	•	Linux startup issues: Ensure WebKitGTK/WebView dependencies listed in tauri.conf.json / Cargo metadata are present.

⸻

License & Notices

This app is distributed under the repository’s main license.
Third-party notices: see installers/LICENSE-THIRD-PARTY.md.

