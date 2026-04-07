# Animica Studio Master Audit

## Snapshot
- Audit date: 2026-04-07
- Scope completed in this pass: startup/foundation hardening, wallet-core repair, base-unit correctness, CLI contract stabilization, ENA+DA local-ingest path repair, smoke-test expansion
- Current posture: usable release-candidate foundation with green startup and wallet-core smoke coverage; live operator flows still need final packaging and live-node validation

## Major Fix Record 1: Startup Reliability
- Broken: `MainWindow` construction could hang, `ConsolePage` synchronously probed CLI help during startup, read-only default app-data paths raised `OSError`, and background runnables emitted into deleted Qt objects during shutdown.
- Root cause: eager construction of heavy pages, synchronous CLI capability discovery on the UI path, brittle `~/.local/share` assumptions, and unguarded Qt signal emits from background workers.
- Files changed: `animica_studio/ui/main_window.py`, `animica_studio/services/cli_capabilities.py`, `animica_studio/services/cli_registry.py`, `animica_studio/services/workers.py`, `animica_studio/util/paths.py`, `tests/test_ui_smoke.py`, `tests/test_core_services.py`
- Validation:
  - direct offscreen constructor smoke now completes and closes cleanly
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_ui_smoke.py`
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_core_services.py`
- Remaining risks: packaged-mode Qt plugin/resource loading is still not exercised; CLI registry refresh is intentionally seeded/non-blocking and should be refreshed explicitly for deep diagnostics rather than on startup

## Major Fix Record 2: Wallet Create/List Wiring
- Broken: Wallet page exposed no real create action, wallet store path assumptions diverged from CLI reality, and Studio/runtime tests were not isolated from the operator wallet store.
- Root cause: placeholder UI handler, missing dialog/worker flow, and direct hardcoding of `~/.animica/wallets.json`.
- Files changed: `animica_studio/ui/pages/wallet_page.py`, `animica_studio/services/wallet_service.py`, `animica_studio/services/wallet_store.py`, `animica_studio/services/wallet_repository.py`, `animica_studio/ui/pages/tx_send_page.py`, `animica_studio/ui/pages/mining_page.py`, `animica_studio/util/paths.py`, `tests/conftest.py`, `tests/test_wallet.py`
- Validation:
  - real create-wallet dialog exists and validates label/algorithm before spawning worker
  - live isolated CLI smoke succeeded:
    - `ANIMICA_WALLETS_FILE=/tmp/animica-studio-live/wallets.json timeout 30s .venv/bin/python -m animica wallet create --label studio_smoke --alg dilithium3`
  - wallet/tx/dashboard/core smoke:
    - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_wallet.py apps/animica_studio/tests/test_dashboard_services.py apps/animica_studio/tests/test_core_services.py apps/animica_studio/tests/test_tx_service.py`
- Remaining risks: live send/balance behavior still needs a running node/RPC for final operator validation

## Major Fix Record 3: Balance and Amount Correctness
- Broken: Studio still treated ANM as `10^18` base units in multiple paths while the current chain/CLI/genesis use `10^9`.
- Root cause: stale Ethereum-style unit assumptions persisted across wallet models, explorer formatting, tx sending, dashboard totals, and template comments.
- Files changed: `animica_studio/models/wallet_models.py`, `animica_studio/services/explorer_client.py`, `animica_studio/services/explorer_balance_service.py`, `animica_studio/services/tx_service.py`, `animica_studio/services/wallet_service.py`, `animica_studio/storage/config.py`, `animica_studio/resources/templates/security/safe_arithmetic/content.py`, `tests/test_wallet.py`, `tests/test_tx_service.py`, `tests/test_dashboard_services.py`
- Validation:
  - unit-focused wallet/tx/dashboard tests now pass against 9-decimal expectations
  - wallet live-create smoke produced a valid wallet store with expected CLI output
- Remaining risks: explorer-backed live balance truth still needs verification against a real RPC/explorer pair

## Major Fix Record 4: Profile/Config Contract Repair
- Broken: legacy `Config.profiles` state and new `rpc_profiles` state could diverge, causing stale runtime behavior and incorrect active-profile lookups.
- Root cause: partial migration to `RpcProfile` without syncing legacy config fields used by older UI/service paths.
- Files changed: `animica_studio/models/profile_models.py`, `animica_studio/services/profile_service.py`, `animica_studio/storage/config.py`, `tests/test_core_services.py`
- Validation:
  - profile-service/core round-trip tests pass
  - header/profile selection smoke is covered by `test_main_window_smoke`
- Remaining risks: profile migration should still be manually exercised against real pre-existing operator configs

## Major Fix Record 5: ENA + DA Local Ingest Pathing
- Broken: ENA publish local-ingest still assumed `~/.animica/...` was writable, which breaks in restricted environments and test sandboxes and matches the known DA path-default problem.
- Root cause: container `/data/...` paths were hard-mapped to home-directory host paths instead of a writable Studio-controlled host root.
- Files changed: `animica_studio/services/ena_automation_service.py`, `tests/test_ena_guided_automation.py`, `tests/test_ena_contribution_engine.py`, `tests/test_publish_page.py`
- Validation:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_ena_guided_automation.py apps/animica_studio/tests/test_ena_contribution_engine.py apps/animica_studio/tests/test_full_auto_da_configure.py apps/animica_studio/tests/test_publish_page.py`
  - DA engine/status/contribution smoke:
    - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_da_engine.py apps/animica_studio/tests/test_da_status_service.py apps/animica_studio/tests/test_da_contribution.py`
- Remaining risks: real node mount visibility still depends on operator Docker/path configuration and must be validated on a real host

## Overall Validation Summary
- `167 passed` in wallet/dashboard/core/tx suites
- `40 passed` in UI/mining/AICF/DA/IDE suites
- `29 passed` in ENA guided/full-auto/publish suites
- `50 passed, 1 skipped` in DA engine/status/contribution suites
- direct wallet CLI create smoke succeeded against isolated `/tmp` wallet store

## Release Readiness View
- Green: startup, core wallet creation wiring, unit correctness, non-blocking CLI discovery, ENA local-ingest path fallback
- Yellow: live balances, live tx send, live node/sync truth, packaged mode, macOS/Linux bundle validation, full operator runbook against real services
- Red: none in the repaired foundation path; remaining work is integration/packaging validation rather than known startup blockers
