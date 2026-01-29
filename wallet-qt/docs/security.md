# Wallet Security Documentation

## Threat Model

### Assets Protected
1. **Private keys**: PQ signature keys (Dilithium3: 4000 bytes, SPHINCS+: 64 bytes)
2. **Master seeds**: Optional HD wallet seeds (32 bytes)
3. **Transaction history**: Metadata about user transactions
4. **Address book**: Contact labels and notes (less sensitive)

### Adversaries

#### In Scope
**Local File Access Attacker**
- **Capability**: Can read wallet file from disk
- **Mitigation**: Strong encryption at rest (Argon2id + XChaCha20-Poly1305)
- **Residual risk**: Brute-force password attack (mitigated by KDF cost)

**Memory Dump Attacker (Locked Wallet)**
- **Capability**: Can dump process memory while wallet is locked
- **Mitigation**: Clear sensitive buffers on lock; keys not in memory when locked
- **Residual risk**: Memory forensics may recover recent data from unallocated pages

**Offline Attacker (Stolen Laptop)**
- **Capability**: Physical access to powered-off machine
- **Mitigation**: Encrypted wallet file; no plaintext keys
- **Residual risk**: Weak user password

#### Out of Scope
**Active Malware/Keylogger**
- **Why**: Cannot defend against OS-level compromise in application
- **User responsibility**: Keep OS updated, use antivirus

**Side-Channel Attacks (Timing, Power, EM)**
- **Why**: Not targeting hardware wallet security level
- **Note**: Use constant-time crypto primitives where available

**Physical Access (Unlocked Wallet)**
- **Why**: Assumed trusted environment while user is present
- **Note**: Auto-lock timer reduces exposure window

### Attack Scenarios

#### Scenario 1: Stolen Backup File
**Attack**: Attacker obtains wallet file from cloud backup or USB drive

**Defense Layers**:
1. File is encrypted with user password
2. Argon2id KDF (65MB memory, 3 iterations, 4 threads) ≈ 500ms per attempt
3. At 500ms/attempt, 10^9 attempts = 15,854 years
4. Password complexity requirements enforced

**Expected Outcome**: Infeasible to brute-force strong password (12+ chars, mixed)

#### Scenario 2: Malicious Software Reads Wallet File
**Attack**: Malware on system reads `keystore.json` and exfiltrates it

**Defense Layers**:
1. File permissions (0600 on Unix) limit access to user's processes
2. Even if exfiltrated, file is encrypted (see Scenario 1)

**Expected Outcome**: Attacker gets encrypted blob; password required to unlock

#### Scenario 3: Memory Scraping While Unlocked
**Attack**: Malware dumps process memory to find private keys in RAM

**Defense Layers**:
1. Keys only in memory while wallet is unlocked
2. Auto-lock timer (default 15 minutes) limits exposure
3. Sensitive buffers cleared on lock (explicit_bzero/SecureZeroMemory)

**Expected Outcome**: 
- **Best case**: Attacker gets nothing (wallet locked)
- **Worst case**: Attacker gets keys if wallet is unlocked during attack

**Mitigation**: User should lock wallet when not actively transacting

#### Scenario 4: Log File Contains Secrets
**Attack**: Attacker reads application logs to find leaked secrets

**Defense Layers**:
1. Code review policy: No logging of sensitive fields
2. Automated tests verify logs don't contain key material
3. Sanitized error messages (e.g., "signature failed" not "signature with key 0x...")

**Expected Outcome**: No secrets in logs

#### Scenario 5: Corrupted/Tampered Wallet File
**Attack**: Attacker modifies encrypted payload to inject malicious data

**Defense Layers**:
1. AEAD (XChaCha20-Poly1305 or AES-GCM) provides authentication
2. Tampering detected during unlock (MAC verification)
3. Wallet refuses to open; user warned

**Expected Outcome**: Tampering detected; wallet remains secure

## Cryptographic Choices

### Key Derivation Function (KDF)

**Priority Order**:
1. **Argon2id** (preferred)
2. **scrypt** (acceptable)
3. **PBKDF2-HMAC-SHA3-256** (minimum acceptable)

#### Argon2id Parameters
```
Memory: 64 MB (65536 KB)
Time: 3 iterations
Parallelism: 4 threads
Salt: 16 bytes (random per wallet)
Output: 32 bytes (256-bit key)
```

