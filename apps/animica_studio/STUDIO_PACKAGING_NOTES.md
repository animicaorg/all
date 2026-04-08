# Animica Studio Packaging Notes

## Current Runtime Expectations
- install Studio extras into repo venv:
  - `.venv/bin/pip install -e 'apps/animica_studio[dev]'`
- install packaging tooling when building release artifacts:
  - `.venv/bin/pip install -e 'apps/animica_studio[package]'`
- for headless smoke:
  - `QT_QPA_PLATFORM=offscreen`
- writable runtime override when needed:
  - `ANIMICA_STUDIO_APP_DATA_DIR=/tmp/animica-studio`
- isolated wallet-store override:
  - `ANIMICA_WALLETS_FILE=/tmp/animica-studio-wallets.json`

## Hardening Added In This Pass
- app-data automatically falls back to a writable temp directory when the default user-data path is read-only
- startup no longer requires synchronous CLI help probing
- heavy pages are lazy-loaded, so packaged launch is less sensitive to optional subsystems on first paint
- worker signal emission is hardened against deleted Qt objects during shutdown
- packaging scripts now emit platform-native release artifacts: Linux `.deb`, macOS `.app`, Windows `.exe`

## Packaging Risks Still Open
- packaged Qt plugin/resource discovery has not been exercised
- CLI discovery in packaged mode may still need an explicit bundled CLI path or documented external-CLI requirement
- ENA/DA local-ingest features require real host-path and node-mount validation on the packaged target machine
- macOS signing/notarization, Windows code-signing, and installed-package validation remain unvalidated

## Suggested Packaging Smoke
1. Launch packaged Studio with a clean writable app-data dir.
2. Confirm icons, styles, fonts, and shell navigation render.
3. Open Wallet, Console, Mining, AICF, DA, and ENA pages once each to force lazy loading.
4. Verify logs are written under the packaged app-data dir.
5. Validate CLI path resolution and override behavior from Settings.
