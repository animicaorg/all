# Animica Explorer — Desktop (Tauri)

This directory contains packaging notes and ops guidance for the **Explorer Desktop** app built with **[Tauri](https://tauri.app/)**.

> TL;DR: We use **Tauri** because it pairs a tiny, Rust-native shell with a secure WebView for a **small footprint**, **fast startup**, and **tight OS integration**—while still letting us ship the Explorer UI written in web tech.

---

## Why Tauri?

### Small runtime + fast startup
- **Binary size**: Tauri links against the system webview (WebKitGTK / WebView2 / WKWebView), so we **don’t ship Chromium**. Typical release sizes are **5–20 MB** instead of hundreds.
- **Memory**: Single WebView + Rust process keeps idle memory low compared to Electron.
- **Startup**: No bundled browser → quicker cold starts; great for “open and check a block” workflows.

### Security & OS integration
- **Rust core** with narrow IPC: We expose only the commands we need, all statically typed and audited.
- **Capability model**: No Node.js in the renderer; file system, clipboard, shell, etc. are opt-in and off by default.
- **Auto-update hooks** (optional) and **code signing** on macOS/Windows for trust and integrity.

### Developer ergonomics
- Keep authoring UI in **TypeScript/React** (reusing `studio-web` components if desired).
- Tauri **CLI** glues the web bundle and the Rust shell into OS-specific installers.

---

## Two modes: Offline (Bundled) vs. Remote URL

Explorer Desktop can run in two distinct modes—choose per channel or environment.

### 1) Offline Mode (bundled assets)
The web app (`/dist`) is **packaged inside** the executable. This ensures the Explorer loads and browses **without network** access (useful for offline validation or air-gapped environments).

**Pros**
- Works offline / behind strict firewalls.
- Reproducible UI snapshot; pinned via checksums.
- No mixed-content or CSP surprises; single origin.

**Cons**
- To update the UI, you must **ship a new app build** (or use Tauri’s updater with signed releases).

**Enable**
- Build the web UI (e.g., `pnpm build` or `yarn build`) and point Tauri to `/dist`.
- Set `EXPLORER_MODE=offline`.

### 2) Remote URL Mode
The shell points WebView to a **remote** URL (e.g., `https://explorer.animica.dev`) and streams the latest UI from CDN.

**Pros**
- Instant UI updates with no desktop reinstall.
- Perfect for fast iteration and A/B testing.

**Cons**
- **Requires network**; offline is degraded (we can optionally cache last-known).
- Need stricter **CSP** and TLS pinning considerations.
- Remote availability affects app startup unless an offline fallback is cached.

**Enable**
- Set `EXPLORER_MODE=url` and `EXPLORER_URL=https://explorer.animica.dev`.
- Consider `EXPLORER_URL_FALLBACK=file://…/dist/index.html` to keep a cached last-known-good.

> **Recommended defaults**
> - **Stable channel** → **Offline mode** for deterministic UX.
> - **Beta/Dev channel** → **Remote URL** with a bundled **fallback** for resilience.

---

## Configuration

All settings are read at build time (and some at runtime via Tauri `conf.json` and Rust env):

| Variable                  | Example                              | Description                                             |
|---------------------------|--------------------------------------|---------------------------------------------------------|
| `EXPLORER_MODE`           | `offline` or `url`                   | Bundled assets vs. remote URL.                          |
| `EXPLORER_URL`            | `https://explorer.animica.dev`       | Only used in `url` mode.                                |
| `EXPLORER_URL_FALLBACK`   | `app://localhost/index.html`         | Optional offline fallback.                              |
| `CHAIN_ID`                | `1`                                  | Default chain (animica main/test/dev).                  |
| `RPC_URL`                 | `https://rpc.animica.dev`            | Default RPC endpoint for live queries.                  |
| `WS_URL`                  | `wss://rpc.animica.dev/ws`           | Default WS endpoint (heads/events).                     |
| `AUTO_UPDATE`             | `true`                               | Enable Tauri updater (channel-specific).                |
| `CHANNEL`                 | `stable` / `beta` / `dev`            | Updater feed & branding toggles.                        |

> The app should **only** expose read-only features (block/tx/address/contract views). Write paths (signing, state changes) are left to the Wallet app.

---

## App Architecture

+–––––––––––––+
| Rust (tauri::App)       |  — window mgmt, menu, auto-update, shell
|  ├─ IPC: explorer_*     |  — typed, minimal command surface
|  └─ URL policy          |  — allowlist origins, CSP
+————+———––+
|
v
+–––––––––––––+
| WebView (WK/WebView2/GTK)|
|  └─ Explorer UI (React)  |
|      - Routes: blocks, txs, accounts, contracts
|      - Uses RPC/WS for live data
+–––––––––––––+

**IPC surface** (minimal):
- `explorer.getVersion()` → returns app + UI version.
- `system.openExternal(url)` → opens default browser (allowlist-only).
- (Optional) `file.saveAs()` for exporting reports (offline mode only; uses Tauri FS permission).

**Networking**:
- The renderer fetches RPC/WS directly; the Rust layer enforces **origin allowlists** and **TLS** (OS trust). For remote mode, the initial URL is pinned to `EXPLORER_URL`.

---

## Building & Running

Requirements:
- Rust stable (toolchain for your OS), Tauri CLI (`cargo install tauri-cli` or via NPM `@tauri-apps/cli`).
- Node.js + package manager to build the web UI.

### Dev (URL mode recommended)
```bash
# 1) Start web UI dev server
pnpm dev  # or yarn dev / npm run dev

# 2) Launch tauri shell pointing to local URL
EXPLORER_MODE=url \
EXPLORER_URL=http://localhost:5173 \
cargo tauri dev

Release (offline mode)

# 1) Build UI bundle
pnpm build

# 2) Build signed binaries/installers via Tauri
EXPLORER_MODE=offline \
CHAIN_ID=1 RPC_URL=https://rpc.animica.dev WS_URL=wss://rpc.animica.dev/ws \
cargo tauri build

Artifacts:
	•	macOS: .app / .dmg (see signing below)
	•	Windows: .msi / .msix per Tauri target
	•	Linux: .AppImage / .deb / .rpm (depending on pipeline)

⸻

Code Signing & Updates
	•	macOS: Sign + notarize; reuse wallet signing infra (installers/wallet/macos/*) or a dedicated Explorer identity. Hardened runtime, no JIT, network allowed.
	•	Windows: Sign with EV/Code Signing cert via signtool. MSIX preferred; optional Winget feed.
	•	Linux: Sign packages via repo metadata (apt/dnf), and provide checksums for AppImage.

Auto-update (optional):
	•	Use Tauri’s updater with channel appcasts (stable, beta).
	•	Every release must sign the update JSON/ZIP with the configured public key.
	•	For offline mode, updater replaces the entire app. For URL mode, updates are less frequent (URLs provide latest UI).

⸻

Security Model
	•	Renderer has no Node; access to OS APIs only through audited Rust commands.
	•	CSP: strict-default with script/style hashes for offline builds. For remote builds, use upgrade-insecure-requests: off, enforce HTTPS, and set connect-src to RPC/WS allowlist.
	•	Origin policy: Only EXPLORER_URL and known RPC hosts are permitted; block javascript: URLs and file origins unless in offline mode, where app:// is used.
	•	No signing keys; this app is read-only. All signing lives in the Wallet.

⸻

Offline vs URL: Operational Guidance

Topic	Offline	URL
Start without net	✅	❌ (unless cached fallback is present)
UI updates	Ship new build / Tauri updater	Deploy to CDN
Determinism	Strong (UI pinned + checksums)	CDN-driven (must pin commit/lockfiles)
Incident mgmt	Slower (build/ship)	Fast (rollback CDN)
Security	Narrow (no remote origin)	Needs strict CSP + origin gate

Hybrid approach recommended:
	•	Ship offline by default with cached last-known.
	•	In dev/beta, enable URL for rapid iteration. Provide a “Use cached UI” toggle in settings.

⸻

CI/CD Outline
	1.	Build UI (production), generate checksums & lockfiles (documented in zk/docs/REPRODUCIBILITY.md).
	2.	cargo tauri build for target triples (macOS, Windows, Linux).
	3.	Sign artifacts per OS.
	4.	Publish:
	•	Offline channel: upload installers + checksums.
	•	URL channel: deploy UI to CDN; update appcast if updater enabled.
	5.	Smoke test (launch, load block pages, search, WS heads).

We can reuse common scripts from wallet installers where applicable (keychain setup, import certs, verification).

⸻

Telemetry & Privacy
	•	No telemetry by default. If we ever add opt-in diagnostics, they must be explicit and minimal.
	•	The app only calls the RPC/WS endpoints configured by the user/channel.

⸻

Troubleshooting
	•	Blank window: Verify CSP and the EXPLORER_URL origin. On Linux, confirm WebKitGTK/WebView2 presence.
	•	CORS/RPC errors: The Explorer must target a node with CORS that allows the Explorer’s origin (for remote URL).
	•	Slow render: Disable animations / reduce expensive charts; verify GPU acceleration on the system.

⸻

License & Notices
	•	Explorer Desktop shell is licensed under the repo’s main license.
	•	See installers/LICENSE-THIRD-PARTY.md for third-party notices (Tauri, webview backends, OS SDKs).

