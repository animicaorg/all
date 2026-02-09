"""
coretx.crypto - PQ Cryptography Registry
=========================================

Registry of post-quantum signature schemes with typed verification results.
Supports dilithium3, sphincs+, and other PQ algorithms.

All verification functions:
- Return typed VerifyResult (never raise exceptions)
- Log only fingerprints (never full keys/signatures)
- Provide diagnostic context on failure
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable, Optional, Protocol, Tuple

from .errors import VerifyResult

__all__ = [
    "SCHEME_DILITHIUM3",
    "SCHEME_SPHINCS_SHAKE_128S",
    "SCHEME_SPHINCS_SHAKE_128F",
    "SCHEME_SPHINCS_SHAKE_256S",
    "SignFunc",
    "VerifyFunc",
    "SchemeInfo",
    "register_scheme",
    "get_scheme",
    "list_schemes",
    "verify_signature",
    "pubkey_fingerprint",
]

log = logging.getLogger(__name__)

# Scheme identifiers (stable integers)
SCHEME_DILITHIUM3 = 1
SCHEME_SPHINCS_SHAKE_128S = 2
SCHEME_SPHINCS_SHAKE_128F = 3
SCHEME_SPHINCS_SHAKE_256S = 4


class SignFunc(Protocol):
    """Signature function protocol"""
    def __call__(self, message: bytes, secret_key: bytes) -> bytes:
        """Sign a message with a secret key, return signature bytes"""
        ...


class VerifyFunc(Protocol):
    """Verification function protocol"""
    def __call__(self, message: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verify a signature, return True if valid"""
        ...


class SchemeInfo:
    """Information about a signature scheme"""
    def __init__(
        self,
        scheme_id: int,
        name: str,
        sign_func: Optional[SignFunc],
        verify_func: VerifyFunc,
        pubkey_len: Tuple[int, int],  # (min, max) in bytes
        sig_len: Tuple[int, int],  # (min, max) in bytes
    ):
        self.scheme_id = scheme_id
        self.name = name
        self.sign_func = sign_func
        self.verify_func = verify_func
        self.pubkey_len = pubkey_len
        self.sig_len = sig_len


# Global registry
_SCHEMES: dict[int, SchemeInfo] = {}


def register_scheme(info: SchemeInfo) -> None:
    """Register a signature scheme"""
    _SCHEMES[info.scheme_id] = info
    log.info(f"Registered signature scheme: {info.name} (id={info.scheme_id})")


def get_scheme(scheme_id: int) -> Optional[SchemeInfo]:
    """Get scheme info by ID"""
    return _SCHEMES.get(scheme_id)


def list_schemes() -> list[SchemeInfo]:
    """List all registered schemes"""
    return list(_SCHEMES.values())


def pubkey_fingerprint(pubkey: bytes) -> str:
    """
    Compute a short fingerprint of a public key for logging.
    Never log full pubkeys (they're large and sensitive).
    """
    h = hashlib.sha3_256(pubkey).digest()
    return h[:8].hex()


def verify_signature(
    scheme_id: int,
    message: bytes,
    signature: bytes,
    public_key: bytes,
) -> VerifyResult:
    """
    Verify a signature using the registered scheme.
    
    Returns:
        VerifyResult with success/failure and diagnostic context
    
    Never raises exceptions - all failures are captured in VerifyResult.
    """
    # Get scheme
    scheme = get_scheme(scheme_id)
    if scheme is None:
        return VerifyResult.failure(
            "scheme_unsupported",
            scheme_id=scheme_id,
            available_schemes=list(_SCHEMES.keys()),
        )
    
    # Check public key length
    pubkey_min, pubkey_max = scheme.pubkey_len
    if not (pubkey_min <= len(public_key) <= pubkey_max):
        return VerifyResult.failure(
            "invalid_pubkey_length",
            scheme_id=scheme_id,
            expected_range=(pubkey_min, pubkey_max),
            got=len(public_key),
            pubkey_fp=pubkey_fingerprint(public_key),
        )
    
    # Check signature length
    sig_min, sig_max = scheme.sig_len
    if not (sig_min <= len(signature) <= sig_max):
        return VerifyResult.failure(
            "invalid_signature_length",
            scheme_id=scheme_id,
            expected_range=(sig_min, sig_max),
            got=len(signature),
            pubkey_fp=pubkey_fingerprint(public_key),
        )
    
    # Verify signature
    try:
        valid = scheme.verify_func(message, signature, public_key)
        if valid:
            return VerifyResult.success()
        else:
            return VerifyResult.failure(
                "signature_invalid",
                scheme_id=scheme_id,
                scheme_name=scheme.name,
                pubkey_fp=pubkey_fingerprint(public_key),
            )
    except Exception as e:
        log.warning(
            f"Signature verification raised exception: {type(e).__name__}: {e}",
            extra={"scheme_id": scheme_id, "pubkey_fp": pubkey_fingerprint(public_key)},
        )
        return VerifyResult.failure(
            "verify_exception",
            scheme_id=scheme_id,
            scheme_name=scheme.name,
            error_class=type(e).__name__,
            error_message=str(e),
            pubkey_fp=pubkey_fingerprint(public_key),
        )


# ============================================================================
# Bootstrap: Register available PQ schemes
# ============================================================================

def _bootstrap_schemes():
    """Register all available PQ signature schemes"""
    
    # Try to load dilithium3
    try:
        from pq.py import dilithium3_sign, dilithium3_verify
        register_scheme(SchemeInfo(
            scheme_id=SCHEME_DILITHIUM3,
            name="dilithium3",
            sign_func=dilithium3_sign,
            verify_func=dilithium3_verify,
            pubkey_len=(1952, 1952),  # Dilithium3 public key is 1952 bytes
            sig_len=(3293, 3293),  # Dilithium3 signature is 3293 bytes
        ))
    except ImportError:
        log.warning("dilithium3 not available")
    
    # Try to load sphincs+ variants
    try:
        from pq.py import sphincs_shake_128s_sign, sphincs_shake_128s_verify
        register_scheme(SchemeInfo(
            scheme_id=SCHEME_SPHINCS_SHAKE_128S,
            name="sphincs_shake_128s",
            sign_func=sphincs_shake_128s_sign,
            verify_func=sphincs_shake_128s_verify,
            pubkey_len=(32, 32),
            sig_len=(7856, 7856),
        ))
    except ImportError:
        log.debug("sphincs_shake_128s not available")
    
    try:
        from pq.py import sphincs_shake_128f_sign, sphincs_shake_128f_verify
        register_scheme(SchemeInfo(
            scheme_id=SCHEME_SPHINCS_SHAKE_128F,
            name="sphincs_shake_128f",
            sign_func=sphincs_shake_128f_sign,
            verify_func=sphincs_shake_128f_verify,
            pubkey_len=(32, 32),
            sig_len=(17088, 17088),
        ))
    except ImportError:
        log.debug("sphincs_shake_128f not available")
    
    try:
        from pq.py import sphincs_shake_256s_sign, sphincs_shake_256s_verify
        register_scheme(SchemeInfo(
            scheme_id=SCHEME_SPHINCS_SHAKE_256S,
            name="sphincs_shake_256s",
            sign_func=sphincs_shake_256s_sign,
            verify_func=sphincs_shake_256s_verify,
            pubkey_len=(64, 64),
            sig_len=(29792, 29792),
        ))
    except ImportError:
        log.debug("sphincs_shake_256s not available")


# Bootstrap on import
_bootstrap_schemes()
