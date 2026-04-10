# Packaging

## Goal

Package `wallet-qt` as a remote-only desktop wallet.

Every release artifact should reflect these truths:

- mainnet only
- hosted RPC only
- endpoint fixed to `https://rpc.animica.org/rpc`
- no embedded node payload

## What ships

Release artifacts should include:

- the Qt wallet executable or app bundle
- Qt runtime dependencies
- icons, desktop metadata, and app resources

## What must not ship

- `node/venv`
- node wrapper scripts
- embedded genesis/spec assets
- node logs or node data directories
- operator-facing node configuration

## Platform expectations

### Linux

Artifacts may include installed-tree, tarball, or AppImage-style layouts, but they should only stage the Qt app and its desktop assets.

### macOS

The `.app` bundle should contain the wallet app resources only. There is no `Contents/Resources/node`.

### Windows

The staged tree should contain the wallet executable and Qt/runtime assets only. There is no `node\\venv`.

## Verification checklist

- the binary starts without spawning a subprocess for node startup
- no packaged file tree contains `node/assets` or `node/venv`
- the runtime settings surface still shows `https://rpc.animica.org/rpc`
- remote-connectivity failure shows a wallet-facing error state instead of node diagnostics

## Scripts

Current packaging/release flows:

- `scripts/build-linux.sh`
- `scripts/build-mac.sh`
- `scripts/build-windows-cross.sh`
- `scripts/build-windows.ps1`
- `scripts/release-linux.sh`
- `scripts/release-mac.sh`
- `scripts/release-windows-cross.sh`

## Limitation

The wallet no longer bundles Python. Source/runtime environments that rely on the wallet bridge and QR helper still need an external Python environment available outside the packaged app.
