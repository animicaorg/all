"""
coretx.types - Core Transaction Types
======================================

Canonical transaction model with strict typing and validation.
Single source of truth for transaction structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

__all__ = [
    "TxKind",
    "TxBody",
    "TxAuth",
    "TxEnvelope",
    "TxId",
]


class TxKind(IntEnum):
    """Transaction type identifier"""
    TRANSFER = 0
    DEPLOY = 1
    CALL = 2
    COINBASE = 3
    AICF_CLAIM = 4  # AICF credit claim transaction
    ENA_CALL = 5  # ENA inference call transaction (future)
    ENA_SUBMIT_RECEIPT = 6  # Phase 2: Submit compute receipt (anchor hash)
    AICF_CLAIM_PROVIDER_REWARDS = 7  # Phase 2: Provider claim accrued rewards
    AICF_GOVERNANCE_TOPUP = 8  # Governance top-up for AICF pool


@dataclass(frozen=True)
class TxBody:
    """
    Transaction body containing all transaction-specific data.
    
    This is the core data structure that gets signed.
    All fields are mandatory for deterministic encoding.
    """
    version: int
    chain_id: int
    nonce: int
    from_addr: bytes  # 32-byte address
    to_addr: bytes  # 32-byte address (can be zero for deploy)
    value: int  # wei
    fee: int  # wei (total gas * gas_price)
    gas_limit: int
    data: bytes  # contract calldata or deployment code
    memo: str  # optional user memo (max 256 bytes)
    timestamp: int  # unix timestamp (for replay protection window)
    kind: TxKind = TxKind.TRANSFER
    
    def __post_init__(self):
        """Validate fields"""
        # Type validation
        if not isinstance(self.version, int):
            raise TypeError(f"version must be int, got {type(self.version)}")
        if not isinstance(self.chain_id, int):
            raise TypeError(f"chain_id must be int, got {type(self.chain_id)}")
        if not isinstance(self.nonce, int):
            raise TypeError(f"nonce must be int, got {type(self.nonce)}")
        if not isinstance(self.from_addr, bytes):
            raise TypeError(f"from_addr must be bytes, got {type(self.from_addr)}")
        if not isinstance(self.to_addr, bytes):
            raise TypeError(f"to_addr must be bytes, got {type(self.to_addr)}")
        if not isinstance(self.value, int):
            raise TypeError(f"value must be int, got {type(self.value)}")
        if not isinstance(self.fee, int):
            raise TypeError(f"fee must be int, got {type(self.fee)}")
        if not isinstance(self.gas_limit, int):
            raise TypeError(f"gas_limit must be int, got {type(self.gas_limit)}")
        if not isinstance(self.data, bytes):
            raise TypeError(f"data must be bytes, got {type(self.data)}")
        if not isinstance(self.memo, str):
            raise TypeError(f"memo must be str, got {type(self.memo)}")
        if not isinstance(self.timestamp, int):
            raise TypeError(f"timestamp must be int, got {type(self.timestamp)}")
        
        # Range validation
        if self.version < 0:
            raise ValueError(f"version must be >= 0, got {self.version}")
        if self.chain_id < 0:
            raise ValueError(f"chain_id must be >= 0, got {self.chain_id}")
        if self.nonce < 0:
            raise ValueError(f"nonce must be >= 0, got {self.nonce}")
        if len(self.from_addr) != 32:
            raise ValueError(f"from_addr must be 32 bytes, got {len(self.from_addr)}")
        if len(self.to_addr) != 32:
            raise ValueError(f"to_addr must be 32 bytes, got {len(self.to_addr)}")
        if self.value < 0:
            raise ValueError(f"value must be >= 0, got {self.value}")
        if self.fee < 0:
            raise ValueError(f"fee must be >= 0, got {self.fee}")
        if self.gas_limit <= 0:
            raise ValueError(f"gas_limit must be > 0, got {self.gas_limit}")
        if len(self.memo.encode('utf-8')) > 256:
            raise ValueError(f"memo must be <= 256 bytes when UTF-8 encoded")
        if self.timestamp < 0:
            raise ValueError(f"timestamp must be >= 0, got {self.timestamp}")


@dataclass(frozen=True)
class TxAuth:
    """
    Transaction authentication (signature envelope).
    
    Contains the cryptographic proof that the sender authorized this transaction.
    """
    scheme_id: int  # Algorithm identifier (e.g., 1=dilithium3, 2=sphincs+)
    pubkey_bytes: bytes
    signature_bytes: bytes
    prehash_id: int  # Prehash algorithm (0=none, 1=sha3-256, 2=sha3-512)
    
    def __post_init__(self):
        """Validate fields"""
        if not isinstance(self.scheme_id, int):
            raise TypeError(f"scheme_id must be int, got {type(self.scheme_id)}")
        if not isinstance(self.pubkey_bytes, bytes):
            raise TypeError(f"pubkey_bytes must be bytes, got {type(self.pubkey_bytes)}")
        if not isinstance(self.signature_bytes, bytes):
            raise TypeError(f"signature_bytes must be bytes, got {type(self.signature_bytes)}")
        if not isinstance(self.prehash_id, int):
            raise TypeError(f"prehash_id must be int, got {type(self.prehash_id)}")
        
        if self.scheme_id < 0:
            raise ValueError(f"scheme_id must be >= 0, got {self.scheme_id}")
        if len(self.pubkey_bytes) == 0:
            raise ValueError("pubkey_bytes cannot be empty")
        if len(self.signature_bytes) == 0:
            raise ValueError("signature_bytes cannot be empty")
        if self.prehash_id < 0:
            raise ValueError(f"prehash_id must be >= 0, got {self.prehash_id}")


@dataclass(frozen=True)
class TxId:
    """
    Transaction identifier (32-byte hash).
    
    Computed deterministically from the complete envelope (body + auth).
    """
    bytes32: bytes
    
    def __post_init__(self):
        if not isinstance(self.bytes32, bytes):
            raise TypeError(f"bytes32 must be bytes, got {type(self.bytes32)}")
        if len(self.bytes32) != 32:
            raise ValueError(f"TxId must be exactly 32 bytes, got {len(self.bytes32)}")
    
    def hex(self) -> str:
        """Return hex representation with 0x prefix"""
        return "0x" + self.bytes32.hex()
    
    @classmethod
    def from_hex(cls, hex_str: str) -> TxId:
        """Create TxId from hex string (with or without 0x prefix)"""
        if hex_str.startswith("0x"):
            hex_str = hex_str[2:]
        return cls(bytes.fromhex(hex_str))
    
    def __str__(self) -> str:
        return self.hex()
    
    def __repr__(self) -> str:
        return f"TxId({self.hex()})"


@dataclass(frozen=True)
class TxEnvelope:
    """
    Complete signed transaction envelope.
    
    Contains both the transaction body and authentication proof.
    This is what gets transmitted over the network and stored in blocks.
    """
    body: TxBody
    auth: TxAuth
    txid: TxId
    
    def __post_init__(self):
        """Validate envelope"""
        if not isinstance(self.body, TxBody):
            raise TypeError(f"body must be TxBody, got {type(self.body)}")
        if not isinstance(self.auth, TxAuth):
            raise TypeError(f"auth must be TxAuth, got {type(self.auth)}")
        if not isinstance(self.txid, TxId):
            raise TypeError(f"txid must be TxId, got {type(self.txid)}")
