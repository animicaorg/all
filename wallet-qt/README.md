# Animica Wallet (Qt Desktop)

`wallet-qt/` is the desktop wallet for Animica. It now uses the canonical Animica Python wallet/transaction stack through a hardened JSON bridge instead of ad hoc subprocess calls and placeholder signing code.

## Implemented Features

- Wallet creation, import, rename, default selection, removal, public export, and guarded secret export
- Real balance refresh with per-wallet totals, connection state, and periodic polling
- Send flow with canonical address validation, balance/fee preflight, confirmation, submission, and pending tracking
- Receive flow with wallet selector, copy address, live QR generation, optional amount/message fields, and PNG export
- Transaction history with filters, details, and CSV/JSON export
- Address book with add/edit/delete, merge-or-replace import, JSON/CSV export, duplicate prevention, and own-address tagging
- Contract interaction for ABI-driven read/write calls plus raw-call fallback
- Advanced settings for RPC/network/explorer/polling/timeouts plus import/export/default reset
- Bundled-node packaging scripts for AppImage, DMG, and MSI build flows

## Architecture

See [docs/architecture.md](docs/architecture.md).

The important runtime path is:

`Qt widgets -> WalletEngine -> AnimicaWalletBackend -> python -m animica.qt_wallet_bridge -> canonical Animica Python modules`

That bridge reuses:

- `python/animica/wallet/...` for wallet storage and serialization
- `python/animica/cli/wallet.py` helpers for canonical wallet behavior
- repo RPC/explorer endpoints for balances, transaction status, and history
- `sdk/python/omni_sdk/...` for contract ABI encoding/decoding and contract calls

## Storage Layout

By default the wallet stores user data under the app data directory chosen by `DataDirManager`.

- Canonical wallet store: `wallets.json`
- Local address book: `address_book.json`
- Wallet activity cache/database: `wallet.db`
- User UI/network preferences: Qt `QSettings`

`wallets.json` is the canonical Animica wallet store. It is not currently encrypted by the upstream wallet format, so the UI does not pretend otherwise.

## Development

### Remote-RPC wallet build

```bash
cmake -S wallet-qt -B build/wallet-qt -DBUILD_TESTING=ON
cmake --build build/wallet-qt -j4 --target animica-wallet
./build/wallet-qt/bin/animica-wallet
```

### Bundled-node wallet build

```bash
cmake -S wallet-qt -B build/wallet-qt-bundled -DWALLET_REMOTE_RPC_ONLY=OFF -DBUILD_TESTING=OFF
cmake --build build/wallet-qt-bundled -j4 --target animica-wallet
./build/wallet-qt-bundled/bin/animica-wallet
```

### Python bridge tests

```bash
PYTHONPATH=python:sdk/python pytest -q python/animica/tests/test_qt_wallet_bridge.py
```

### Qt/C++ regression subset

```bash
cd build/wallet-qt
ctest --output-on-failure -R 'test_keystore_security|test_wallet_engine|test_walletdatabase|test_datadirmanager|test_redactor'
```

## Packaging

See [docs/receive_qr.md](docs/receive_qr.md), [docs/packaging.md](docs/packaging.md), and [docs/RELEASING.md](docs/RELEASING.md).

Quick commands:

```bash
./wallet-qt/scripts/release-linux.sh --appimage-only
./wallet-qt/scripts/release-mac.sh --adhoc-sign --dmg
./wallet-qt/scripts/release-mac.sh --dmg
pwsh ./wallet-qt/scripts/release-windows.ps1
```

## Manual Verification

Use [docs/operator-checklist.md](docs/operator-checklist.md) after each build.

## Native Validation Boundary

Linux build and packaging verification can be exercised in this environment. DMG creation/signing/notarization and MSI installation validation still require native macOS and Windows hosts even though the scripts and staged-runtime checks are now wired in-tree.