**Rationale**: 
- Argon2id won PHC (Password Hashing Competition)
- Resistant to GPU/ASIC attacks (memory-hard)
- Hybrid mode combines data-dependent (Argon2i) and data-independent (Argon2d)
- ~500ms on modern CPU (acceptable UX, strong security)

**Benchmark** (Intel i7-10700K, single-threaded):
- 1 attempt: ~500ms
- 10^6 attempts: 5.8 days
- 10^9 attempts: 15,854 years

#### scrypt Parameters (Fallback)
```
N: 2^18 (262144)
r: 8
p: 1
Salt: 16 bytes
Output: 32 bytes
```

**Rationale**: 
- Widely supported (OpenSSL, libsodium)
- Memory-hard (similar to Argon2)
- ~300ms per attempt (slightly faster than Argon2id)

#### PBKDF2-HMAC-SHA3-256 (Minimum)
```
Iterations: 200,000
Salt: 16 bytes
Output: 32 bytes
```

**Rationale**:
- Available everywhere (Qt OpenSSL binding)
- Standardized (NIST SP 800-132)
- Slower than SHA-256 variant (SHA-3 has smaller hardware optimization potential)

**Weakness**: Not memory-hard; GPU/ASIC acceleration possible

**Mitigation**: Use only if Argon2id/scrypt unavailable; require 12+ char password

### Authenticated Encryption (AEAD)

**Priority Order**:
1. **XChaCha20-Poly1305** (preferred)
2. **AES-256-GCM** (acceptable)

#### XChaCha20-Poly1305
```
Key: 256 bits (from KDF)
Nonce: 192 bits (24 bytes, random)
Tag: 128 bits (16 bytes, appended to ciphertext)
```

**Rationale**:
- Extended nonce (XChaCha) allows random nonce generation (no counter state)
- Fast in software (no AES-NI dependency)
- Constant-time by design (no cache-timing attacks)
- Widely trusted (libsodium default)

**Security**: 256-bit key + 192-bit nonce = strong margin

#### AES-256-GCM (Fallback)
```
Key: 256 bits (from KDF)
Nonce: 96 bits (12 bytes, random)
Tag: 128 bits (16 bytes, appended to ciphertext)
```

**Rationale**:
- Hardware acceleration (AES-NI) on modern CPUs
- NIST-approved (FIPS 140-2)
- Available in OpenSSL (Qt dependency)

**Caution**: 
- 96-bit nonce requires counter mode (must never reuse nonce with same key)
- Our design uses random nonce + single encryption per wallet file (safe)

### Post-Quantum Signature Algorithms

**Default**: **Dilithium3** (ML-DSA-65)

**Key Sizes**:
- Public key: 1952 bytes
- Secret key: 4000 bytes (normalized)
- Signature: 3293 bytes

**Security Level**: NIST Level 3 (equivalent to AES-192, SHA-384)

**Rationale**:
- NIST FIPS 204 standard (finalized 2024)
- Fast signing/verification
- Compact signatures compared to other PQ schemes
- Liboqs native implementation available

**Alternative**: **SPHINCS+-SHAKE-128s** (for archival keys)

**Key Sizes**:
- Public key: 64 bytes
- Secret key: 64 bytes
- Signature: 7856 bytes

**Rationale**:
- Hash-based (stateless, conservative security)
- Minimal key storage
- Slower, but suitable for infrequent operations

### Random Number Generation

**Source**: OS-provided CSPRNG
- Linux/macOS: `/dev/urandom`
- Windows: `CryptGenRandom` (via Qt `QRandomGenerator::system()`)

**Never**: Custom RNG, Mersenne Twister, `rand()`

## Implementation Security Practices

### Memory Management

#### Secure Erase
```cpp
// Qt built-in (Qt 6.5+)
void clearSensitiveData(QByteArray& data) {
    data.fill(0);
    data.squeeze();  // Force deallocation
}

// Or use platform-specific
#ifdef Q_OS_WIN
#include <windows.h>
void secureZero(void* ptr, size_t size) {
    SecureZeroMemory(ptr, size);
}
#else
#include <string.h>
void secureZero(void* ptr, size_t size) {
    explicit_bzero(ptr, size);  // Or memset_s on macOS
}
#endif
```

