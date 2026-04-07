# GUI Status Matrix

Date: 2026-04-07

| GUI | Path | Status | Evidence | Next gate |
| --- | --- | --- | --- | --- |
| Wallet Qt | `wallet-qt/` | Unvalidated | No build or run proof in this pass | Add build and launch smoke |
| Browser wallet extension | `wallet-extension/`, `apps/wallet-extension/` | Blocked / drifted | Focused extension RPC client behavior is not stabilized | Align with current RPC contract |
| Miner GUI | `apps/miner-gui/` | Unvalidated | No smoke run in this pass | Add build smoke and backend connectivity check |
| Miner dashboard | `apps/miner-dashboard/` | Unvalidated | No smoke run in this pass | Add backend data contract smoke |
| Miner wallet flutter app | `apps/miner-wallet-flutter/` | Unvalidated | No smoke run in this pass | Add build and wallet flow smoke |
| Studio desktop or local app surfaces | `apps/animica_studio/` | Partially validated | Python tests exist, but no UI smoke in this pass | Prove wallet and provider flow end to end |
