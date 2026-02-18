"""
Address validation utilities for Animica blockchain.
"""

import re
from typing import Optional


def validate_address(address: str) -> bool:
    """
    Validate Animica bech32 address format.
    
    Expected format: anim1 + 39 characters (lowercase alphanumeric)
    Total length: 44 characters
    
    Args:
        address: Address to validate
    
    Returns:
        True if valid, False otherwise
    """
    if not address:
        return False
    
    # Check prefix
    if not address.startswith("anim1"):
        return False
    
    # Check length (anim1 = 5 chars + 39 chars = 44 total)
    if len(address) != 44:
        return False
    
    # Check characters (bech32 uses lowercase alphanumeric except 1, b, i, o)
    # For simplicity, we'll allow any lowercase alphanumeric
    pattern = r'^anim1[a-z0-9]{39}$'
    return bool(re.match(pattern, address))


def validate_tx_hash(tx_hash: str) -> bool:
    """
    Validate transaction hash format.
    
    Expected format: 0x followed by 64 hexadecimal characters
    
    Args:
        tx_hash: Transaction hash to validate
    
    Returns:
        True if valid, False otherwise
    """
    if not tx_hash:
        return False
    
    if not tx_hash.startswith("0x"):
        return False
    
    if len(tx_hash) != 66:  # 0x + 64 hex chars
        return False
    
    # Check hex characters
    try:
        int(tx_hash[2:], 16)
        return True
    except ValueError:
        return False


def normalize_address(address: str) -> str:
    """
    Normalize address to lowercase.
    
    Args:
        address: Address to normalize
    
    Returns:
        Normalized address
    """
    return address.lower()


def normalize_tx_hash(tx_hash: str) -> str:
    """
    Normalize transaction hash to lowercase.
    
    Args:
        tx_hash: Transaction hash to normalize
    
    Returns:
        Normalized hash
    """
    return tx_hash.lower()
