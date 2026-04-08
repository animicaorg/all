# Qt Wallet Architecture

## Overview

The wallet is split into UI, controller/service, persistence, and background execution layers. Qt widgets do not directly shell out to CLI commands or construct raw wallet files.

```text
Qt Widgets
  ├─ AccountsWidget / SendWidget / ReceiveWidget / TransactionHistoryWidget
  ├─ AddressBookWidget / ContractInteractionWidget / SettingsWidget
  └─ NodeControlWidget

Wallet Services
  ├─ WalletEngine
  ├─ BalanceTracker
  ├─ TransactionMonitor
  ├─ WalletDatabase
  └─ AnimicaRpcClient

Bridge Layer
  └─ AnimicaWalletBackend
       -> python -m animica.qt_wallet_bridge

Canonical Python
  ├─ python/animica/wallet/*
  ├─ python/animica/cli/wallet.py
  ├─ repo RPC / explorer endpoints
  └─ sdk/python/omni_sdk/*
```

## Canonical Wallet Integration

`WalletEngine` no longer owns a private wallet format. It opens the canonical `wallets.json` store and delegates wallet lifecycle operations to `python/animica/qt_wallet_bridge.py`.

That bridge provides:

- supported algorithm discovery
- wallet creation/import/export/default-wallet management
- canonical address validation
- transaction submission and status lookup
- history aggregation from explorer/indexer sources plus local pending state
- contract read/write and payload preview

The bridge is invoked through `AnimicaWalletBackend`, which centralizes Python lookup, `PYTHONPATH` wiring in the repo, timeout handling, JSON parsing, and safe error propagation.

## Wallet Storage

Wallet-related data is split by responsibility:

- `wallets.json`: canonical Animica wallet store
- `address_book.json`: local contacts, JSON versioned and CSV/JSON importable
- `wallet.db`: local cache for pending ledger effects, history decoration, and reconciliation state
- `QSettings`: UI/network preferences and recent recipients/contracts

The wallet deliberately does not claim encryption support because the canonical upstream `wallets.json` format is currently plaintext.

## Send / Receive Flow

### Send

1. `SendWidget` collects user input and performs local validation.
2. `WalletEngine::validateAddress()` checks the recipient through the canonical Python path.
3. `BalanceTracker` and `WalletDatabase` provide confirmed and reserved balance context.
4. `WalletEngine::submitTransaction()` calls the bridge, which uses the canonical Animica transaction path.
5. On success the wallet stores the pending transaction locally, reserves fee/amount in the ledger cache, and starts `TransactionMonitor`.
6. `TransactionMonitor` reconciles pending, confirmed, and rejected states back into the UI.

### Receive

The receive view is derived from the selected wallet in `wallets.json`. It shows the canonical address, current balance, copy action, and a local note field. QR generation is not shipped yet, so the UI shows an explicit availability notice instead of a fake image.

## Transaction History Strategy

There is no single monolithic history source inside the repo, so the wallet uses a resilient adapter:

- explorer/indexer API for canonical transaction history and detail lookups
- direct transaction status/detail calls for confirmation updates
- local `wallet.db` pending entries for transactions submitted by this UI before the explorer catches up

The history tab supports combined and per-wallet views, filters, exports, and details. Pending entries can transition to confirmed/rejected without forcing a restart.

## Address Book

The address book is a local persistence feature, not part of the canonical wallet store.

- Validation: canonical Animica address validation through `WalletEngine`
- Persistence: versioned JSON store with CSV/JSON import/export
- Duplicate handling: address-based duplicate prevention on add/import
- Own-address tagging: computed dynamically from current wallet accounts

## Contract Interaction

The contract tab supports two modes:

- ABI/schema mode for method discovery, payload preview, read calls, and signed writes
- raw mode for direct payload calls when no ABI is available

ABI parsing/encoding/decoding is handled through the updated `sdk/python/omni_sdk/types/abi.py` helpers. Read calls surface RPC errors directly. Signed writes go through the same canonical wallet bridge used for normal sends.

## Settings and Node Control

The wallet separates:

- user/UI settings in `QSettings`
- network/RPC/explorer settings consumed by `AnimicaRpcClient`, `WalletEngine`, and polling jobs
- bundled-node lifecycle control in `NodeControlWidget` when the app is built with `WALLET_REMOTE_RPC_ONLY=OFF`

Disruptive actions such as endpoint changes trigger explicit reconfiguration rather than silent success states.

## Packaging Runtime Layout

The runtime lookup logic supports these embedded-node locations:

- Linux build tree: `bin/node/venv/bin/python`
- Linux installed package: `../lib/animica-wallet/node/venv/bin/python`
- Linux AppImage: `../lib/node/venv/bin/python`
- macOS bundle: `Contents/Resources/node/venv/bin/python`
- Windows package: `node/venv/Scripts/python.exe`

This lookup is shared by both the wallet bridge adapter and the node manager so packaged builds and development builds resolve the same embedded runtime correctly.
