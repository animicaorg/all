"""Wallet balance backup and restore for node reset operations.

This module provides functionality to preserve mining rewards and other balances
when resetting a node. It exports balances before the reset and can restore them
after the node is reinitialized with a fresh genesis state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


def _get_balance_backup_path(data_dir: Path) -> Path:
    """Get the path for the balance backup file.
    
    Args:
        data_dir: Network data directory (e.g., ~/.animica/chain-1337/)
    
    Returns:
        Path to balance backup file
    """
    base_dir = data_dir.parent  # Go up to ~/.animica/
    return base_dir / f"{data_dir.name}_balances_backup.json"


async def _rpc_call(rpc_url: str, method: str, params: List[Any], timeout: float = 10.0) -> Any:
    """Make an RPC call with proper error handling.
    
    Args:
        rpc_url: RPC endpoint URL
        method: RPC method name
        params: Method parameters
        timeout: Request timeout in seconds
    
    Returns:
        RPC result
    
    Raises:
        RuntimeError: If RPC call fails
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(rpc_url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            
            return data.get("result")
    except Exception as e:
        logger.debug(f"RPC call to {method} failed: {e}")
        raise RuntimeError(f"RPC {method} failed: {e}") from e


def _load_wallet_file(wallet_path: Path) -> List[Dict[str, Any]]:
    """Load wallet entries from wallets.json file.
    
    Args:
        wallet_path: Path to wallets.json
    
    Returns:
        List of wallet entries
    """
    if not wallet_path.exists():
        return []
    
    try:
        data = json.loads(wallet_path.read_text(encoding="utf-8"))
        return data.get("wallets", [])
    except Exception as e:
        logger.warning(f"Failed to load wallet file {wallet_path}: {e}")
        return []


def _address_to_hex(address: str) -> str:
    """Convert an address to 0x-prefixed hex format.
    
    Args:
        address: Address (bech32 anim1... or hex)
    
    Returns:
        0x-prefixed hex address (32 bytes = 64 hex chars)
    """
    # If already hex, normalize it
    if address.startswith("0x") or all(c in "0123456789abcdefABCDEF" for c in address):
        hex_addr = address[2:] if address.startswith("0x") else address
        # Pad or truncate to 32 bytes (64 hex chars)
        if len(hex_addr) < 64:
            hex_addr = hex_addr.rjust(64, "0")
        elif len(hex_addr) > 64:
            hex_addr = hex_addr[-64:]
        return f"0x{hex_addr}"
    
    # Try to decode bech32 address
    if address.startswith("anim1"):
        try:
            from pq.py.address import decode_address
            decoded = decode_address(address)
            # Get 32-byte digest
            addr_bytes = bytes(decoded.digest)[:32].ljust(32, b"\x00")
            return "0x" + addr_bytes.hex()
        except Exception as e:
            logger.warning(f"Failed to decode bech32 address {address}: {e}")
    
    # Fallback: return zero address
    return "0x" + ("00" * 32)


async def export_wallet_balances(
    wallet_path: Path,
    data_dir: Path,
    rpc_url: str,
    *,
    timeout: float = 10.0,
    quiet: bool = False,
) -> Tuple[Path, int, int]:
    """Export balances for all addresses in wallets.json.
    
    Args:
        wallet_path: Path to wallets.json file
        data_dir: Network data directory
        rpc_url: RPC endpoint to query balances
        timeout: RPC timeout in seconds
        quiet: Suppress output messages
    
    Returns:
        Tuple of (backup_file_path, total_addresses, non_zero_balances)
    
    Raises:
        RuntimeError: If node is not accessible or export fails
    """
    # Load wallet entries
    wallets = _load_wallet_file(wallet_path)
    if not wallets:
        if not quiet:
            logger.info("No wallets found in wallet file; skipping balance export")
        return (_get_balance_backup_path(data_dir), 0, 0)
    
    # Query balances for each address
    balances = []
    non_zero_count = 0
    
    for wallet in wallets:
        address = wallet.get("address")
        label = wallet.get("label", "unlabeled")
        
        if not address:
            continue
        
        # Convert to hex format for RPC
        hex_address = _address_to_hex(address)
        
        try:
            # Try multiple balance query methods
            balance = None
            for method in ["state.getBalance", "state_getBalance", "eth_getBalance"]:
                try:
                    balance = await _rpc_call(rpc_url, method, [hex_address], timeout=timeout)
                    if balance is not None:
                        break
                except Exception:
                    continue
            
            if balance is None:
                logger.warning(f"Could not query balance for {label} ({address})")
                balance = 0
            
            # Parse balance (may be hex string or int)
            if isinstance(balance, str):
                if balance.startswith("0x"):
                    balance = int(balance, 16)
                else:
                    balance = int(balance)
            else:
                balance = int(balance)
            
            if balance > 0:
                non_zero_count += 1
            
            balances.append({
                "label": label,
                "address": address,
                "hex_address": hex_address,
                "balance": balance,
            })
            
            if not quiet and balance > 0:
                logger.info(f"Exported balance for {label}: {balance} nANM")
        
        except Exception as e:
            logger.warning(f"Failed to query balance for {label} ({address}): {e}")
            balances.append({
                "label": label,
                "address": address,
                "hex_address": hex_address,
                "balance": 0,
                "error": str(e),
            })
    
    # Write backup file
    backup_path = _get_balance_backup_path(data_dir)
    backup_data = {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "rpc_url": rpc_url,
        "balances": balances,
    }
    
    backup_path.write_text(json.dumps(backup_data, indent=2), encoding="utf-8")
    
    if not quiet:
        logger.info(f"Exported {len(balances)} addresses ({non_zero_count} with balance > 0)")
        logger.info(f"Balance backup saved to: {backup_path}")
    
    return (backup_path, len(balances), non_zero_count)


async def restore_wallet_balances(
    data_dir: Path,
    rpc_url: str,
    *,
    backup_file: Optional[Path] = None,
    timeout: float = 10.0,
    quiet: bool = False,
) -> Tuple[int, int]:
    """Restore balances from a backup file by crediting them via RPC.
    
    This function assumes the node has been reset and is running with a fresh
    genesis state. It will credit balances to addresses using the admin.setBalance
    RPC method that directly manipulates state.
    
    ⚠️ IMPORTANT: This requires the ANIMICA_ADMIN_RPC_ENABLED=1 environment
    variable to be set when starting the node. This is a security feature to
    prevent accidental use in production.
    
    Args:
        data_dir: Network data directory
        rpc_url: RPC endpoint to credit balances
        backup_file: Optional explicit backup file path
        timeout: RPC timeout in seconds
        quiet: Suppress output messages
    
    Returns:
        Tuple of (total_restored, restore_failures)
    
    Raises:
        RuntimeError: If backup file not found or restore fails
    """
    # Determine backup file path
    if backup_file is None:
        backup_file = _get_balance_backup_path(data_dir)
    
    if not backup_file.exists():
        raise RuntimeError(f"Balance backup file not found: {backup_file}")
    
    # Load backup data
    try:
        backup_data = json.loads(backup_file.read_text(encoding="utf-8"))
        balances = backup_data.get("balances", [])
    except Exception as e:
        raise RuntimeError(f"Failed to load backup file {backup_file}: {e}") from e
    
    if not balances:
        if not quiet:
            logger.info("No balances to restore")
        return (0, 0)
    
    # Filter to only addresses with non-zero balance
    to_restore = [b for b in balances if b.get("balance", 0) > 0]
    
    if not to_restore:
        if not quiet:
            logger.info("No non-zero balances to restore")
        return (0, 0)
    
    if not quiet:
        logger.info(f"Restoring balances for {len(to_restore)} addresses...")
    
    # Restore balances using admin.setBalance RPC method
    restored = 0
    failed = 0
    errors = []
    
    for entry in to_restore:
        label = entry.get("label", "unlabeled")
        address = entry.get("address")
        hex_address = entry.get("hex_address")
        balance = entry.get("balance", 0)
        
        # Prefer original address format, fallback to hex
        addr_to_use = address or hex_address
        if not addr_to_use:
            logger.warning(f"Skipping entry with no address: {label}")
            failed += 1
            continue
        
        try:
            # Call admin.setBalance RPC method
            result = await _rpc_call(
                rpc_url,
                "admin.setBalance",
                [addr_to_use, balance],
                timeout=timeout
            )
            
            if result and result.get("success"):
                restored += 1
                if not quiet:
                    # Format balance in ANM (divide by 1e9)
                    balance_anm = balance / 1_000_000_000
                    logger.info(f"✓ Restored {label}: {balance_anm:.9f} ANM")
            else:
                failed += 1
                error_msg = result.get("error", "unknown error") if result else "no response"
                logger.warning(f"✗ Failed to restore {label}: {error_msg}")
                errors.append(f"{label}: {error_msg}")
        
        except RuntimeError as e:
            error_str = str(e)
            # Check if this is an "admin RPC disabled" error
            if "disabled" in error_str.lower() or "not found" in error_str.lower():
                # Admin RPC is not enabled - provide helpful error
                raise RuntimeError(
                    "Admin RPC is not enabled. To restore balances, restart the node with:\n"
                    "  ANIMICA_ADMIN_RPC_ENABLED=1 animica node up\n"
                    "Then run: animica balance restore"
                ) from e
            failed += 1
            logger.warning(f"✗ Failed to restore {label}: {e}")
            errors.append(f"{label}: {error_str}")
        
        except Exception as e:
            failed += 1
            logger.warning(f"✗ Failed to restore {label}: {e}")
            errors.append(f"{label}: {str(e)}")
    
    if not quiet:
        logger.info(f"\nRestore summary: {restored} succeeded, {failed} failed")
        if errors and len(errors) <= 5:
            logger.info("Errors:")
            for err in errors:
                logger.info(f"  - {err}")
        elif errors:
            logger.info(f"Errors: {len(errors)} total (showing first 5)")
            for err in errors[:5]:
                logger.info(f"  - {err}")
    
    return (restored, failed)


def export_wallet_balances_sync(
    wallet_path: Path,
    data_dir: Path,
    rpc_url: str,
    *,
    timeout: float = 10.0,
    quiet: bool = False,
) -> Tuple[Path, int, int]:
    """Synchronous wrapper for export_wallet_balances."""
    return asyncio.run(
        export_wallet_balances(
            wallet_path, data_dir, rpc_url, timeout=timeout, quiet=quiet
        )
    )


def restore_wallet_balances_sync(
    data_dir: Path,
    rpc_url: str,
    *,
    backup_file: Optional[Path] = None,
    timeout: float = 10.0,
    quiet: bool = False,
) -> Tuple[int, int]:
    """Synchronous wrapper for restore_wallet_balances."""
    return asyncio.run(
        restore_wallet_balances(
            data_dir, rpc_url, backup_file=backup_file, timeout=timeout, quiet=quiet
        )
    )
