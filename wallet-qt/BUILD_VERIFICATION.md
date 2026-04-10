# Build Verification

Use this checklist to verify the refactored remote-only wallet build.

## Configure and build

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON
cmake --build /tmp/wallet-qt-build -j
```

## Core verification

- `animica-wallet` builds successfully
- test binaries build successfully
- no build output stages `node/venv` or bundled genesis/spec assets

## Focused test run

```bash
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_widget
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_receive_qr
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
```

## Smoke launch

```bash
timeout 6 /tmp/wallet-qt-build/bin/animica-wallet -platform offscreen
```

Expected result:

- app starts
- no local node subprocess is launched
- remote-RPC errors, if any, are surfaced as hosted-network problems
