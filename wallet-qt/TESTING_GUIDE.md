# Testing Guide

## Scope

Test the Qt wallet as a hosted-RPC desktop wallet.

Do not test it as an embedded-node product. That architecture has been removed.

## Manual checks

1. Launch the app.
2. Confirm settings show Animica mainnet and `https://rpc.animica.org/rpc`.
3. Confirm there is no Node tab or local-node control surface.
4. Create or open a wallet store.
5. Verify accounts, send, receive, and history screens initialize.
6. Verify remote-connectivity failures show a clear banner/error message.

## Automated checks

```bash
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_widget
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_receive_qr
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_engine
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
```

## Packaging checks

- verify the staged artifact contains only the Qt app/runtime payload
- verify `node/`, `node/venv`, and bundled genesis/spec assets are absent

## Troubleshooting

- connection failures should be investigated as access issues to `https://rpc.animica.org/rpc`
- wallet data issues should be investigated in the wallet data directory
- node/operator issues belong to separate Animica tooling, not this app
