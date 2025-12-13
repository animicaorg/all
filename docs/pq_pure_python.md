# Pure-Python Post-Quantum Cryptography

This document describes Animica's pure-Python post-quantum cryptography implementation.

## Overview

Animica uses **pure-Python implementations** of NIST-standardized post-quantum algorithms:

- **ML-KEM-768** (Module-Lattice-Based Key Encapsulation Mechanism) - formerly Kyber768
- **ML-DSA-65** (Module-Lattice-Based Digital Signature Algorithm) - formerly Dilithium3

These implementations are vendored under `python/animica/_vendor/` and require **no compiled dependencies** (no liboqs, no oqs, no C extensions).

## Rationale

### Why Pure-Python?

1. **Zero compiled dependencies**: Works on any platform with Python 3.10+
2. **Easy installation**: No need to build liboqs or install system packages
3. **Transparent**: All cryptographic operations are in readable Python code
4. **Cross-platform**: Works on Linux, macOS, Windows, and even WebAssembly (Pyodide)
5. **Deterministic**: Same results across platforms and Python implementations

### Trade-offs

- **Performance**: ~10-100x slower than native implementations
- **Not formally validated**: These are reference implementations, not NIST CAVP validated
- **Educational focus**: Suitable for development, testing, and moderate-throughput production

## Architecture

```
python/animica/pq/              # Public API
    __init__.py                 # Main API: kem_*, sig_*, is_available()
    _drbg/                      # Deterministic RNG for testing
        aes.py                  # Pure-Python AES-256
        aes256_ctr_drbg.py      # NIST SP 800-90A CTR_DRBG
    tests/                      # Comprehensive test suite
        test_kem.py             # KEM roundtrip & negative tests
        test_sig.py             # Signature roundtrip & negative tests
        test_kat.py             # Known Answer Tests (determinism)
        test_backend_compat.py  # Legacy compatibility layer
        assets/                 # KAT test vectors

python/animica/_vendor/         # Vendored implementations
    kyber_py/                   # ML-KEM-768
        kyber768.py
        LICENSE
    dilithium_py/               # ML-DSA-65
        dilithium3.py
        LICENSE
```

## API Reference

### Availability

```python
from animica.pq import is_available, get_mode

# Check if PQ is available
if is_available():
    print(f"PQ mode: {get_mode()}")  # 'pure' or 'disabled'
```

### Key Encapsulation (KEM)

```python
from animica.pq import kem_keygen, kem_encaps, kem_decaps

# Generate keypair
encapsulation_key, decapsulation_key = kem_keygen()

# Encapsulate (Alice generates shared secret for Bob)
shared_secret, ciphertext = kem_encaps(encapsulation_key)

# Decapsulate (Bob recovers shared secret)
recovered_secret = kem_decaps(decapsulation_key, ciphertext)

assert shared_secret == recovered_secret
```

**Key sizes (ML-KEM-768):**
- Encapsulation key (public): 1184 bytes
- Decapsulation key (secret): 2400 bytes
- Ciphertext: 1088 bytes
- Shared secret: 32 bytes

### Digital Signatures

```python
from animica.pq import sig_keygen, sig_sign, sig_verify

# Generate keypair
public_key, secret_key = sig_keygen()

# Sign message
message = b"Hello, Animica!"
signature = sig_sign(secret_key, message)

# Verify signature
is_valid = sig_verify(public_key, message, signature)
assert is_valid
```

**Key sizes (ML-DSA-65):**
- Public key: 1952 bytes
- Secret key: 4000 bytes
- Signature: 3293 bytes (maximum)

## Configuration

### Environment Variables

- **`ANIMICA_PQ_MODE`**: Controls PQ behavior
  - `pure` (default): Use pure-Python implementations
  - `disabled`: Disable PQ (for testing fallback behavior)

Example:
```bash
# Disable PQ temporarily
export ANIMICA_PQ_MODE=disabled
python -c "from animica.pq import is_available; print(is_available())"  # False

# Re-enable (or unset)
export ANIMICA_PQ_MODE=pure
```

## Testing

### Run All PQ Tests

```bash
# Activate venv first
source .venv/bin/activate

# Run tests
pytest -q python/animica/pq/tests

# Verbose output
pytest -v python/animica/pq/tests

# Run specific test file
pytest python/animica/pq/tests/test_kem.py
pytest python/animica/pq/tests/test_sig.py
pytest python/animica/pq/tests/test_kat.py
```

### Quick Smoke Tests

```bash
# Test KEM
python -c "from animica.pq import kem_keygen, kem_encaps, kem_decaps; \
  ek,dk=kem_keygen(); k,ct=kem_encaps(ek); \
  assert kem_decaps(dk,ct)==k; print('✓ KEM ok')"

# Test signatures
python -c "from animica.pq import sig_keygen, sig_sign, sig_verify; \
  pk,sk=sig_keygen(); m=b'hi'; s=sig_sign(sk,m); \
  assert sig_verify(pk,m,s); print('✓ SIG ok')"
```