#### Lock State Enforcement
```cpp
class WalletEngine {
private:
    bool m_isLocked = true;
    QList<WalletAccount> m_unlockedAccounts;  // Empty when locked
    
public:
    void lock() {
        // Clear all sensitive data
        for (auto& account : m_unlockedAccounts) {
            secureZero(account.secretKey.data(), account.secretKey.size());
        }
        m_unlockedAccounts.clear();
        m_isLocked = true;
    }
    
    SignedTx signTransaction(const Tx& tx) {
        if (m_isLocked) {
            throw WalletLockedException("Wallet must be unlocked to sign");
        }
        // ... signing logic
    }
};
```

### Logging & Error Messages

**Safe**:
```cpp
qDebug() << "Creating new account with label:" << label;
qDebug() << "Signing transaction with address:" << address;
qWarning() << "Failed to sign transaction: invalid nonce";
```

**Unsafe** (Never do this):
```cpp
qDebug() << "Secret key:" << secretKey.toHex();  // ❌
qDebug() << "Signing with key material:" << key;  // ❌
qWarning() << "Signature verification failed for sig:" << signature;  // ⚠️ (depends on context)
```

**Policy**:
- Log addresses, labels, nonces, tx hashes (public metadata)
- Never log secret keys, seeds, raw signatures (unless debug-only test code)
- Sanitize error paths to avoid leaking partial key material

### File Permissions

**Unix/Linux/macOS**:
```cpp
#include <sys/stat.h>

void setRestrictivePermissions(const QString& path) {
    #ifdef Q_OS_UNIX
    QFile::setPermissions(path, 
        QFileDevice::ReadOwner | QFileDevice::WriteOwner);  // 0600
    #endif
}
```

**Windows**:
```cpp
#ifdef Q_OS_WIN
#include <windows.h>
#include <aclapi.h>

void setOwnerOnlyACL(const QString& path) {
    // Set DACL to owner-only (requires Win32 API)
    // Details: https://docs.microsoft.com/en-us/windows/win32/secauthz/dacls
}
#endif
```

**Note**: Windows file permissions are more complex (ACLs); consider using encrypted file system (EFS) or BitLocker for additional protection.

### Atomic Writes

```cpp
bool EncryptedKeystore::save() {
    QString tmpPath = m_path + ".tmp." + QUuid::createUuid().toString();
    
    QFile tmp(tmpPath);
    if (!tmp.open(QIODevice::WriteOnly)) {
        return false;
    }
    
    QByteArray data = serializeToJson();
    if (tmp.write(data) != data.size()) {
        tmp.remove();
        return false;
    }
    
    tmp.flush();
    #ifdef Q_OS_UNIX
    fsync(tmp.handle());  // Force disk write
    #endif
    tmp.close();
    
    setRestrictivePermissions(tmpPath);
    
    // Atomic rename (POSIX guarantees atomicity)
    #ifdef Q_OS_WIN
    QFile::remove(m_path);  // Windows requires remove before rename
    #endif
    
    if (!QFile::rename(tmpPath, m_path)) {
        QFile::remove(tmpPath);
        return false;
    }
    
    return true;
}
```

## User-Facing Security

### Password Requirements

**Enforced**:
- Minimum 8 characters
- No empty passwords

**Recommended** (UI hints):
- 12+ characters
- Mix of uppercase, lowercase, numbers, symbols
- Avoid common words/patterns

**Password Strength Indicator**:
- Weak: < 8 chars or all lowercase
- Medium: 8-11 chars with some complexity
- Strong: 12+ chars with mixed complexity
- Very Strong: 16+ chars, high entropy

### Auto-Lock Configuration

**Options**:
- 5 minutes (high security)
- 15 minutes (default)
- 30 minutes
- 1 hour
- On minimize (optional)
- Never (not recommended, but allowed)

**Implementation**:
```cpp
QTimer m_autoLockTimer;

void setAutoLockTimeout(int minutes) {
    if (minutes > 0) {
        m_autoLockTimer.start(minutes * 60 * 1000);
    } else {
        m_autoLockTimer.stop();  // Disable auto-lock
    }
}

// Reset timer on any wallet operation
void resetAutoLock() {
    if (m_autoLockTimer.isActive()) {
        m_autoLockTimer.start();  // Restart with same interval
    }
}
```

