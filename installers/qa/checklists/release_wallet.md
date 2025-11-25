# Wallet Release — UX & Security Checklist

> Component: **Animica Wallet (Flutter desktop)**  
> Platforms: **macOS**, **Windows**, **Linux (AppImage/DEB/RPM/Flatpak)**  
> Keys: **Post-quantum** (Dilithium3 default; SPHINCS+ fallback)  
> Address format: **bech32m** `anim1…`  

Use this checklist before promoting a build to **beta/stable**. Mark every item ✅ or file a blocker.

---

## 0) Build Provenance & Signing

- [ ] Version/tag matches app **About** screen.
- [ ] Checksums (SHA256/SHA512) match CI artifacts.
- [ ] macOS: DMG/APP is **codesigned + notarized + stapled**; `spctl` and `codesign` verify.
- [ ] Windows: MSIX/EXE **signtool verify /pa /v** passes; timestamped (primary or backup TSA).
- [ ] Linux: package installs cleanly; if repo-signed, metadata GPG signature verifies.
- [ ] Update feeds (Sparkle appcasts / WinGet) reference **this** version and correct hashes.

---

## 1) First-Run & Onboarding (UX)

- [ ] Cold start < 3s on target hardware; no blank white screen > 1s.
- [ ] Onboarding language + theme default sensible; can switch to dark/light.
- [ ] Create new wallet: shows **mnemonic** (12/24 words) with **explicit warnings**:
  - [ ] “Do not screenshot / share” copy present.
  - [ ] **Confirm phrase** step with randomized word indices.
  - [ ] “I understand” / risk acknowledgement checkbox before proceed.
- [ ] Import wallet: accepts mnemonic (with checksum), encrypted keystore file.
- [ ] Error copy is clear for invalid or partial inputs (bad word, wrong checksum, wrong file).

---

## 2) Key Management & Vault Security

- [ ] Default **PQ signer = Dilithium3**; fallback path **SPHINCS+** is labeled.
- [ ] Address derive = `alg_id || sha3_256(pubkey)` → bech32m `anim1…`; round-trip decode works.
- [ ] Vault encryption:
  - macOS: Keychain used (kSecAttrAccessibleAfterFirstUnlock / or documented).
  - Windows: DPAPI user/protected; no plaintext secrets on disk.
  - Linux: Secret Service if available; else encrypted file with strong KDF (PBKDF2/HKDF-SHA3).
- [ ] **Auto-lock** timer configurable; default ≤ 10 minutes; manual lock works.
- [ ] Clipboard: mnemonic/secret never copied automatically; copy button is explicit; clears after N seconds.
- [ ] Screens with secrets have **“hide/show”** toggle and warn on screenshots (if OS signals are available).
- [ ] Multiple accounts: create/rename/delete flows; primary account clearly indicated.

---

## 3) Network, Chain & RPC Safety

- [ ] Default **RPC URL** and **chainId** match shipped network presets (main/test/dev).
- [ ] Network switch: updates head height & chain params; persisted across restarts.
- [ ] **ChainId mismatch** on tx/sign **blocks** with actionable error (no silent signing).
- [ ] TLS: rejects invalid certs; descriptive error; option to add **trusted** self-signed only in dev.
- [ ] Rate-limit & exponential backoff on RPC failures; UI surfaces “offline” state cleanly.
- [ ] **Read-only** calls (balance, head) do not require unlocked vault.

---

## 4) Send / Receive UX

- [ ] Receive screen: QR code (bech32m) and copy button; checksum/HRP displayed.
- [ ] Send flow:
  - [ ] Address validation (bech32m HRP + length + checksum).
  - [ ] Amount validation (≥ dust; ≤ balance; decimals rules).
  - [ ] **Fee estimator** present; editable tip; shows final fee & total.
  - [ ] **Simulate** preflight (when RPC supports) → RESULT OK before enabling Sign.
  - [ ] Confirmation modal displays: from/to, amount, fee, **chainId**, **nonce**, **signing alg**.
  - [ ] Signing requires unlocked vault and per-tx confirmation.
  - [ ] Progress: pending → confirmed; receipt view (status/gas/logs).
- [ ] Failed tx: clear error (InsufficientBalance, OOG, NonceTooLow/Gap, ChainIdMismatch).
- [ ] Recent activity list stable across restarts; pagination works.

---

## 5) Post-Quantum Crypto Correctness

