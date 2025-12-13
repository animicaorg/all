# Security Policy

This document describes security practices and policies for the Animica blockchain.

## Reporting Security Issues

If you discover a security vulnerability, please email security@animica.org with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if available)

We will acknowledge receipt within 48 hours and provide a timeline for resolution.

## Timing Side Channels in Pure Python

### Overview

Animica's consensus, execution, and cryptographic paths are implemented in pure Python for portability and ease of deployment. However, **pure Python cannot provide the same timing guarantees as constant-time implementations in C, Rust, or assembly**.

This section documents our approach to mitigating timing side-channels in pure Python code.

### Python Timing Limitations

Python is an interpreted language with several characteristics that make true constant-time code impossible:

1. **Interpreter Overhead**: CPython's bytecode interpreter adds variable overhead depending on operation type and object types
2. **Dynamic Dispatch**: Attribute lookups, method calls, and type checks have variable timing
3. **Garbage Collection**: Stop-the-world GC pauses occur unpredictably
4. **Memory Layout**: No control over object layout or CPU cache effects
5. **OS Scheduling**: Process preemption by the OS scheduler
6. **JIT Effects**: PyPy and other implementations may have different timing characteristics

### Threat Model

Our timing hardening targets **LOCAL timing side-channels** where an attacker can:
- Measure response times with microsecond precision
- Make many repeated measurements
- Control input values to probe timing differences

We do NOT attempt to mitigate:
- **Remote timing attacks** over networks (network jitter dominates)
- **Cache timing attacks** (require same-core execution)
- **Speculative execution attacks** (require hardware access)

For remote APIs:
- Network jitter (milliseconds) far exceeds Python timing variance (microseconds)
- Rate limiting and quotas prevent statistical attacks
- Server load introduces additional noise

### Mitigation Strategy

We apply defense-in-depth with the following techniques:

#### 1. Constant-Time Comparison Helpers (`animica.security.ct`)

Use `hmac.compare_digest()` for all security-sensitive comparisons:

```python
from animica.security.ct import ct_eq_bytes, ct_eq_str

# ❌ BAD: Timing leak from early exit
if password == expected_password:
    return True

# ✅ GOOD: Constant-time comparison
if ct_eq_str(password, expected_password):
    return True
```

Available helpers:
- `ct_eq_bytes(a, b)` - Compare byte strings
- `ct_eq_str(a, b)` - Compare UTF-8 strings
- `ct_memcmp(a, b)` - Compare memoryviews
- `ct_select(mask, if_true, if_false)` - Bitwise selection
- `ct_all_checks(*checks)` - Evaluate all checks without short-circuit
- `ct_any_check(*checks)` - Evaluate all checks without short-circuit

#### 2. Normalized Verification Paths

Avoid secret-dependent early returns. Process all checks before deciding:

```python
# ❌ BAD: Early return on first failure
def verify_multi_sig(sigs):
    for sig in sigs:
        if not verify_sig(sig):
            return False  # Reveals which signature failed
    return True

# ✅ GOOD: Check all, then decide
def verify_multi_sig(sigs):
    results = [verify_sig(sig) for sig in sigs]
    return ct_all_checks(*results)
```

#### 3. Normalized Error Messages

Return normalized error messages to external callers. Log detailed reasons to debug logs only:

```python
# ❌ BAD: Error message reveals failure reason
if not hmac.compare_digest(tag, computed_tag):
    return "HMAC verification failed"
if not check_timestamp(data):
    return "Timestamp expired"

# ✅ GOOD: Normalized message externally
valid_hmac = hmac.compare_digest(tag, computed_tag)
valid_ts = check_timestamp(data)
if not ct_all_checks(valid_hmac, valid_ts):
    logger.debug("Validation failed: hmac=%s ts=%s", valid_hmac, valid_ts)
    return "Authentication failed"  # Generic message
```

#### 4. Batch Verification

Use batch verification to amortize timing variance across multiple operations:

