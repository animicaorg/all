# Packaging

## Goal

Package `wallet-qt` as a remote-only desktop wallet.

Every release artifact should reflect these truths:

- mainnet only
- hosted RPC only
- endpoint fixed to `https://rpc.animica.org/rpc`
- no embedded node daemon payload

## What ships

Release artifacts should include:

- the Qt wallet executable or app bundle
- Qt runtime dependencies
- bundled wallet Python runtime (`node/venv`)
- bundled wallet runtime assets (`node/assets/spec/params.yaml`, `node/assets/genesis/devnet.json`)
- icons, desktop metadata, and app resources

## What must not ship

- node startup wrapper scripts
- node logs or node data directories
- operator-facing node configuration

## Platform expectations

### Linux

Artifacts may include installed-tree, tarball, or AppImage-style layouts. They should stage the Qt app, bundled wallet runtime, and desktop assets.

### macOS

The `.app` bundle should include `Contents/Resources/node` with a bundled Python runtime and wallet assets.

### Windows

The staged tree should include `node\\venv\\Scripts\\python.exe` and bundled wallet assets under `node\\assets`.

## Verification checklist

- the binary starts without spawning a subprocess for node startup
- packaged file tree contains `node/venv` and `node/assets` for wallet backend/QR operations
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

The wallet bundles Python runtime components for wallet backend and QR helper operations, but still does not bundle or run a local full node daemon.
