# Animica Security Utilities

This module provides security-focused utilities for the Animica blockchain, with emphasis on mitigating timing side-channels in pure Python code.

## Overview

Pure Python cannot provide hardware-level constant-time guarantees, but we can apply best practices to reduce timing leakage in security-sensitive operations.

## Modules

### `ct.py` - Constant-Time Helpers

Provides best-effort constant-time comparison functions using `hmac.compare_digest()`:

```python
from animica.security.ct import ct_eq_bytes, ct_eq_str

# Compare byte strings (passwords, tokens, HMAC tags)
if ct_eq_bytes(computed_hmac, provided_hmac):
    print("HMAC verified")

# Compare strings (session IDs, API keys)
if ct_eq_str(token, expected_token):
    print("Token valid")

# Avoid early returns - check all conditions
checks = [
    ct_eq_bytes(hmac1, expected_hmac1),
    ct_eq_bytes(hmac2, expected_hmac2),
    ct_eq_bytes(hmac3, expected_hmac3),
]
if ct_all_checks(*checks):
    print("All checks passed")
```

**Functions:**
- `ct_eq_bytes(a, b)` - Compare byte strings
- `ct_eq_str(a, b)` - Compare UTF-8 strings
- `ct_select(mask, if_true, if_false)` - Bitwise selection
- `ct_memcmp(a, b)` - Compare memoryviews
- `ct_all_checks(*checks)` - Evaluate all checks without short-circuit
- `ct_any_check(*checks)` - Evaluate any check without short-circuit

### `batch_verify.py` - Batch Signature Verification

Parallel signature verification for improved throughput:

```python
from animica.security.batch_verify import VerifyItem, verify_batch

# Create verification items
items = [
    VerifyItem(
        index=i,
        message=messages[i],
        signature=signatures[i],
        public_key=public_keys[i],
        alg_id=0x2002,  # ML-DSA-65
    )
    for i in range(len(messages))
]

# Verify in parallel
results = verify_batch(items, workers=4)

# Check results
all_valid = all(r.valid for r in results)
```

**Configuration:**
```bash
# Set worker count
export ANIMICA_VERIFY_WORKERS=4
```

**Benefits:**
- Parallel verification improves throughput
- Amortizes timing variance across batch
- Deterministic ordering of results

### `cache.py` - Hot Path Caching

LRU caches for expensive operations:

```python
from animica.security.cache import (
    get_tx_hash_cache,
    get_sign_msg_cache,
    get_block_template_cache,
)

# Transaction hash caching
tx_hash = get_tx_hash_cache().get_or_compute(tx_bytes)

# Signature message caching
sign_msg = get_sign_msg_cache().get_or_compute(
    tx_bytes,
    lambda: compute_signing_message(tx)
)

# Block template caching with TTL
template_cache = get_block_template_cache(ttl_ms=250)
cached = template_cache.get()
if cached is None:
    cached = build_block_template()
    template_cache.put(cached)
```

**Configuration:**
```python
# TTL for block template cache (milliseconds)
template_cache = get_block_template_cache(ttl_ms=500)
```

## Testing

### Unit Tests

```bash
# All security tests (fast)
pytest python/animica/security/tests/ -v -m "not slow"

# Constant-time helpers
pytest python/animica/security/tests/test_ct.py -v

# Batch verification
pytest python/animica/security/tests/test_batch_verify.py -v

# Caching
pytest python/animica/security/tests/test_cache.py -v
```

### Timing Variability Tests (Opt-in)

These tests are probabilistic and may be flaky due to OS/CPU noise:

```bash
# Enable timing tests
ANIMICA_TIMING_TESTS=1 pytest python/animica/security/tests/test_timing_variability.py -v
```

**Note:** Timing tests are disabled by default to avoid CI flakiness.

## Benchmarking

See `python/animica/bench/README.md` for detailed benchmarking documentation.

Quick start:

```bash
# Run all benchmarks
python -m animica.bench.bench_verify

# Single verification benchmark
python -m animica.bench.bench_verify --single --iterations=100

# Batch verification scaling
python -m animica.bench.bench_verify --batch --workers=4
```

## Usage Guidelines

### When to Use Constant-Time Helpers

Use `ct_*` helpers for comparing:
- ✅ Passwords and password hashes
- ✅ API tokens and session IDs
- ✅ HMAC tags and authentication codes
- ✅ Cryptographic signatures (when not using batch verify)
- ✅ Shared secrets and ephemeral keys
- ✅ Any data where timing leaks could aid an attacker

Do NOT use for:
- ❌ Public data (chain IDs, block heights, gas prices)
- ❌ Display/formatting comparisons
- ❌ Debug/logging comparisons
- ❌ Performance-critical hot loops (unless timing matters)

### Normalized Error Messages

Return generic messages to external callers, log details internally:

```python
# ❌ BAD: Reveals failure reason
if not ct_eq_bytes(hmac, expected):
    return "HMAC verification failed"
if not check_timestamp():
    return "Timestamp expired"

# ✅ GOOD: Normalized external message
valid_hmac = ct_eq_bytes(hmac, expected)
valid_ts = check_timestamp()

if not ct_all_checks(valid_hmac, valid_ts):
    logger.debug("Auth failed: hmac=%s ts=%s", valid_hmac, valid_ts)
    return "Authentication failed"  # Generic message
```

### Cheap Checks First

For DoS defense, validate cheap conditions before expensive crypto:

```python
def validate_transaction(tx):
    # 1. Cheap public checks (no secrets)
    if len(tx.data) > MAX_SIZE:
        return False
    if tx.gas_limit == 0:
        return False
    
    # 2. Expensive cryptography (with ct helpers)
    if not verify_signature(tx):  # Uses ct_eq_bytes internally
        return False
    
    return True
```

### Batch Verification

Use batch verification in hot paths:

```python
# Mempool admission
def admit_transactions(txs):
    items = [make_verify_item(tx) for tx in txs]
    results = verify_batch(items)
    return [tx for tx, r in zip(txs, results) if r.valid]

# Block validation
def validate_block(block):
    items = [make_verify_item(tx) for tx in block.txs]
    results = verify_batch(items)
    return all(r.valid for r in results)
```

## Limitations

**What we CAN do:**
- Use `hmac.compare_digest()` (implemented in C)
- Avoid obvious early-exit timing leaks
- Normalize error messages
- Batch verification for throughput

**What we CANNOT prevent:**
- CPython interpreter timing variance
- Garbage collection pauses
- OS scheduler preemption
- CPU cache effects
- Dynamic dispatch overhead

For hardware-level timing guarantees:
- Use dedicated HSMs or secure enclaves
- Implement critical paths in C/Rust with constant-time primitives
- Use hardware timing randomization

## References

- [SECURITY.md](../../../../SECURITY.md) - Main security documentation
- [hmac.compare_digest() docs](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
- [Timing Attacks and Python](https://www.nccgroup.com/us/research-blog/timing-attacks-and-python-string-comparison/)
- [libsodium constant-time API](https://doc.libsodium.org/helpers#constant-time-test-for-equality)

## Contributing

When adding new security-sensitive code:

1. Use `ct_*` helpers for all secret comparisons
2. Avoid early returns based on secrets
3. Normalize error messages
4. Add tests to verify behavior
5. Document any limitations or assumptions

See `SECURITY.md` for full coding guidelines.
