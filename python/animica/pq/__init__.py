"""Post-quantum cryptography API for Animica.

This module provides a unified API for post-quantum cryptographic operations:
- KEM (Key Encapsulation Mechanism): ML-KEM-768 (Kyber768)
- Digital Signatures: ML-DSA-65 (Dilithium3)

Uses pure-Python implementations by default (ANIMICA_PQ_MODE=pure).
Can be disabled with ANIMICA_PQ_MODE=disabled for testing fallback behavior.

No compiled dependencies (no liboqs/oqs required).
"""

from __future__ import annotations

import os
from typing import Tuple

# Algorithm identifiers
ALG_ID_ML_KEM_768 = 0x2001
ALG_ID_ML_DSA_65 = 0x2002
ALG_ID_SPHINCS_SHAKE_128S = 0x1002  # Legacy, for compatibility

ALG_NAME_KEM = "ml_kem_768"
ALG_NAME_SIG = "ml_dsa_65"
ALG_NAME = "ml_dsa_65"  # Default signature algorithm


def is_available() -> bool:
    """Check if PQ cryptography is available.
    
    Returns:
        True if PQ is available (mode != 'disabled'), False otherwise
    """
    mode = os.environ.get("ANIMICA_PQ_MODE", "pure").lower()
    return mode != "disabled"


def get_mode() -> str:
    """Get current PQ mode.
    
    Returns:
        'pure' (pure-Python implementation) or 'disabled'
    """
    return os.environ.get("ANIMICA_PQ_MODE", "pure").lower()


# ============================================================================
# KEM (Key Encapsulation Mechanism) - ML-KEM-768
# ============================================================================

def kem_keygen(seed: bytes = None) -> Tuple[bytes, bytes]:
    """Generate ML-KEM-768 keypair.
    
    Args:
        seed: Optional 64-byte seed for deterministic generation (testing only).
              If None, uses os.urandom().
    
    Returns:
        (encapsulation_key, decapsulation_key) tuple
        - encapsulation_key (public key): 1184 bytes
        - decapsulation_key (secret key): 2400 bytes
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.kyber_py import Kyber768
    sk, pk = Kyber768.keygen(seed)
    return pk, sk  # Return (public, secret) matching conventional order


def kem_encaps(encapsulation_key: bytes, seed: bytes = None) -> Tuple[bytes, bytes]:
    """Encapsulate to generate shared secret.
    
    Args:
        encapsulation_key: Public key (1184 bytes)
        seed: Optional 32-byte seed for deterministic encapsulation (testing only).
              If None, uses os.urandom().
    
    Returns:
        (shared_secret, ciphertext) tuple
        - shared_secret: 32 bytes
        - ciphertext: 1088 bytes
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.kyber_py import Kyber768
    ct, ss = Kyber768.encaps(encapsulation_key, seed)
    return ss, ct  # Return (shared_secret, ciphertext)


def kem_decaps(decapsulation_key: bytes, ciphertext: bytes) -> bytes:
    """Decapsulate to recover shared secret.
    
    Args:
        decapsulation_key: Secret key (2400 bytes)
        ciphertext: Ciphertext (1088 bytes)
    
    Returns:
        shared_secret (32 bytes)
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.kyber_py import Kyber768
    return Kyber768.decaps(decapsulation_key, ciphertext)


# ============================================================================
# Digital Signatures - ML-DSA-65
# ============================================================================

def sig_keygen(seed: bytes = None) -> Tuple[bytes, bytes]:
    """Generate ML-DSA-65 keypair.
    
    Args:
        seed: Optional 32-byte seed for deterministic generation (testing only).
              If None, uses os.urandom().
    
    Returns:
        (public_key, secret_key) tuple
        - public_key: 1952 bytes
        - secret_key: 4000 bytes
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.dilithium_py import Dilithium3
    sk, pk = Dilithium3.keygen(seed)
    return pk, sk  # Return (public, secret)


def sig_sign(secret_key: bytes, message: bytes, seed: bytes = None) -> bytes:
    """Sign a message with ML-DSA-65.
    
    Args:
        secret_key: Secret key (4000 bytes)
        message: Message to sign
        seed: Optional randomness for signing (hedged by default)
    
    Returns:
        signature (3293 bytes)
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.dilithium_py import Dilithium3
    return Dilithium3.sign(secret_key, message, seed)


def sig_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a signature with ML-DSA-65.
    
    Args:
        public_key: Public key (1952 bytes)
        message: Message that was signed
        signature: Signature to verify (3293 bytes)
    
    Returns:
        True if signature is valid, False otherwise
    
    Raises:
        RuntimeError: If PQ is disabled
    """
    if not is_available():
        raise RuntimeError("PQ cryptography is disabled (ANIMICA_PQ_MODE=disabled)")
    
    from animica._vendor.dilithium_py import Dilithium3
    return Dilithium3.verify(public_key, message, signature)


# ============================================================================
# Legacy compatibility (for existing code using get_backend pattern)
# ============================================================================

class PQBackend:
    """Compatibility wrapper for existing code."""
    
    def keygen(self) -> Tuple[bytes, bytes]:
        """Generate keypair. Returns (public_key, secret_key)."""
        return sig_keygen()
    
    def sign(self, secret_key: bytes, message: bytes) -> bytes:
        """Sign message."""
        return sig_sign(secret_key, message)
    
    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Verify signature."""
        return sig_verify(public_key, message, signature)


def get_backend() -> Tuple[PQBackend, str]:
    """Get PQ backend for compatibility with existing code.
    
    Returns:
        (backend, label) tuple
    """
    mode = get_mode()
    if mode == "disabled":
        # Return a dummy backend that raises on use
        class DisabledBackend:
            def keygen(self):
                raise RuntimeError("PQ is disabled")
            def sign(self, *args):
                raise RuntimeError("PQ is disabled")
            def verify(self, *args):
                raise RuntimeError("PQ is disabled")
        return DisabledBackend(), "disabled"  # type: ignore
    
    return PQBackend(), "pure"


__all__ = [
    # Algorithm IDs
    "ALG_ID_ML_KEM_768",
    "ALG_ID_ML_DSA_65",
    "ALG_ID_SPHINCS_SHAKE_128S",
    "ALG_NAME_KEM",
    "ALG_NAME_SIG",
    "ALG_NAME",
    # Status
    "is_available",
    "get_mode",
    # KEM API
    "kem_keygen",
    "kem_encaps",
    "kem_decaps",
    # Signature API
    "sig_keygen",
    "sig_sign",
    "sig_verify",
    # Legacy compatibility
    "PQBackend",
    "get_backend",
]
