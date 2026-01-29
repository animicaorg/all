# Wallet Engine Architecture

## Overview

The Animica Qt Wallet Engine provides secure key management, account storage, address book, and balance tracking integrated with the embedded Animica node. This document describes the architecture, data formats, and RPC integration strategy.

## Integration Strategy

**Decision: Wallet app owns encrypted keystore, node handles chain queries + tx broadcast**

### Why This Approach?
1. **Security isolation**: Secrets stay in wallet memory, never sent over RPC
2. **Node compatibility**: Existing node CLI can continue using plaintext wallets.json
3. **Qt-native UX**: Lock/unlock state managed in GUI without node dependency
4. **Migration path**: Can import existing wallets.json entries

### Trade-offs
- Wallet must implement Dilithium3/SPHINCS+ signing (links to `pq` libs)
- Transaction construction must match node format exactly
- Requires careful testing against node validation

## Architecture Components

### 1. Core Classes

```
WalletEngine (Coordinator)
├── EncryptedKeystore (Storage)
│   ├── KeyDerivation (KDF: Argon2id/scrypt/PBKDF2)
│   └── AEADEncryption (XChaCha20-Poly1305 or AES-256-GCM)
├── AccountManager (Account CRUD)
│   └── WalletAccount (Account data)
├── AddressBook (Contacts)
└── BalanceTracker (RPC polling/WS)
```

### 2. WalletEngine (src/wallet/WalletEngine.h)

**Responsibilities:**
- Coordinate all wallet operations
- Manage lock/unlock state
- Auto-lock timer
- Transaction signing orchestration
- Balance updates from node RPC

**State Machine:**
```
Locked → Unlocked → Locked
         ↓ (timeout)
         Locked
```

**Key Methods:**
```cpp
bool createWallet(QString password, QString dataDir);
bool unlockWallet(QString password);
void lockWallet();
bool isLocked();
void setAutoLockTimeout(int minutes);

WalletAccount createAccount(QString label);
bool importAccount(WalletAccount account);
bool removeAccount(QString accountId);
QList<WalletAccount> listAccounts();

bool addContact(QString label, QString address);
QList<Contact> listContacts();

QMap<QString, Balance> getBalances();
SignedTransaction signTransaction(Transaction tx, QString fromAccount);
```

### 3. EncryptedKeystore (src/wallet/EncryptedKeystore.h)

**File Format (v1):**
```json
{
  "schema_version": 1,
  "kdf": {
    "algorithm": "argon2id",
    "params": {
      "memory_cost": 65536,
      "time_cost": 3,
      "parallelism": 4,
      "salt": "<base64>"
    }
  },
  "encryption": {
    "algorithm": "xchacha20-poly1305",
    "nonce": "<base64>"
  },
  "public_accounts": [
    {
      "account_id": "<uuid>",
      "label": "Account 1",
      "address": "anim1...",
      "alg_id": 4097,
      "created_at": "2025-01-01T00:00:00Z",
      "last_used_at": "2025-01-29T01:00:00Z"
    }
  ],
  "encrypted_payload": "<base64>",
  "created_at": "2025-01-29T01:00:00Z",
  "updated_at": "2025-01-29T01:00:00Z"
}
```

**Encrypted Payload (JSON before encryption):**
```json
{
  "accounts": [
    {
      "account_id": "<uuid>",
      "alg_id": 4097,
      "alg_name": "dilithium3",
      "public_key": "<hex>",
      "secret_key": "<hex>",
      "seed": "<hex|null>"
    }
  ],
  "master_seed": "<hex|null>",
  "address_book_notes": {
    "anim1...": "Private note about Alice"
  }
}
```

**Security Features:**
- **KDF Priority**: Argon2id (if available) > scrypt > PBKDF2-SHA3-256
- **AEAD Priority**: XChaCha20-Poly1305 (libsodium) > AES-256-GCM
- **Salt**: 16 bytes random per wallet
- **Nonce**: 24 bytes (XChaCha20) or 12 bytes (AES-GCM)
- **Atomic writes**: Temp file + atomic rename
- **File permissions**: 0600 (owner read/write only) on Unix
- **Memory security**: Clear sensitive buffers on lock

**Key Methods:**
```cpp
static bool create(QString path, QByteArray payload, QString password);
bool unlock(QString password, QByteArray& outPayload);
void lock();
bool changePassword(QString oldPass, QString newPass);
KeystoreInfo readInfo();  // metadata without decrypt
```

### 4. AccountManager (src/wallet/AccountManager.h)

**Account Data Structure:**
```cpp
struct WalletAccount {
    QString accountId;          // UUID v4
    QString label;              // User-friendly name
    QString address;            // bech32m (anim1...)
    int algId;                  // 0x1001 = Dilithium3
    QString algName;            // "dilithium3"
    QByteArray publicKey;       // Raw bytes
    QByteArray secretKey;       // Raw bytes (only when unlocked)
    QDateTime createdAt;
    QDateTime lastUsedAt;
    bool isDefault;
};
```

