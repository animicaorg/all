"""
omni_sdk.wallet
================

Convenience exports for wallet helpers:

- Mnemonic utilities (create/import/validate, seed derivation).
- File keystore (AES-GCM encrypted, lock/unlock).
- PQ signers (Dilithium3 / SPHINCS+), unified Signer interface.
"""

# Gracefully handle missing components
try:
    from .keystore import KeyStore
    EncryptedKey = None  # Not yet implemented
except Exception:
    KeyStore = None  # type: ignore
    EncryptedKey = None  # type: ignore

try:
    from .mnemonic import generate_mnemonic, mnemonic_to_seed, validate_mnemonic
except Exception:
    generate_mnemonic = None  # type: ignore
    mnemonic_to_seed = None  # type: ignore
    validate_mnemonic = None  # type: ignore

try:
    from .signer import Dilithium3Signer, Signer, SignResult, SphincsPlusSigner
except Exception:
    Dilithium3Signer = None  # type: ignore
    Signer = None  # type: ignore
    SignResult = None  # type: ignore
    SphincsPlusSigner = None  # type: ignore

__all__ = [
    # mnemonic
    "generate_mnemonic",
    "mnemonic_to_seed",
    "validate_mnemonic",
    # keystore
    "KeyStore",
    "EncryptedKey",
    # signers
    "Signer",
    "SignResult",
    "Dilithium3Signer",
    "SphincsPlusSigner",
]
