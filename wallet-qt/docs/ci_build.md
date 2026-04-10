# CI Build Notes

## CI intent

CI for `wallet-qt` should validate the remote-only desktop wallet build.

CI should not:

- create a bundled node runtime
- verify bundled genesis/spec assets
- run localhost node smoke tests for the Qt app

## Recommended CI steps

1. Configure with CMake.
2. Build the Qt wallet targets.
3. Run focused remote-only tests.

Example:

```bash
cmake -S wallet-qt -B /tmp/wallet-qt-build -DBUILD_TESTING=ON
cmake --build /tmp/wallet-qt-build -j

QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_rpc_settings
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_packaging_config
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_wallet_widget
QT_QPA_PLATFORM=offscreen /tmp/wallet-qt-build/tests/test_receive_qr
```

## CI prerequisites

- Qt 6 development packages
- CMake and a C++17 compiler
- Python available for the wallet bridge used by source builds
