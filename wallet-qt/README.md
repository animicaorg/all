# Animica Wallet Qt

Animica Wallet Qt is now a hosted-RPC desktop wallet.

The application:

- always targets Animica mainnet
- always connects to `https://rpc.animica.org/rpc`
- never starts or manages a local node
- bundles a dedicated Python runtime for wallet bridge and QR operations

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
- RPC endpoint: `https://rpc.animica.org/rpc`
- Chain ID: `1`

## Build

Requirements for developer builds:

- CMake 3.24+
- Qt 6 Widgets, Network, Svg
- C++17 compiler
- Python 3.10+ in the developer environment when building with bundled runtime support

Configure and build:

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON -DWALLET_BUNDLE_PYTHON_RUNTIME=ON
cmake --build /tmp/wallet-qt-build -j
```

To skip bundled runtime creation in local development, pass `-DWALLET_BUNDLE_PYTHON_RUNTIME=OFF`.

## Run

```bash
/tmp/wallet-qt-build/bin/animica-wallet
```

Optional data-dir override:

```bash
ANIMICA_WALLET_DATA_DIR=/path/to/wallet-data /tmp/wallet-qt-build/bin/animica-wallet
```

On launch the wallet uses `https://rpc.animica.org/rpc` automatically.

## Packaging

Packaging scripts stage a Qt desktop app plus bundled wallet runtime assets:

- bundled Python venv for wallet backend and QR helper
- bundled wallet runtime assets (`spec/params.yaml`, `genesis/devnet.json`)

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

The wallet still does not run or manage a local node. The bundled runtime is used for wallet backend and QR helper operations only.