**Operations:**
```cpp
WalletAccount createAccount(QString label, int algId = 0x1001);
bool importFromWalletsJson(QString path);
bool exportAccount(QString accountId, QString path, bool includeSecret);
WalletAccount setDefault(QString accountId);
bool renameAccount(QString accountId, QString newLabel);
```

### 5. AddressBook (src/wallet/AddressBook.h)

**Contact Structure:**
```cpp
struct Contact {
    QString label;
    QString address;            // bech32m validated
    QString note;               // Optional
    QDateTime createdAt;
};
```

**Operations:**
```cpp
bool addContact(QString label, QString address, QString note = "");
bool updateContact(QString address, QString label, QString note);
bool removeContact(QString address);
QList<Contact> listContacts(QString filter = "");
bool validateAddress(QString address);  // bech32m + hrp check
```

### 6. BalanceTracker (src/wallet/BalanceTracker.h)

**Balance Data:**
```cpp
struct Balance {
    QString address;
    quint64 confirmed;          // In smallest unit (1 ANM = 10^18)
    quint64 pending;
    QString asset;              // "ANM" or token address
    bool syncing;
    int lastSyncHeight;
};
```

**Update Strategy:**
- **Primary**: RPC polling (`state.getBalance`) every 5 seconds
- **Future**: WebSocket subscription to `newHeads` event
- **Handles**:
  - Reorgs (balance adjustments)
  - Mempool pending (via `state.getPendingNonce` + tx tracking)
  - Sync state (via `eth_syncing` or `chain.getHead` vs `chain.getCanonicalHeight`)

**Methods:**
```cpp
void startTracking(QStringList addresses);
void stopTracking();
QMap<QString, Balance> getBalances();
void refresh();  // Force immediate update

signals:
void balanceUpdated(QString address, Balance balance);
void syncStatusChanged(bool syncing);
```

## RPC Methods Used

### Balance & State Queries
- `state.getBalance(address, "latest")` → hex balance
- `state.getNonce(address, "latest")` → nonce for next tx
- `state.getPendingNonce(address)` → pending mempool nonce

### Transaction Operations
- `tx.send(signedTxHex)` → tx_hash
- `tx.getStatus(tx_hash)` → {status, confirmations, included_height}

### Chain Queries
- `chain.getHead()` → {height, hash}
- `eth_syncing` → false or {currentBlock, highestBlock}

### Future Enhancements
- `newHeads` (WebSocket) for real-time balance updates
- `pendingTransactions` (WebSocket) for mempool tracking

## Wallet File Locations