```python
from animica.security.batch_verify import VerifyItem, verify_batch

items = [
    VerifyItem(i, messages[i], signatures[i], public_keys[i], alg_id)
    for i in range(len(messages))
]

results = verify_batch(items)  # Parallel verification
```

Benefits:
- Parallelization improves throughput
- Timing variance averaged across batch
- Statistical attacks harder with batch processing

#### 5. Cheap Checks First

For DoS defense, perform cheap checks before expensive crypto:

```python
def validate_transaction(tx):
    # Cheap checks first (no secrets involved)
    if len(tx.data) > MAX_SIZE:
        return "Transaction too large"
    if tx.chain_id != EXPECTED_CHAIN_ID:
        return "Wrong chain"
    if tx.gas_limit == 0:
        return "Invalid gas"
    
    # Expensive crypto last
    if not verify_signature(tx):
        return "Invalid signature"  # Normalized message
```

### Coding Rules

When writing security-sensitive code:

1. **Use ct helpers for all secret comparisons**
   - Passwords, tokens, HMAC tags, session IDs
   - Addresses (when used for authentication)
   - Signatures, handshake keys, shared secrets

2. **Avoid early returns based on secrets**
   - Evaluate all checks before returning
   - Use `ct_all_checks()` or `ct_any_check()`

3. **Normalize error messages**
   - Generic messages to external callers
   - Detailed reasons in debug logs only

4. **Use batch verification where possible**
   - Mempool admission
   - Block validation
   - Transaction pools

5. **Order checks by cost**
   - Public input validation first
   - Expensive crypto last
   - DoS prevention

### Configuration

#### Batch Verification Workers

Control parallelism with environment variable:

```bash
export ANIMICA_VERIFY_WORKERS=4  # Number of worker processes
```

Default: `max(1, cpu_count() - 1)`

#### Timing Tests

Enable timing variability tests (opt-in due to flakiness):

```bash
export ANIMICA_TIMING_TESTS=1
pytest python/animica/security/tests/test_timing_variability.py
```

These tests are **probabilistic** and may fail due to OS/CPU noise.

### Testing

Run security tests:

```bash
# Constant-time helpers
pytest python/animica/security/tests/test_ct.py

# Batch verification
pytest python/animica/security/tests/test_batch_verify.py

# Timing variability (opt-in)
ANIMICA_TIMING_TESTS=1 pytest python/animica/security/tests/test_timing_variability.py
```

Run benchmarks:

```bash
# All benchmarks
python -m animica.bench.bench_verify

# Specific benchmarks
python -m animica.bench.bench_verify --single
python -m animica.bench.bench_verify --batch --workers=4
python -m animica.bench.bench_verify --block
```

### Limitations and Caveats

**What we CAN do:**
- Use `hmac.compare_digest()` (implemented in C)
- Avoid obvious timing leaks from early returns
- Normalize error messages
- Batch verification for throughput

**What we CANNOT prevent:**
- CPython interpreter timing variance
- Garbage collection pauses
- OS scheduler preemption
- CPU cache effects from Python object layout
- Dynamic dispatch overhead

**Recommendation:**
For applications requiring hardware-level timing guarantees:
- Use dedicated HSMs or secure enclaves
- Implement critical paths in C/Rust with constant-time primitives
- Use hardware timing randomization (if available)

### References

- [hmac.compare_digest() documentation](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
- [Timing Attack on Python String Comparison](https://www.nccgroup.com/us/research-blog/timing-attacks-and-python-string-comparison/)
- [Cryptography Coding Rules (OpenSSL)](https://wiki.openssl.org/index.php/Coding_Style#Constant_Time)
- [libsodium constant-time comparison](https://doc.libsodium.org/helpers#constant-time-test-for-equality)

## Additional Security Topics

For other security topics, see:
- `docs/security/THREAT_MODEL.md` - Overall threat model
- `docs/security/DOS_DEFENSES.md` - DoS protection
- `docs/security/AUDIT_CHECKLIST.md` - Security audit checklist
- `docs/security/RESPONSIBLE_DISCLOSURE.md` - Vulnerability disclosure process
