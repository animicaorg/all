# Changelog

## Unreleased

- Fixed wallet switching by introducing a single active-wallet source of truth shared by UI and background, persisted at `chrome.storage.local.active_wallet_id` (existing `vaultData.currentAccount` is still maintained for compatibility).
- Fixed balance display by resolving the active wallet at call time, validating RPC chain-id, calling `state.getBalance` with the wallet address, and surfacing RPC errors instead of silently returning `0`.
- Added active-wallet change messaging (`WALLET_LIST`, `WALLET_GET_ACTIVE`, `WALLET_SET_ACTIVE`, `WALLET_ACTIVE_CHANGED`, `BALANCE_GET`) plus UI refresh and polling updates.
- Network policy can disable signature schemes; wallet now detects disabled schemes, fetches allowed on-chain policy, and guides remediation with a structured switch-account/operator-enable action.