**Default Data Directory:**
- macOS: `~/Library/Application Support/AnimicaWallet/wallet/`
- Windows: `%APPDATA%\AnimicaWallet\wallet\`
- Linux: `~/.local/share/AnimicaWallet/wallet/`

**Files:**
```
wallet/
├── keystore.json           # Encrypted wallet (primary)
├── keystore.json.backup.*  # Timestamped backups (on migration)
├── address_book.json       # Address book (unencrypted)
└── tx_cache.json           # Pending tx tracking (optional)
```

## Backward Compatibility with Node CLI

**Approach 1: Import-only** (Implemented)
- Wallet GUI can import accounts from `~/.animica/wallets.json`
- Imported accounts are encrypted in GUI wallet
- Node CLI continues using plaintext wallets.json independently

**Approach 2: Sync on unlock** (Not implemented, optional)
- On wallet unlock, export public-only view to node's wallets.json
- Allows node CLI to see addresses (but not sign)
- Requires careful coordination to avoid conflicts

**Decision: Approach 1** for initial release. Users choose GUI *or* CLI per account.

## Migration Framework

**Migration Directory:** `src/wallet/migrations/`

**Schema Versioning:**
- Each wallet file has `schema_version` field
- Migrations run automatically on unlock if version mismatch
- Always create timestamped backup before migration

**Migration Steps:**
1. Detect `schema_version < CURRENT_VERSION`
2. Create backup: `keystore.json.backup.YYYYMMDD_HHMMSS`
3. Apply migration: `MigrationV1ToV2::migrate(payload)`
4. Write updated wallet with new version
5. If error, restore from backup and surface error to user

**Example Migrations:**
- V1 → V2: Add `last_used_at` field to accounts
- V2 → V3: Migrate from PBKDF2 to Argon2id KDF

## Security Considerations

### Threat Model
**In scope:**
- Local attacker with read access to wallet file (mitigated by encryption)
- Memory dump while wallet locked (mitigated by clearing buffers)
- Password brute force (mitigated by strong KDF)

**Out of scope:**
- Keylogger / malware (OS-level responsibility)
- Physical access to unlocked wallet (assumed trusted environment)
- Side-channel attacks (not targeting high-security use cases)

### Security Measures
1. **Strong KDF**: Argon2id (65MB, 3 iterations, 4 threads) ≈ 500ms on modern CPU
2. **AEAD**: Authenticated encryption prevents tampering
3. **No plaintext secrets on disk**: All keys encrypted at rest
4. **No secret logging**: Code reviews + tests enforce this
5. **Secure erase**: `memset_s` or `explicit_bzero` for buffers
6. **File permissions**: 0600 on Unix (owner-only)
7. **Atomic writes**: Temp file + rename prevents corruption

### Password Requirements
- Minimum 8 characters (enforced in UI)
- Recommend 12+ characters with mixed case/numbers/symbols
- No maximum length (limited by UI field size)
- Show password strength indicator

## Testing Strategy

### Unit Tests
- `test_kdf_roundtrip.cpp`: PBKDF2/Argon2id derive + verify
- `test_aead_encryption.cpp`: Encrypt/decrypt/tamper detection
- `test_account_creation.cpp`: Dilithium3 keygen + address derivation
- `test_bech32m_encoding.cpp`: Address encode/decode
- `test_migration_v1_to_v2.cpp`: Schema migration

### Integration Tests
- `test_wallet_create_import.cpp`: Create wallet → import account → verify
- `test_balance_polling.cpp`: Start node → track address → verify balance
- `test_sign_and_send.cpp`: Create tx → sign → send via RPC → confirm in block
- `test_lock_unlock_cycle.cpp`: Unlock → sign → lock → verify secrets cleared

### Security Tests
- `test_no_secret_logging.cpp`: Enable debug logging → verify no secrets in logs
- `test_file_permissions.cpp`: Create wallet → verify mode 0600 (Unix)
- `test_password_wrong.cpp`: Try wrong password → verify rejection
- `test_corrupted_file.cpp`: Tamper with ciphertext → verify MAC failure

## Dependencies

### Cryptography
- **libsodium** (preferred): Argon2id + XChaCha20-Poly1305
  - Install: `apt install libsodium-dev` (Ubuntu), `brew install libsodium` (macOS)
- **OpenSSL** (fallback): PBKDF2-SHA3 + AES-256-GCM
  - Usually pre-installed or via Qt's OpenSSL support

### PQ Cryptography
- **liboqs** (optional): Fast Dilithium3 implementation
  - Build from source: https://github.com/open-quantum-safe/liboqs
  - Fallback to pure-Python `pq` module via subprocess if unavailable

### Qt Modules
- **Qt6::Core**: QByteArray, QString, QJsonDocument
- **Qt6::Network**: QNetworkAccessManager (for RPC client)

## Implementation Notes

### Why C++ instead of Python?
- Native Qt integration (signals/slots, no IPC)
- Better memory control (secure erase of secrets)
- Performance for cryptography operations
- Easier packaging (no Python runtime in installer)

### Python PQ Integration
- **Option 1**: Link liboqs C library directly (preferred)
- **Option 2**: Shell out to `python -m pq.cli.sign` (fallback)
- **Option 3**: Use ctypes/cffi to call Python `pq` module (experimental)

### Atomic Wallet Updates
```cpp
void EncryptedKeystore::save() {
    QString tmpPath = path + ".tmp";
    QFile tmp(tmpPath);
    tmp.open(QIODevice::WriteOnly);
    tmp.write(serializeToJson());
    tmp.flush();
    fsync(tmp.handle());  // Ensure written to disk
    tmp.close();
    
    #ifdef Q_OS_UNIX
    QFile::setPermissions(tmpPath, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    #endif
    
    // Atomic rename
    QFile::remove(path);  // Remove old (Windows requirement)
    QFile::rename(tmpPath, path);
}
```

## Future Enhancements

### Planned (Post-v1)
- **Hardware wallet support**: Ledger/Trezor integration
- **Multi-sig accounts**: Threshold signatures
- **HD wallets**: BIP-32/BIP-44 style key derivation
- **QR code import/export**: For mobile wallets
- **Encrypted backup to cloud**: Optional auto-backup with separate passphrase

### Under Consideration
- **Biometric unlock**: TouchID/WindowsHello (with secure enclave)
- **WebAuthn support**: FIDO2 keys as 2FA
- **Social recovery**: Shamir secret sharing for password recovery

## References

- **SDK Keystore**: `/sdk/python/omni_sdk/wallet/keystore.py`
- **CLI Wallet**: `/python/animica/cli/wallet.py`
- **PQ Crypto**: `/pq/py/` (keygen, sign, address)
- **RPC Methods**: `/rpc/methods/` (state, tx, chain)
- **Address Encoding**: `/pq/py/address.py` (bech32m)
- **Dilithium3 Spec**: FIPS 204 (ML-DSA)
