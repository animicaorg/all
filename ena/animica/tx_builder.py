"""
Transaction builder for Animica payments.

This module provides utilities to build payment transactions for ENA service.
It integrates with the animica CLI wallet system for signing.
"""

import logging
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

from .address import validate_address

logger = logging.getLogger(__name__)


class TransactionBuildError(Exception):
    """Raised when transaction building fails."""
    pass


def build_payment_transaction(
    from_address: str,
    to_address: str,
    value: int,
    nonce: int,
    chain_id: int,
    gas_limit: int = 21000,
    gas_price: int = 1000,
) -> Dict[str, Any]:
    """
    Build an unsigned payment transaction.
    
    Args:
        from_address: Sender address
        to_address: Recipient address
        value: Amount in base units
        nonce: Transaction nonce
        chain_id: Chain ID
        gas_limit: Gas limit (default: 21000)
        gas_price: Gas price in base units (default: 1000)
    
    Returns:
        Unsigned transaction dictionary
    
    Raises:
        TransactionBuildError: If validation fails
    """
    # Validate addresses
    if not validate_address(from_address):
        raise TransactionBuildError(f"Invalid from address: {from_address}")
    if not validate_address(to_address):
        raise TransactionBuildError(f"Invalid to address: {to_address}")
    
    # Validate amounts
    if value < 0:
        raise TransactionBuildError("Value must be non-negative")
    if nonce < 0:
        raise TransactionBuildError("Nonce must be non-negative")
    if gas_limit < 21000:
        raise TransactionBuildError("Gas limit must be at least 21000")
    
    tx = {
        "from": from_address,
        "to": to_address,
        "value": str(value),
        "nonce": nonce,
        "chainId": chain_id,
        "gasLimit": gas_limit,
        "gasPrice": str(gas_price),
        "data": "",
    }
    
    return tx


def sign_transaction_with_cli(
    tx: Dict[str, Any],
    from_address: str,
    rpc_url: Optional[str] = None,
) -> str:
    """
    Sign a transaction using animica CLI wallet.
    
    This shells out to `animica tx send` with appropriate flags.
    
    Args:
        tx: Transaction dictionary
        from_address: Sender address
        rpc_url: Optional RPC URL override
    
    Returns:
        Signed transaction hex string
    
    Raises:
        TransactionBuildError: If signing fails
    """
    # Build animica CLI command
    cmd = [
        "animica",
        "tx",
        "send",
        "--from", from_address,
        "--to", tx["to"],
        "--value", str(tx["value"]),
        "--nonce", str(tx["nonce"]),
        "--chain-id", str(tx["chainId"]),
        "--gas-limit", str(tx.get("gasLimit", 21000)),
        "--gas-price", str(tx.get("gasPrice", 1000)),
        "--json",
        "--dry-run",  # Don't actually send, just return signed tx
    ]
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode != 0:
            raise TransactionBuildError(
                f"CLI signing failed: {result.stderr}"
            )
        
        # Parse JSON output
        output = json.loads(result.stdout)
        signed_tx = output.get("signedTx")
        
        if not signed_tx:
            raise TransactionBuildError("No signed transaction in CLI output")
        
        return signed_tx
    
    except subprocess.TimeoutExpired:
        raise TransactionBuildError("CLI signing timed out")
    except json.JSONDecodeError as e:
        raise TransactionBuildError(f"Failed to parse CLI output: {e}")
    except Exception as e:
        raise TransactionBuildError(f"CLI signing error: {e}")


def estimate_transaction_fee(
    gas_limit: int = 21000,
    gas_price: int = 1000,
) -> int:
    """
    Estimate transaction fee.
    
    Args:
        gas_limit: Gas limit
        gas_price: Gas price in base units
    
    Returns:
        Estimated fee in base units
    """
    return gas_limit * gas_price


def format_amount(value: int, decimals: int = 9) -> str:
    """
    Format amount for display.
    
    Args:
        value: Amount in base units
        decimals: Number of decimals (default: 9 for ANM)
    
    Returns:
        Formatted string
    """
    divisor = 10 ** decimals
    whole = value // divisor
    fraction = value % divisor
    
    if fraction == 0:
        return f"{whole}"
    
    # Format with decimals, removing trailing zeros
    fraction_str = f"{fraction:0{decimals}d}".rstrip("0")
    return f"{whole}.{fraction_str}"


def parse_amount(amount_str: str, decimals: int = 9) -> int:
    """
    Parse amount string to base units.
    
    Args:
        amount_str: Amount as string (e.g., "1.5" or "1500000000")
        decimals: Number of decimals (default: 9 for ANM)
    
    Returns:
        Amount in base units
    
    Raises:
        ValueError: If amount is invalid
    """
    try:
        if "." in amount_str:
            # Parse decimal format
            parts = amount_str.split(".")
            whole = int(parts[0])
            fraction = parts[1] if len(parts) > 1 else "0"
            
            # Pad or truncate fraction to match decimals
            if len(fraction) > decimals:
                raise ValueError(f"Too many decimal places (max {decimals})")
            
            fraction = fraction.ljust(decimals, "0")
            return whole * (10 ** decimals) + int(fraction)
        else:
            # Parse as base units
            return int(amount_str)
    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid amount: {amount_str}") from e
