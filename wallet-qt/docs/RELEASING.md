# Releasing

## Release truth

The Qt wallet release is a hosted-RPC wallet release.

Before shipping, verify:

- the app targets `https://rpc.animica.org/rpc`
- the app assumes mainnet
- no embedded-node files are present in the artifact
- docs and release notes do not mention bundled node behavior

## Linux

```bash
./wallet-qt/scripts/build-linux.sh
./wallet-qt/scripts/release-linux.sh
```

## macOS

```bash
./wallet-qt/scripts/build-mac.sh
./wallet-qt/scripts/release-mac.sh
```

## Windows cross-build

```bash
./wallet-qt/scripts/build-windows-cross.sh
./wallet-qt/scripts/release-windows-cross.sh
```

## Minimum release validation

Run:

```bash
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_widget
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_receive_qr
```

Smoke launch:

```bash
timeout 6 /tmp/wallet-qt-build/bin/animica-wallet -platform offscreen
```

## Release notes guidance

State clearly that:

- the wallet is now remote-RPC only
- it connects to `https://rpc.animica.org/rpc`
- local node control is no longer part of the Qt wallet
- operator workflows belong to other Animica tooling
