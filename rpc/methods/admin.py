from __future__ import annotations

"""
Admin RPC methods for development and testing.

These methods provide privileged operations that should only be available
in development or testing environments. They allow direct state manipulation
for testing purposes.

⚠️ WARNING: These methods bypass normal consensus and should never be
exposed on production networks.
"""

import logging
import os
import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

log = logging.getLogger("animica.rpc.admin")

# Environment variable to enable admin RPC methods
ADMIN_RPC_ENABLED_ENV = "ANIMICA_ADMIN_RPC_ENABLED"

# Optional helpers for address parsing
try:
    from pq.py.utils import bech32 as _bech32  # type: ignore
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _bech32 = None  # type: ignore


def _is_admin_rpc_enabled() -> bool:
    """Check if admin RPC methods are enabled via environment variable."""
    enabled = os.getenv(ADMIN_RPC_ENABLED_ENV, "").lower()
    return enabled in ("1", "true", "yes", "on")


def _require_admin_rpc() -> None:
    """Raise an error if admin RPC is not enabled."""
    if not _is_admin_rpc_enabled():
        raise rpc_errors.MethodNotFound(
            f"Admin RPC methods are disabled. Set {ADMIN_RPC_ENABLED_ENV}=1 to enable (dev/test only)."
        )


def _is_hex_addr(s: str) -> bool:
    """Check if a string is a valid hex address."""
    s = s.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        return False
    try:
        bytes.fromhex(s)
        return True
    except Exception:
        return False


def _validate_address(addr: t.Any) -> str:
    """Validate and normalize an address string."""
    if not isinstance(addr, str) or not addr:
        raise rpc_errors.InvalidParams("address must be a non-empty string")
    a = addr.strip()
    # Accept anim… bech32m, system:…, or raw hex (0x…)
    if a.lower().startswith("anim"):
        return a
    if a.lower().startswith("system:"):
        return a
    if _is_hex_addr(a):
        if not a.lower().startswith("0x"):
            a = "0x" + a
        return a
    raise rpc_errors.InvalidParams("address must be anim… (bech32m), system:… or 0x… (hex)")


def _parse_balance(balance: t.Any) -> int:
    """Parse and validate a balance value."""
    if isinstance(balance, int):
        if balance < 0:
            raise rpc_errors.InvalidParams("balance must be non-negative")
        return balance
    
    if isinstance(balance, str):
        # Handle hex string
        if balance.startswith("0x"):
            try:
                val = int(balance, 16)
                if val < 0:
                    raise rpc_errors.InvalidParams("balance must be non-negative")
                return val
            except ValueError as e:
                raise rpc_errors.InvalidParams(f"invalid hex balance: {e}") from e
        # Handle decimal string
        try:
            val = int(balance)
            if val < 0:
                raise rpc_errors.InvalidParams("balance must be non-negative")
            return val
        except ValueError as e:
            raise rpc_errors.InvalidParams(f"invalid balance: {e}") from e
    
    raise rpc_errors.InvalidParams(f"balance must be int or hex string, got {type(balance).__name__}")


def _to_account_key_bytes(addr: str) -> bytes | None:
    """Convert address to account key bytes for state DB access."""
    if addr.lower().startswith("anim") and _bech32 is not None:
        try:
            hrp, data = _bech32.decode(addr)
            if hrp and data:
                payload = bytes(data)
                if len(payload) == 34:
                    return payload[2:34]
                return payload
        except Exception:
            return None
    
    # hex
    if _is_hex_addr(addr):
        s = addr.lower()
        if s.startswith("0x"):
            s = s[2:]
        try:
            addr_bytes = bytes.fromhex(s)
            # Pad to 32 bytes if needed
            if len(addr_bytes) < 32:
                addr_bytes = b"\x00" * (32 - len(addr_bytes)) + addr_bytes
            # Truncate to 32 bytes if too long
            return addr_bytes[:32]
        except Exception:
            return None
    
    return None


@method("admin.setBalance", desc="Set account balance (dev/test only)")
async def set_balance(address: str, balance: t.Union[str, int]) -> t.Dict[str, t.Any]:
    """
    Set an account's balance directly (bypasses consensus).
    
    ⚠️ WARNING: This method is for development and testing only.
    It directly manipulates state without going through consensus.
    
    Args:
        address: Account address (anim1…, system:…, or 0x…)
        balance: New balance in smallest unit (nANM) as int or hex string
    
    Returns:
        Dict with success status and updated balance
        
    Example:
        >>> await set_balance("anim1...", 1000000000000)  # 1000 ANM
        {"success": True, "address": "anim1...", "balance": "1000000000000"}
    
    Raises:
        MethodNotFound: If admin RPC is not enabled
        InvalidParams: If address or balance is invalid
        InternalError: If state update fails
    """
    # Check if admin RPC is enabled
    _require_admin_rpc()
    
    # Validate inputs
    addr = _validate_address(address)
    bal = _parse_balance(balance)
    
    log.info(f"Admin RPC: Setting balance for {addr} to {bal} nANM")
    
    # Try to use state DB via deps
    try:
        state_db = deps.state_db()
        if state_db is None:
            raise rpc_errors.InternalError("State database not available")
        
        # Convert address to key bytes
        key = _to_account_key_bytes(addr)
        if key is None:
            raise rpc_errors.InvalidParams(f"Failed to convert address to key bytes: {addr}")
        
        # The state DB should have a method to set balance
        # Check for the specific method that exists in the codebase
        if hasattr(state_db, 'set_balance'):
            # Direct method (preferred)
            state_db.set_balance(key, bal)
            log.info(f"Balance set via state_db.set_balance: {addr} = {bal}")
        elif hasattr(state_db, 'set_account_balance'):
            # Alternative method name
            state_db.set_account_balance(key, bal)
            log.info(f"Balance set via state_db.set_account_balance: {addr} = {bal}")
        else:
            # State DB doesn't have a direct balance setter
            # This is expected - most implementations don't allow direct manipulation
            raise rpc_errors.InternalError(
                "State DB does not support direct balance updates. "
                "This is a limitation of the current implementation. "
                "Consider using genesis initialization or transaction-based updates."
            )
        
        return {
            "success": True,
            "address": addr,
            "balance": str(bal),
            "method": "state_db"
        }
    
    except rpc_errors.RpcError:
        # Re-raise RPC errors as-is
        raise
    except Exception as e:
        log.error(f"Failed to set balance for {addr}: {e}", exc_info=True)
        raise rpc_errors.InternalError(
            f"Failed to set balance: {e}. "
            "Admin RPC balance updates may not be fully implemented yet."
        ) from e


@method("admin.getInfo", desc="Get admin RPC information")
async def get_info() -> t.Dict[str, t.Any]:
    """
    Get information about admin RPC availability and settings.
    
    Returns:
        Dict with admin RPC status and configuration
    """
    return {
        "enabled": _is_admin_rpc_enabled(),
        "env_var": ADMIN_RPC_ENABLED_ENV,
        "warning": "Admin RPC methods bypass consensus and should only be used in dev/test environments"
    }
