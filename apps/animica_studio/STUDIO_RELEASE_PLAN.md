# Animica Studio Release Plan

## Phase 1: Foundation
- Done: writable app-data fallback, non-blocking CLI capability path, lazy-loading of heavy pages, safer worker shutdown emits, main-window smoke restored
- Done: startup smoke and offscreen Qt validation

## Phase 2: Wallet Core
- Done: real create-wallet dialog and worker flow
- Done: wallet store path alignment via `ANIMICA_WALLETS_FILE`
- Done: ANM decimal correction to `10^9`
- Done: tx-send page wallet refresh hardening
- Next: validate live send/balance against a running node/RPC pair

## Phase 3: Network / Node / Mining
- Done: startup-safe construction of node/mining surfaces
- Done: mining smoke suite green
- Next: manual/live verification for node status, sync truthfulness, and stalled-sync diagnostics

## Phase 4: AICF / ENA / DA / IDE
- Done: AICF/DA/IDE smoke suites green
- Done: ENA publish local-ingest path fallback repaired
- Done: ENA combined smoke restored by fixing Qt app test interaction
- Next: run live publish/register/claim flows against actual services

## Phase 5: Packaging / Polish
- Done: documented run/smoke commands and release status
- Next: exercise packaged mode, Qt resource/plugin discovery, Linux/macOS bundle notes, and operator runbook finalization

## Immediate Next Work
1. Run Studio against a real local or testnet node and capture live screenshots/logs for wallet balance, tx send, node head, sync, mining, AICF, ENA publish, and DA configure.
2. Validate PyInstaller or equivalent packaged launch on Linux, then record plugin/resource/path fixes.
3. Review page-by-page information architecture after live use and remove any remaining stale or duplicative operator actions.
