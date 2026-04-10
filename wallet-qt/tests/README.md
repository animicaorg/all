# Wallet Qt Tests

The Qt wallet test suite now targets the hosted-RPC product model.

## What the tests cover

- canonical RPC defaults resolve to `https://rpc.animica.org/rpc`
- the wallet assumes Animica mainnet
- remote-only widget surfaces initialize without embedded-node components
- receive/send flows stay usable without local node management
- packaging scripts stage a Qt app without node payloads

## What the tests do not assume

- no bundled node
- no local RPC daemon
- no localhost defaults
- no bundled genesis/spec assets

## Typical local runs

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON
cmake --build /tmp/wallet-qt-build -j

QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_widget
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_receive_qr
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_engine
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
```

## Notes

- Several widget tests run in `offscreen` mode.
- Network-host failures are acceptable in unit tests as long as the widget/backend behavior remains sane.
- If you need node/operator validation, use Animica node tooling outside the Qt wallet suite.