### Unlock Dialog

**Features**:
- Password field (with show/hide toggle)
- Caps Lock warning indicator
- Wrong password counter (rate-limit after 5 attempts)
- "Remember for this session" option (keeps wallet unlocked)

**Rate Limiting**:
After 5 failed attempts, delay next attempt by:
- Attempt 6: 5 seconds
- Attempt 7: 10 seconds
- Attempt 8+: 30 seconds

Resets on successful unlock.

## Audit & Testing

### Security Test Suite

**Unit Tests**:
- `test_secure_erase.cpp`: Verify memory is zeroed after clear
- `test_kdf_parameters.cpp`: Verify KDF cost meets minimums
- `test_aead_tamper.cpp`: Modify ciphertext → verify MAC failure
- `test_password_rate_limit.cpp`: 5+ wrong attempts → verify delay

**Integration Tests**:
- `test_no_plaintext_on_disk.cpp`: Search wallet file for known secret → not found
- `test_lock_prevents_signing.cpp`: Lock wallet → attempt sign → expect exception
- `test_file_permissions.cpp`: Create wallet → verify mode 0600 (Unix)

**Manual Security Review**:
- [ ] All QDebug statements reviewed for secret leakage
- [ ] All exceptions reviewed for secret leakage in messages
- [ ] All file I/O reviewed for atomic writes + permissions
- [ ] All crypto library usage reviewed (correct parameters)

### Penetration Testing (Optional)

**Tools**:
- **Valgrind memcheck**: Detect uncleared sensitive buffers
- **ASAN (AddressSanitizer)**: Detect memory corruption
- **AFL (American Fuzzy Lop)**: Fuzz wallet file parser
- **Hashcat**: Test password KDF strength (offline attack simulation)

### Responsible Disclosure

**Security Issues**: Report to security@animica.org (planned)

**Bug Bounty**: Not offered at this stage (community project)

## Compliance & Standards

### Applicable Standards

**Cryptography**:
- NIST FIPS 204 (ML-DSA / Dilithium)
- NIST SP 800-132 (PBKDF2 guidance)
- RFC 7539 (ChaCha20-Poly1305)
- RFC 5869 (HKDF, if used for key derivation)

**Data Protection** (if applicable):
- GDPR (EU): User can export/delete their data
- CCPA (California): User can request data deletion

**Note**: Wallet is self-custodial; user is data controller.

## Known Limitations

### Not Implemented (Out of Scope)
1. **Secure boot / TPM attestation**: OS-level security
2. **Hardware wallet integration**: Future feature
3. **Multi-party computation (MPC)**: Future feature
4. **Quantum-resistant key exchange**: Not needed (no network key exchange in wallet)

### Inherent Risks
1. **User password strength**: Cannot prevent weak user-chosen passwords
2. **OS-level compromise**: Cannot defend against kernel-mode malware
3. **Physical theft (unlocked)**: Auto-lock reduces but doesn't eliminate risk

### Recommended External Mitigations
1. **Full disk encryption**: BitLocker (Windows), FileVault (macOS), LUKS (Linux)
2. **OS updates**: Keep OS patched for kernel security
3. **Antivirus/EDR**: Detect malware before it accesses wallet
4. **Password manager**: Generate strong, unique wallet password

## Incident Response

### If Wallet Password Compromised
1. User should immediately:
   - Create new wallet with new password
   - Transfer funds to new accounts
   - Destroy old wallet file
2. Attacker can decrypt old wallet file but cannot reverse transactions

### If Wallet File Stolen
1. User should:
   - Verify password is strong (12+ chars)
   - Optionally generate new wallet + transfer funds (precaution)
2. If password is weak, assume compromise

### If Private Key Leaked
1. User must:
   - Generate new account
   - Transfer all funds immediately
   - Abandon compromised key
2. Cannot revoke key (blockchain immutable)

## References

- **NIST FIPS 204**: Dilithium / ML-DSA specification
- **Argon2 RFC 9106**: Argon2 password hashing
- **libsodium docs**: XChaCha20-Poly1305 guidance
- **OWASP**: Cryptographic storage cheat sheet
- **Qt Security**: https://doc.qt.io/qt-6/security.html

## Version History

- **v1.0** (2025-01-29): Initial security architecture
