# Animica Post-Quantum Cryptography Policy

**Version:** 1.0  
**Date:** 2026-02-11  
**Status:** MANDATORY

---

## 1. Overview

Animica implements **custom post-quantum (PQ) cryptography** using pure-Python implementations based on CRYSTALS-Dilithium, SPHINCS+, and ML-KEM (Kyber). These implementations are:

- Self-contained and deterministic
- Auditable and maintainable by the Animica team
- Compatible across Python, TypeScript, and WASM environments
- Free from external PQ library dependencies

---

## 2. NO LIBOQS POLICY

### 2.1 Prohibition

The following libraries and tools are **STRICTLY PROHIBITED** in all Animica repositories, packages, services, and deployments:

❌ **liboqs** (Open Quantum Safe library)  
❌ **oqs** (liboqs Python/Node bindings)  
❌ **open-quantum-safe** (OQS project libraries)  
❌ **pqclean** (Reference PQ implementations)  
❌ **Any third-party PQ library wrappers**

### 2.2 Rationale

1. **Determinism:** External PQ libraries may have non-deterministic behavior or version mismatches
2. **Portability:** liboqs requires native compilation and platform-specific builds
3. **Auditability:** External libraries are harder to audit and customize
4. **Maintenance:** Dependencies create supply-chain risks and version conflicts
5. **Browser compatibility:** Native libraries cannot run in browsers without complex WASM packaging

### 2.3 Exceptions

**NONE.** There are no exceptions to this policy.

Even for testing or development environments, external PQ libraries must not be used. All PQ operations must use Animica's custom implementations.

---

## 3. Authorized PQ Implementations

### 3.1 Python Implementations

**Location:** `python/animica/pq/` and `python/animica/_vendor/`

| Algorithm | Implementation | Status |
|-----------|----------------|--------|
| **Dilithium3** | `python/animica/_vendor/dilithium_py/dilithium3.py` | ✅ Production |
| **SPHINCS+ SHAKE-128s** | `pq/py/algs/sphincs_shake_128s.py` | ✅ Production |
| **Kyber768 (KEM)** | `python/animica/_vendor/kyber_py/kyber768.py` | ✅ Production (P2P only) |

**API:** `python/animica/pq/__init__.py`

```python
from animica.pq import sig_keygen, sig_sign, sig_verify
from animica.pq import kem_keygen, kem_encaps, kem_decaps
```

### 3.2 TypeScript/JavaScript Implementations

**Location:** `packages/animica-crypto/`

**Status:** Partial implementation (address derivation, SignBytes construction)

**Full PQ backend:** Must be implemented in wallet extension by either:
1. Porting Python implementations to TypeScript
2. Compiling Python implementations to WASM
3. Using Pyodide to run Python code in browser

**Package:** `@animica/crypto`

```typescript
import { addressFromPubkey, buildSignBytes } from '@animica/crypto';
```

### 3.3 Rust Implementations (Future)

**Status:** Not yet implemented

When implemented, must follow the same NO LIBOQS policy and use custom Rust ports of the Python implementations.

---

## 4. Implementation Requirements

### 4.1 For All New Code

When adding PQ cryptography to any part of the Animica stack:

1. ✅ **Use existing APIs** from `python/animica/pq/` or `packages/animica-crypto/`
2. ✅ **Port from Python** if implementing in a new language (TypeScript, Rust, etc.)
3. ✅ **Add test vectors** generated from Python CLI to validate consistency
4. ✅ **Document deviations** if any optimization or adaptation is needed
5. ❌ **NEVER import** liboqs, oqs, pqclean, or similar

### 4.2 For Browser/Web Code

Browser extensions, web UIs, and Dapp IDEs:

