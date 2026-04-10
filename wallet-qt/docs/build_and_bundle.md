# Build And Bundle

## Build model

`wallet-qt` now builds as a Qt desktop application only.

The build no longer:

- creates a bundled node virtualenv
- copies Python node modules into the app
- bundles genesis/spec assets
- generates node wrapper scripts
- stages embedded-node install trees

## Developer prerequisites

- CMake 3.24+
- Qt 6 Widgets, Network, Svg
- C++17 compiler
- Python available to the developer environment for the wallet bridge and QR helper used by source builds

## Configure

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON
```

## Build

```bash
cmake --build /tmp/wallet-qt-build -j
```

Main binary:

```text
/tmp/wallet-qt-build/bin/animica-wallet
```

## Run

```bash
/tmp/wallet-qt-build/bin/animica-wallet
```

The wallet will connect to `https://rpc.animica.org` automatically.

## Build outputs

The staged output contains:

- the Qt wallet executable
- Qt/runtime resources needed by the desktop app
- icons and desktop metadata

It does not contain:

- `node/`
- `venv/`
- `assets/spec/params.yaml`
- `assets/genesis/*.json`

## Platform notes

### Linux

Build script:

```bash
./wallet-qt/scripts/build-linux.sh
```

### macOS

Build script:

```bash
./wallet-qt/scripts/build-mac.sh
```

### Windows cross-build

Build script:

```bash
./wallet-qt/scripts/build-windows-cross.sh
```

## Verification

Recommended focused checks:

```bash
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
```

## Packaging intent

Packaging is optimized for:

- smaller artifacts
- faster configure/build time
- fewer release-time mismatches
- remote-RPC desktop UX only