### Test Coverage

The test suite includes:

1. **Roundtrip tests**: Encrypt/decrypt, sign/verify
2. **Negative tests**: Wrong keys, tampered data, invalid sizes
3. **Determinism tests (KATs)**: Same seed produces same output
4. **Edge cases**: Empty messages, large messages, multiple operations
5. **Mode tests**: Disabled mode, availability checks
6. **Compatibility tests**: Legacy `get_backend()` API

## Known Answer Tests (KATs)

Deterministic test vectors are in `python/animica/pq/tests/assets/`:

- `kem_kat_simple.json`: ML-KEM-768 vectors
- `sig_kat_simple.json`: ML-DSA-65 vectors

These ensure implementation consistency across platforms and prevent regressions.

## Performance Characteristics

Approximate timings on a modern CPU (pure Python, unoptimized):

| Operation | Time (ms) | Notes |
|-----------|-----------|-------|
| KEM Keygen | ~50-100 | One-time per keypair |
| KEM Encaps | ~30-60 | Per message |
| KEM Decaps | ~30-60 | Per message |
| Sig Keygen | ~100-200 | One-time per keypair |
| Sig Sign | ~150-300 | Per message |
| Sig Verify | ~50-100 | Per message |

For high-throughput applications, consider:
- Caching keypairs
- Using native implementations (e.g., liboqs via PyPI, if available)
- Running operations in parallel (multi-processing)

## Security Considerations

### Randomness

- **Production**: Uses `os.urandom()` (cryptographically secure)
- **Testing**: Accepts optional seed parameters for deterministic generation (KATs)

### Implementation Notes

1. **Not constant-time**: Python operations are not constant-time; vulnerable to timing attacks
2. **No formal validation**: Not NIST CAVP validated (reference implementation only)
3. **Memory safety**: Python's memory management mitigates buffer overflows but doesn't zero secrets
4. **Side channels**: Susceptible to cache-timing, power analysis (not hardened)

**Recommendation**: For high-security applications, use validated native implementations.

### When to Use Pure-Python PQ

✅ **Good for:**
- Development and testing
- CI/CD environments without native dependencies
- Cross-platform compatibility requirements
- Low to moderate throughput workloads
- Educational purposes

❌ **Not recommended for:**
- High-frequency trading or low-latency systems
- Environments requiring FIPS 140-3 compliance
- Applications under active side-channel attack threat
- Performance-critical cryptographic operations

## Migration from liboqs

If migrating from liboqs-based code:

### Old (liboqs-python)
```python
import oqs
sig = oqs.Signature("Dilithium3")
pk = sig.generate_keypair()[0]
```

### New (pure-Python)
```python
from animica.pq import sig_keygen
pk, sk = sig_keygen()
```

### Compatibility Layer

The old `get_backend()` pattern still works:

```python
from animica.pq import get_backend

backend, label = get_backend()
print(f"Backend: {label}")  # "pure" or "disabled"

pk, sk = backend.keygen()
sig = backend.sign(sk, b"message")
assert backend.verify(pk, b"message", sig)
```

## Troubleshooting

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'animica.pq'`

**Solution**:
```bash
source .venv/bin/activate
pip install -e ./python
```

### PQ Disabled

**Problem**: `RuntimeError: PQ cryptography is disabled`

**Solution**:
```bash
# Check mode
python -c "import os; print(os.getenv('ANIMICA_PQ_MODE', 'pure'))"

# Enable if needed
export ANIMICA_PQ_MODE=pure
# or unset ANIMICA_PQ_MODE
```

### Test Failures

**Problem**: Tests fail with `AssertionError` or `ValueError`

**Solution**:
```bash
# Re-run with verbose output
pytest -vv python/animica/pq/tests/test_kem.py

# Check Python version (requires 3.10+)
python --version

# Reinstall package
pip install -e ./python --force-reinstall --no-cache-dir
```

## Standards and References

- **FIPS 203**: Module-Lattice-Based Key-Encapsulation Mechanism Standard (ML-KEM)
  - https://csrc.nist.gov/pubs/fips/203/final
- **FIPS 204**: Module-Lattice-Based Digital Signature Standard (ML-DSA)
  - https://csrc.nist.gov/pubs/fips/204/final
- **NIST PQC Project**: https://csrc.nist.gov/projects/post-quantum-cryptography

## Contributing

To improve the pure-Python PQ implementation:

1. **Optimize critical paths** (e.g., polynomial operations)
2. **Add more KAT vectors** from NIST ACVP
3. **Implement constant-time operations** where feasible
4. **Add alternative algorithms** (e.g., SPHINCS+, Falcon)
5. **Benchmark and profile** performance

See `CONTRIBUTING.md` for development guidelines.

## License

The pure-Python PQ implementations are licensed under the MIT License.
See `python/animica/_vendor/kyber_py/LICENSE` and `python/animica/_vendor/dilithium_py/LICENSE`.

Also see `THIRD_PARTY_NOTICES.md` at the repository root.

---

*Last updated: 2024-12-13*