- [ ] **Sign/verify** self-test at startup (local vector) passes (Dilithium3).
- [ ] Domain separation: `SignBytes` encodes exact fields (chainId, kind, nonce, accessList, etc.).
- [ ] Address ↔ public key mapping stable; address book entry survives import/export.
- [ ] If a PQ backend feature-flags off, UI disables algorithm (no silent fallback).

---

## 6) Dapp / Provider (if surfaced in desktop)

- [ ] Provider session prompts show **origin** and **requested permissions**.
- [ ] Approvals are **per origin**; revocation UI available.
- [ ] Re-entrancy guarded: one approval at a time; background windows do not auto-focus sensitive UI.
- [ ] Message signing disclaimer and preview of sign bytes (humanized + hex).

---

## 7) Privacy & Telemetry

- [ ] Default **no PII telemetry**; analytics (if any) is **opt-in** with clear copy.
- [ ] Logs redact secrets, mnemonics, private keys, seeds, and RPC authorization headers.
- [ ] Crash dumps (if enabled) exclude vault/seed content.

---

## 8) Updates (per OS)

- **macOS (Sparkle)**  
  - [ ] Appcast URL matches channel (stable/beta).  
  - [ ] Ed25519 signature verified; update → relaunch preserves state.
- **Windows**  
  - [ ] MSIX incremental update succeeds; `signtool` verify on the updated package.  
  - [ ] WinGet manifest points to current version.
- **Linux**  
  - [ ] AppImage replacement works.  
  - [ ] DEB/RPM repo updates pull the new version; GPG metadata trusted.  
  - [ ] Flatpak (if shipped) updates via portal without extra privileges.

---

## 9) Anti-Phishing UX

- [ ] Warning for known-bad addresses (if denylist shipped); distinct danger color.
- [ ] Visual checksum / partial address highlighting; copy-paste verification step optional.
- [ ] External links open in default browser with confirmation.

---

## 10) Internationalization & A11y

- [ ] English/Spanish strings present (if included); no key leaks (`i18n.key.name` shown).
- [ ] Tab order, focus rings, screen-reader labels on critical buttons.
- [ ] Color contrast meets WCAG AA for primary text & buttons.

---

## 11) Persistence & Uninstall

- [ ] Config/state path documented per OS; user data survives update; can be reset from Settings.
- [ ] Uninstall leaves no orphaned executables/services; optional data removal flow (documented).

---

## 12) Performance Sanity

- [ ] Typical send flow < 5s on testnet (RPC latency aside).
- [ ] Memory steady after idle 5 minutes; no unbounded growth.
- [ ] Background timers pause when app unfocused (no busy loops).

---

## 13) Dependency & Supply-Chain

- [ ] Flutter/Dart, native plugins versions recorded in release notes.
- [ ] Licenses for bundled third-party packages present (`installers/LICENSE-THIRD-PARTY.md`).
- [ ] Reproducible build notes match `zk/docs/REPRODUCIBILITY.md` expectations where applicable.

---

## 14) Platform-Specific Checks

### macOS
- [ ] Hardened runtime entitlements: no JIT, network allowed, file access minimal.
- [ ] App sandboxing (if used) allows required directories only.
- [ ] Gatekeeper first-run dialog text sane; app icon crisp.

### Windows
- [ ] MSIX capabilities minimal; no unconstrained broadFileSystemAccess unless justified.
- [ ] UAC prompts only when required (install time).
- [ ] High-DPI scaling and dark mode respected.

### Linux
- [ ] Wayland + X11 run; clipboard & file pickers work.
- [ ] Flatpak portals used for FS/network if applicable.

---

## 15) Final “Go / No-Go” Snapshot

- [ ] All sections above marked ✅.
- [ ] Release notes drafted using `installers/ci/github/release-note-fragments.md` template.
- [ ] Appcast/WinGet manifests updated; artifacts uploaded; checksums published.
- [ ] Rollback plan documented (previous installers available; feeds can be reverted).

---

### Appendix — Quick Commands

**macOS**
```bash
spctl -a -vv Animica-Wallet.dmg
xcrun stapler validate Animica-Wallet.dmg
codesign --verify --deep --strict --verbose=2 /Applications/Animica\ Wallet.app

Windows (PowerShell)

Get-FileHash .\Animica-Wallet.msix -Algorithm SHA256
signtool verify /pa /v .\Animica-Wallet.msix

Linux

sha256sum Animica-Wallet-x86_64.AppImage
sudo apt install ./animica-wallet_*.deb   # or rpm -Uvh animica-wallet-*.rpm


⸻

Owner: Release Engineering + Wallet Team
Last updated: YYYY-MM-DD
