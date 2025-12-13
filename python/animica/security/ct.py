"""
Constant-Time Comparison Helpers for Animica

This module provides best-effort constant-time comparison functions for
security-sensitive data (passwords, tokens, HMAC tags, signatures, etc.).

⚠️ IMPORTANT LIMITATIONS OF PURE PYTHON CONSTANT-TIME CODE:

Python is an interpreted language with dynamic dispatch, garbage collection,
and no control over memory layout or CPU cache behavior. Therefore, pure Python
code CANNOT provide the same timing guarantees as constant-time implementations
in languages like C, Rust, or assembly.

What we can do in pure Python:
1. Use hmac.compare_digest() which is designed to mitigate timing attacks
   (it's implemented in C and uses a constant-time comparison loop)
2. Avoid early returns based on secret data (compare all bytes/fields)
3. Use bitwise operations for selection instead of if/else branches
4. Structure code to avoid secret-dependent control flow

What we CANNOT prevent in pure Python:
1. CPython interpreter overhead and variability
2. Garbage collection pauses
3. Dynamic dispatch and attribute lookup timing
4. CPU cache effects from Python object layout
5. OS scheduler preemption
6. JIT compilation effects (PyPy, etc.)

INTENDED MITIGATIONS:

1. Normalize verification paths: process all checks regardless of early failures
2. Combine multiple checks and decide after full evaluation
3. Return normalized error messages; log detailed reasons to debug logs only
4. Use these helpers for all security-sensitive comparisons
5. For critical paths (e.g., signature verification), use batch verification
   to amortize timing variance across multiple operations

THREAT MODEL:

These helpers are designed to mitigate LOCAL timing side-channels where an
attacker can measure response times with high precision (microsecond-level).
Remote timing attacks over networks are generally not a concern due to:
- Network jitter (milliseconds)
- Server load variability
- Rate limiting / quotas

However, we still apply these defenses throughout to establish defense-in-depth.

USAGE:

Instead of:
    if password == expected_password:  # ❌ TIMING LEAK
        return True

Use:
    if ct_eq_str(password, expected_password):  # ✅ CONSTANT-TIME
        return True

Instead of:
    if hmac_tag == computed_hmac:  # ❌ TIMING LEAK
        return True

Use:
    if ct_eq_bytes(hmac_tag, computed_hmac):  # ✅ CONSTANT-TIME
        return True
"""

from __future__ import annotations

import hmac
from typing import Optional


def ct_eq_bytes(a: Optional[bytes], b: Optional[bytes]) -> bool:
    """
    Constant-time comparison of two byte strings.
    
    Uses hmac.compare_digest() which is implemented in C and designed to
    mitigate timing attacks. Returns False if either input is None.
    
    Note: Length comparison is NOT constant-time (length is not considered
    secret in our threat model). hmac.compare_digest() handles different
    lengths safely by returning False immediately.
    
    Args:
        a: First byte string (may be None)
        b: Second byte string (may be None)
    
    Returns:
        True if both are non-None and equal, False otherwise
    
    Examples:
        >>> ct_eq_bytes(b"secret", b"secret")
        True
        >>> ct_eq_bytes(b"secret", b"guess")
        False
        >>> ct_eq_bytes(b"secret", None)
        False
        >>> ct_eq_bytes(None, None)
        False
    """
    if a is None or b is None:
        return False
    # hmac.compare_digest handles different lengths safely (returns False)
    return hmac.compare_digest(a, b)


def ct_eq_str(a: Optional[str], b: Optional[str]) -> bool:
    """
    Constant-time comparison of two UTF-8 strings.
    
    Encodes both strings as UTF-8 bytes and uses ct_eq_bytes() for comparison.
    Returns False if either input is None.
    
    Args:
        a: First string (may be None)
        b: Second string (may be None)
    
    Returns:
        True if both are non-None and equal (as UTF-8), False otherwise
    
    Examples:
        >>> ct_eq_str("password", "password")
        True
        >>> ct_eq_str("password", "guess")
        False
        >>> ct_eq_str("password", None)
        False
    """
    if a is None or b is None:
        return False
    return ct_eq_bytes(a.encode("utf-8"), b.encode("utf-8"))


def ct_select(mask: bool, if_true: int, if_false: int) -> int:
    """
    Constant-time selection based on boolean mask.
    
    Returns if_true if mask is True, otherwise if_false.
    Uses bitwise operations instead of if/else to avoid branching on secrets.
    
    Note: This is best-effort in Python. The interpreter may still optimize
    or branch internally. For critical operations, prefer combining checks
    and deciding after full evaluation rather than early returns.
    
    Args:
        mask: Boolean condition (True or False)
        if_true: Value to return if mask is True
        if_false: Value to return if mask is False
    
    Returns:
        if_true if mask else if_false (via bitwise ops)
    
    Examples:
        >>> ct_select(True, 1, 0)
        1
        >>> ct_select(False, 1, 0)
        0
    """
    # Convert mask to 0 or -1 (all bits set)
    # True -> -1 (all bits 1), False -> 0 (all bits 0)
    m = -int(mask)
    # Bitwise select: (m & if_true) | (~m & if_false)
    return (m & if_true) | (~m & if_false)


def ct_memcmp(a: memoryview, b: memoryview) -> bool:
    """
    Constant-time comparison of two memoryviews.
    
    Useful for comparing buffer regions without copying. Uses hmac.compare_digest()
    on the underlying bytes.
    
    Args:
        a: First memoryview
        b: Second memoryview
    
    Returns:
        True if both memoryviews have equal contents, False otherwise
    
    Examples:
        >>> buf1 = bytearray(b"secret")
        >>> buf2 = bytearray(b"secret")
        >>> ct_memcmp(memoryview(buf1), memoryview(buf2))
        True
        >>> buf3 = bytearray(b"guess")
        >>> ct_memcmp(memoryview(buf1), memoryview(buf3))
        False
    """
    return hmac.compare_digest(bytes(a), bytes(b))


# Guardrail helpers for avoiding early returns

def ct_all_checks(*checks: bool) -> bool:
    """
    Evaluate all boolean checks and return True only if all are True.
    
    Unlike using 'and' operators or early returns, this ensures all checks
    are evaluated (not short-circuited) to avoid secret-dependent timing.
    
    Args:
        *checks: Variable number of boolean conditions
    
    Returns:
        True if all checks are True, False otherwise
    
    Examples:
        >>> ct_all_checks(True, True, True)
        True
        >>> ct_all_checks(True, False, True)
        False
    """
    # Force evaluation of all checks (no short-circuit)
    result = True
    for check in checks:
        result = result and check
    return result


def ct_any_check(*checks: bool) -> bool:
    """
    Evaluate all boolean checks and return True if any is True.
    
    Unlike using 'or' operators or early returns, this ensures all checks
    are evaluated (not short-circuited) to avoid secret-dependent timing.
    
    Args:
        *checks: Variable number of boolean conditions
    
    Returns:
        True if any check is True, False otherwise
    
    Examples:
        >>> ct_any_check(False, True, False)
        True
        >>> ct_any_check(False, False, False)
        False
    """
    # Force evaluation of all checks (no short-circuit)
    result = False
    for check in checks:
        result = result or check
    return result


__all__ = [
    "ct_eq_bytes",
    "ct_eq_str",
    "ct_select",
    "ct_memcmp",
    "ct_all_checks",
    "ct_any_check",
]
