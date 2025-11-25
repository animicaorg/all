# Explorer Desktop Release — UX & Security Checklist

> Component: **Animica Explorer Desktop (Tauri wrapper)**  
> Platforms: **macOS**, **Windows**, **Linux (AppImage/DEB/RPM)**  
> Behavior: Loads a **default HTTPS URL** (embedded), supports **offline/fallback** view, optional deep links.

Use this checklist before promoting a build to **beta/stable**. Mark every item ✅ or file a blocker.

---

## 0) Build Provenance & Signing

- [ ] Version/tag on **About** screen matches release tag.
- [ ] Checksums (SHA256/SHA512) match CI artifacts.
- [ ] macOS: DMG/APP is **codesigned + notarized + stapled**; `spctl` and `codesign` verify.
- [ ] Windows: MSI/MSIX/EXE **signtool verify /pa /v** passes; timestamp present.
- [ ] Linux: package installs cleanly; if repo-signed, GPG metadata verifies.
- [ ] Artifacts list recorded in release notes (name, size, hashes).

---

## 1) First Launch & URL Loading

- [ ] Cold start < 2s to first paint on target hardware (no >1s white screen).
- [ ] App loads **embedded default URL** (see `tauri/build.rs` / config) over **HTTPS**.
- [ ] If app cannot reach the URL (airplane mode), it shows **offline fallback** UI (no crash).
- [ ] The **window title** and/or header shows the resolved **network** (if surfaced).
- [ ] User-supplied URL override (via config/CLI/env if supported) **only** accepts `https://` origins.

---

## 2) Navigation & Origin Safety

- [ ] Only **allowlisted origins** may be loaded in the main window.
- [ ] **External links** open in the system browser; they do **not** navigate the main webview.
- [ ] No in-app navigation to `file://`, `data:`, or `http://` (unencrypted) schemes.
- [ ] Back/forward/reload behavior sane; window cannot be script-resized beyond screen bounds.

---

## 3) Tauri Security Configuration

- [ ] `tauri.conf.json` **allowlist** is **minimal**:
  - [ ] `shell.open` scope restricted to `https://*` and `mailto:` only.
  - [ ] File system APIs **disabled** unless a read-only scoped path is required.
  - [ ] No `process`/`os` arbitrary execution access.
- [ ] Custom protocol handlers (e.g., `animica://`) validate input; no path traversal.
- [ ] `dangerousRemoteDomainIpcAccess` **unused**.
- [ ] `csp`/headers for the embedded site prevent inline JS where feasible (documented if not).
- [ ] Single-instance guard enabled; second launch focuses existing window (no duplicate processes).

---

## 4) RPC / WS Connectivity (Explorer Features)

- [ ] The loaded site connects to **RPC** and **WebSocket** endpoints and shows **live head** updates.
- [ ] Searching by **block hash/height**, **tx hash**, **address** works end-to-end.
- [ ] Receipt pages decode **status/logs** correctly; errors render gracefully for missing items.
- [ ] Network selector (if present) persists across restarts and respects allowlisted RPCs.

---

## 5) Deep Links (Optional)

If protocol handling is enabled:

- [ ] `animica://tx/<hash>` opens the transaction page.
- [ ] `animica://address/<bech32>` opens the address page.
- [ ] Malformed deep links are rejected with an error dialog (no crashes, no navigation).

---

## 6) Updates

> The desktop **updater is disabled by default** in config.

- [ ] Confirm **no auto-update** prompt appears (unless explicitly enabled for a channel).
- [ ] If an external update mechanism (e.g., Sparkle/Tauri updater) is **enabled** for a build:
  - [ ] Feed URL points to the correct channel (stable/beta).
  - [ ] Update signature verifies; post-update app reopens last viewed page and state persists.

---

## 7) Privacy & Telemetry

- [ ] No PII telemetry by default; analytics (if any) is **opt-in** and clearly disclosed.
- [ ] Logs redact cookies, tokens, Authorization headers, and RPC keys.
- [ ] Crash reports (if enabled) contain no navigation history or secrets.

---

## 8) Performance & Resource Use

- [ ] Steady memory after 5 minutes idle with a live WS subscription (no unbounded growth).
- [ ] Window resize maintains 60fps-ish on typical hardware; no jank on scrolling large tables.
- [ ] Relaunch time after update remains < 2s to first paint.

---

## 9) Internationalization & A11y

- [ ] All visible strings present for supported locales (e.g., en/es); no `i18n.key` leaks.
- [ ] Keyboard navigation (Tab/Shift+Tab) reaches all interactive elements.
- [ ] High-contrast/dark mode supported; color contrast meets WCAG AA for primary text.

---

## 10) Platform-Specific Checks

### macOS
- [ ] Hardened runtime entitlements: no JIT; network only; file access minimal.
- [ ] Gatekeeper first-run succeeds; notarization **stapled** on DMG.
- [ ] App icon crisp in Dock and Finder; menu items work (Hide, Quit).

### Windows
- [ ] MSIX capabilities minimal; no unrestricted broad file system access.
- [ ] `signtool verify /pa /v` passes; SmartScreen shows **verified publisher**.
- [ ] High-DPI scaling, Snap layouts, and dark mode respected.

### Linux
- [ ] AppImage launches on Wayland & X11; system font rendering OK.
- [ ] DEB/RPM install/uninstall clean; desktop file entries (icons, categories) correct.

---

## 11) Uninstall & Residuals

- [ ] Uninstall removes application binaries.
- [ ] Config/cache location documented; optional removal flow leaves system clean.

---

## 12) Quick Commands

**macOS**
```bash
spctl -a -vv Animica-Explorer.dmg
xcrun stapler validate Animica-Explorer.dmg
codesign --verify --deep --strict --verbose=2 /Applications/Animica\ Explorer.app

Windows (PowerShell)

Get-FileHash .\Animica-Explorer.msix -Algorithm SHA256
signtool verify /pa /v .\Animica-Explorer.msix

Linux

sha256sum Animica-Explorer-x86_64.AppImage
sudo apt install ./animica-explorer_*.deb    # or: sudo rpm -Uvh animica-explorer-*.rpm


⸻

13) Release Gate

A build passes if:
	•	✅ All signing/notarization checks pass on at least one artifact per OS family.
	•	✅ First launch, URL load, core navigation, and WS live heads work.
	•	✅ (If enabled) Update mechanism verifies signature and preserves state.
	•	✅ No navigation to non-HTTPS or non-allowlisted origins is possible.

⸻

Owner: Release Engineering + Explorer Team
Last updated: YYYY-MM-DD