1. ✅ **Use `@animica/crypto`** for address derivation and SignBytes construction
2. ✅ **Request signatures** from wallet extension (don't generate keys in Dapp IDE)
3. ✅ **Implement full PQ backend** in wallet extension using:
   - TypeScript port of Dilithium3/SPHINCS+
   - WASM-compiled Python implementations
   - Pyodide runtime (if size permits)
4. ❌ **NEVER use** any PQ library from NPM that wraps liboqs

### 4.3 For Testing

1. ✅ **Use Python CLI** to generate test keypairs and signatures
2. ✅ **Store test vectors** in `test-vectors/` directories
3. ✅ **Validate** that all implementations produce identical outputs
4. ❌ **NEVER use** liboqs even for test vector generation

---

## 5. CI Enforcement

### 5.1 Automated Checks

The CI pipeline includes checks to prevent liboqs from being added:

**File:** `.github/workflows/pq-policy-check.yml`

Checks performed:
1. Grep for "liboqs", "oqs", "open-quantum-safe" in:
   - `package.json` files
   - `requirements.txt` files
   - `Cargo.toml` files
   - `Dockerfile` files
   - Python imports
   - TypeScript/JavaScript imports
2. Validate `packages/animica-crypto/package.json` has NO liboqs dependencies
3. Fail the build if any violations are found

### 5.2 Pull Request Review

All PRs that touch cryptographic code must:
1. Pass automated PQ policy checks
2. Be reviewed by a core team member familiar with PQ implementation
3. Include test vectors if modifying signing/verification

### 5.3 Violation Response

If a liboqs dependency is accidentally added:
1. **Immediate revert** of the PR/commit
2. **Remove dependency** from package manifests
3. **Re-implement** using custom PQ code
4. **Add test** to prevent future occurrences

---

## 6. Migration from liboqs (Historical)

### 6.1 Background

Earlier versions of Animica used liboqs for PQ cryptography. This was phased out due to:
- Version mismatch issues (0.14.x vs 0.15.x)
- Native compilation requirements
- Browser incompatibility
- Maintenance burden

### 6.2 Current State

As of 2026-02-11:
- ✅ All Python code uses `python/animica/pq/` (custom implementations)
- ✅ No production dependencies on liboqs
- ⚠️ Some legacy code may have commented-out liboqs imports (remove on sight)
- ⚠️ Old test fixtures may reference liboqs (migrate to custom PQ)

### 6.3 Complete Migration Checklist

- [x] Python node implementation
- [x] Python CLI wallet
- [x] Transaction signing/verification
- [ ] Browser wallet extension (in progress)
- [ ] Dapp IDE (in progress)
- [ ] Mobile wallet (future)
- [ ] Rust SDK (future)

---

## 7. Algorithm Selection

### 7.1 Default Algorithm

**Dilithium3** (ML-DSA-65, Algorithm ID: `0x1001`) is the default for:
- User accounts and wallets
- Transaction signing
- Block header signing

**Specifications:**
- Public key: 1952 bytes
- Secret key: 4000 bytes (canonical format)
- Signature: 3293 bytes
- Security level: 128-bit

### 7.2 Alternative Algorithms

**SPHINCS+ SHAKE-128s** (Algorithm ID: `0x1002`):
- Stateless hash-based signatures
- Smaller keys (64 bytes each)
- Larger signatures (7856 bytes)
- Fallback when Dilithium3 is not available

**Kyber768 KEM** (Algorithm ID: `0x2001`):
- Key encapsulation for P2P handshakes
- NOT used for transaction signing
- Wallet extension typically doesn't need this

### 7.3 Future Algorithms

If NIST finalizes additional PQ standards, Animica may add:
- ML-DSA-87 (Dilithium5) for higher security
- Falcon-512/1024 for compact signatures
- NTRU Prime for diversity

**All must follow NO LIBOQS policy** and be implemented from scratch or ported from reference code.

---

## 8. Security Considerations

### 8.1 Domain Separation

All PQ signatures MUST include explicit domain strings:
- Prevents cross-domain signature reuse attacks
- Domain examples: `"tx"`, `"animica.tx.v1"`, `"p2p/identity"`
- See `pq/py/sign.py` for implementation

### 8.2 Prehashing

Messages are prehashed with SHA3-512 before signing:
- Ensures fixed-length input to signing algorithm
- Matches across all implementations
- Documented in SignBytes construction

### 8.3 Key Storage

Secret keys must be:
- Encrypted at rest (wallet files, extension storage)
- Never logged or transmitted unencrypted
- Zeroed from memory after use (where possible)
- Protected by OS-level permissions (file mode 0600)

### 8.4 Side-Channel Resistance

While full constant-time implementation is difficult in Python/TypeScript:
- Use deterministic algorithms where possible
- Avoid branching on secret data in hot paths
- Consider WASM for more control over timing

---

## 9. Documentation Requirements

### 9.1 For Each Implementation

Every PQ implementation must include:
1. **Algorithm name and ID** (e.g., "Dilithium3, 0x1001")
2. **Key sizes** (public, secret, signature)
3. **Reference to Python implementation** (canonical source)
4. **Test vectors** (generated from Python CLI)
5. **Known limitations** (e.g., not constant-time)

### 9.2 For New Language Ports

When porting to a new language (TypeScript, Rust, Go, etc.):
1. **Create design document** explaining approach (port vs WASM vs FFI)
2. **Add test suite** validating against Python outputs
3. **Benchmark** to ensure reasonable performance
4. **Review** by core team before merging

---

## 10. Compliance and Auditing

### 10.1 Regular Audits

Every release:
1. **Scan** all dependencies for liboqs/oqs/pqclean
2. **Verify** CI checks are passing
3. **Review** any new crypto code

### 10.2 Security Audits

External security audits should:
1. **Verify** no liboqs dependencies exist
2. **Review** custom PQ implementations for correctness
3. **Test** cross-implementation consistency
4. **Check** domain separation and prehashing

---

## 11. Frequently Asked Questions

### Q: Why not use liboqs? It's the standard.

A: liboqs is excellent but doesn't meet Animica's needs:
- Native compilation required (not browser-friendly)
- Version mismatches between 0.14.x and 0.15.x caused issues
- We need full control for deterministic consensus
- Custom implementations are more auditable and portable

### Q: Is the Python implementation secure?

A: Yes, when used correctly:
- Based on reference implementations (CRYSTALS-Dilithium, SPHINCS+)
- Validated against test vectors
- Domain-separated to prevent attacks
- Not constant-time, but suitable for blockchain signing (no interactive protocols)

### Q: What about future NIST standards?

A: We'll add them as needed, following the same NO LIBOQS policy. Either port reference code or implement from spec.

### Q: Can I use liboqs for testing?

A: No. All test vectors must be generated from Python CLI using our custom implementations.

### Q: What if I need PQ crypto in a new language?

A: Port from `python/animica/_vendor/` or `pq/py/`. Document deviations and add test vectors.

---

## 12. Contact and Governance

### Policy Owner

**Animica Core Team**

### Changes to This Policy

This policy can only be changed by:
1. **Core team consensus**
2. **PR updating this document**
3. **Corresponding CI check updates**

### Reporting Violations

If you discover a liboqs dependency:
1. **File an issue** on GitHub
2. **Tag** `@animicaorg` and maintainers
3. **Do not use** the code in production

---

## 13. Summary

### ✅ DO

- Use `python/animica/pq/` or `@animica/crypto`
- Port Python implementations to new languages
- Generate test vectors from Python CLI
- Document all deviations and design choices

### ❌ DON'T

- Use liboqs, oqs, pqclean, or similar
- Import third-party PQ libraries
- Skip test vector validation
- Generate keys/sign transactions in Dapp IDE (use wallet extension)

---

**This policy is mandatory for all Animica code and must be followed without exception.**

**Violations will result in rejected PRs and reverted commits.**
