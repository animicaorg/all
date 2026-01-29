# Wallet Qt Tests

Unit and integration tests for the Animica Qt Wallet Engine.

## Running Tests

### Build and Run All Tests

```bash
cd wallet-qt/build
cmake .. -DCMAKE_BUILD_TYPE=Debug
cmake --build . --target test_keystore_security test_wallet_engine
ctest --output-on-failure
```

### Run Individual Tests

```bash
# Keystore security tests
./test_keystore_security

# Wallet engine integration tests
./test_wallet_engine
```

## Test Coverage

### test_keystore_security.cpp

Security-focused tests for EncryptedKeystore:

- ✅ **Wrong password rejection** - Verifies unlock fails with incorrect password
- ✅ **File tampering detection** - Verifies MAC authentication catches modifications
- ✅ **Roundtrip encryption** - Tests encrypt/decrypt for various payload sizes
- ⚠️ **File permissions** (Unix only) - Verifies 0600 permissions on keystore file

### test_wallet_engine.cpp

Integration tests for WalletEngine:

- ✅ **Create and unlock wallet** - Full lifecycle test
- ✅ **Wrong password fails** - Verifies access control
- ✅ **Auto-lock timer** - Verifies auto-lock after timeout
- ⚠️ **Account creation requires unlock** - Tests state enforcement (requires Python)

## Test Requirements

- Qt6 Test framework
- OpenSSL (for cryptography)
- Python 3.11+ with `pq` module (for account creation tests)

## Known Limitations

1. **Python dependency**: Account creation tests require Python subprocess calls
2. **No RPC mocking**: Balance tracker tests require running node (not implemented yet)
3. **No UI tests**: Widget tests not included (manual testing required)

## Adding New Tests

```cpp
#include <QTest>
#include "../src/wallet/YourClass.h"

class TestYourClass : public QObject
{
    Q_OBJECT

private slots:
    void testSomething()
    {
        // Your test code
        QVERIFY(condition);
        QCOMPARE(actual, expected);
    }
};

QTEST_MAIN(TestYourClass)
#include "test_your_class.moc"
```

Then add to `CMakeLists.txt`:

```cmake
add_wallet_test(test_your_class test_your_class.cpp)
```

## CI Integration

Add to `.github/workflows/` (planned):

```yaml
- name: Build and test wallet
  run: |
    cd wallet-qt
    mkdir build && cd build
    cmake .. -DCMAKE_BUILD_TYPE=Debug
    cmake --build .
    ctest --output-on-failure
```

## Manual Testing

For features requiring Python or RPC:

1. Build wallet GUI: `./scripts/build-linux.sh`
2. Run wallet: `./build/linux/bin/animica-wallet`
3. Test scenarios:
   - Create wallet with password
   - Create account (verify Python subprocess works)
   - Lock/unlock wallet
   - Auto-lock after timeout
   - Balance updates (requires running node)

## Security Testing Notes

### Memory Security
- Tests verify secure clearing of sensitive buffers
- Run under Valgrind to detect memory leaks: `valgrind --leak-check=full ./test_keystore_security`

### Crypto Parameters
- PBKDF2: 200k iterations (~100ms on modern CPU)
- AES-256-GCM: 256-bit keys, 12-byte nonces
- OpenSSL RAND_bytes for entropy

### File Security
- Unix: 0600 permissions enforced
- Windows: Relies on NTFS ACLs (not tested)

## Future Tests (TODO)

- [ ] Migration tests (V1 → V2 schema)
- [ ] Address book CRUD
- [ ] Balance polling with mock RPC
- [ ] Transaction signing integration
- [ ] Concurrent lock/unlock stress test
- [ ] Large wallet performance (1000+ accounts)
