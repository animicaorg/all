# Animica Studio Status Matrix

| Area | Status | Validation | Notes |
|---|---|---|---|
| Launch / imports / main window | Green | direct offscreen constructor + `test_ui_smoke.py` | startup no longer blocks on CLI probing |
| App-data / env / path sanity | Green | direct constructor without writable home | falls back to `/tmp/animica-studio` when default path is read-only |
| Wallet create / list wiring | Green | live isolated CLI wallet create + wallet smoke suite | real dialog and worker flow now exist |
| Balance formatting / units | Green | wallet/dashboard/tx suites | standardized on 9 decimals |
| Tx send wiring | Yellow | `test_tx_service.py` + wallet tests | live broadcast still needs real node validation |
| Node status / sync surface | Yellow | dashboard + UI smoke | live-node truthfulness still needs operator run |
| Mining panel | Yellow | `test_mining_page.py` | command-path and UI smoke are green; real mining still needs node-backed validation |
| AICF page | Yellow | `test_aicf.py` | smoke is green; real credits/claim flow still needs service-backed validation |
| ENA publish / guided automation | Yellow | guided/full-auto/publish suites | local-ingest pathing repaired; real services still needed |
| DA configure / contribution | Yellow | DA engine/status/contribution suites | writable host-path defaults improved; real mount visibility still needs host validation |
| IDE / deterministic workspace | Yellow | `test_ide_page_workspace.py` | smoke green; full script-to-chain/operator flow still pending |
| Packaging / app bundle | Yellow | notes only | no packaged-mode smoke executed yet |
