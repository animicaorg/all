"""
omni_sdk.wallet
================

Convenience exports for wallet helpers:

- Mnemonic utilities (create/import/validate, seed derivation).
- File keystore (AES-GCM encrypted, lock/unlock).
- PQ signers (Dilithium3 / SPHINCS+), unified Signer interface.
"""

from .mnemonic import generate_mnemonic, mnemonic_to_seed, validate_mnemonic
from .keystore import KeyStore, EncryptedKey
from .signer import Signer, SignResult, Dilithium3Signer, SphincsPlusSigner

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
