# Animica Wallet Qt

Animica Wallet Qt is now a hosted-RPC desktop wallet.

The application:

- always targets Animica mainnet
- always connects to `https://rpc.animica.org`
- never starts or manages a local node
- never bundles node assets, genesis files, or a Python node runtime inside the app

This repo target is intentionally opinionated. If you need node lifecycle control, chain storage, or operator tooling, use the Animica node/CLI stack outside `wallet-qt`.

## Product model

At runtime the wallet is a Qt desktop UI with:

- wallet/account management
- send and receive flows
- balance and history retrieval over hosted RPC
- a simplified settings surface for wallet preferences
- remote connectivity feedback instead of local-node controls

There is no embedded-node mode and no supported localhost mode.

## Canonical network settings

- Network: `mainnet`
- RPC endpoint: `https://rpc.animica.org`
- Chain ID: `1`

## Build

Requirements for developer builds:

- CMake 3.24+
- Qt 6 Widgets, Network, Svg
- C++17 compiler
- Python available in the developer environment for the wallet bridge and QR helper used by source builds

Configure and build:

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON
cmake --build /tmp/wallet-qt-build -j
```

The build no longer creates or stages a bundled node runtime.

## Run

```bash
/tmp/wallet-qt-build/bin/animica-wallet
```

Optional data-dir override:

```bash
ANIMICA_WALLET_DATA_DIR=/path/to/wallet-data /tmp/wallet-qt-build/bin/animica-wallet
```

On launch the wallet uses `https://rpc.animica.org` automatically.

## Packaging

Packaging scripts stage a Qt desktop app only. They do not:

- build a bundled node
- create a bundled venv
- copy genesis/spec assets
- install node wrapper scripts

See:

- `docs/build_and_bundle.md`
- `docs/packaging.md`
- `docs/RELEASING.md`

## Testing

Focused regression coverage lives under `wallet-qt/tests` and validates:

- canonical RPC defaults
- remote-only packaging expectations
- wallet/account surfaces initializing without embedded-node components
- remote receive/send widget behavior

## Current limitation

The wallet no longer bundles Python or node assets. Source builds still rely on a developer-available Python environment for the wallet bridge and QR helper. That runtime is external to the packaged app and is not an embedded node dependency.
