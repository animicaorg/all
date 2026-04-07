# Animica Studio Smoke Tests

## Canonical Smoke Scripts
- `scripts/smoke_start_studio.sh`
- `scripts/smoke_wallet_flow.sh`
- `scripts/smoke_network_flow.sh`
- `scripts/smoke_ena_aicf_da.sh`

## Commands Validated In This Audit
- Startup/UI:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_ui_smoke.py`
- Wallet/core:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_wallet.py apps/animica_studio/tests/test_dashboard_services.py apps/animica_studio/tests/test_core_services.py apps/animica_studio/tests/test_tx_service.py`
- Network/mining/AICF/DA/IDE:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_ui_smoke.py apps/animica_studio/tests/test_mining_page.py apps/animica_studio/tests/test_aicf.py apps/animica_studio/tests/test_da_client.py apps/animica_studio/tests/test_ide_page_workspace.py`
- ENA/advanced DA:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_ena_guided_automation.py apps/animica_studio/tests/test_ena_contribution_engine.py apps/animica_studio/tests/test_full_auto_da_configure.py apps/animica_studio/tests/test_publish_page.py`
- DA contribution/status:
  - `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q apps/animica_studio/tests/test_da_engine.py apps/animica_studio/tests/test_da_status_service.py apps/animica_studio/tests/test_da_contribution.py`
- Live wallet CLI sanity:
  - `ANIMICA_WALLETS_FILE=/tmp/animica-studio-live/wallets.json timeout 30s .venv/bin/python -m animica wallet create --label studio_smoke --alg dilithium3`

## Expected Coverage
- startup smoke: main window construction, lazy page shell, theme primitives, console page construction
- wallet smoke: create/list contract, RPC-dependent `wallet show` path, balance unit handling, tx service contract
- network smoke: dashboard/node/mining/AICF/DA/IDE surface construction and service smoke
- ENA/AICF/DA smoke: guided publish, full-auto DA configure, publish page, DA engine/status/contribution

## Manual Follow-Up Still Required
- attach the wallet smoke to a live node/RPC if you want `wallet show` to assert real on-chain balances instead of expected offline failure handling
- run Studio against a real node and validate live balance, send, sync, mining, AICF credits, ENA publish, and DA configure
- run packaged binary smoke on Linux and macOS once packaging artifacts exist
