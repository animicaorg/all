from __future__ import annotations

"""
RPC faucet methods (devnet/testnet-only)
=========================================

Provides a simple, unlimited-supply faucet for non-mainnet environments.

Methods:
  - faucet.request(address, amount?) -> { address, amount, balance }

Security:
  - ONLY available on non-mainnet chains (chainId != 1)
  - Returns clear error on mainnet
  - No rate limits (testnet/devnet simplicity)
  - Direct state DB credit (no block production required)

Usage:
  # Request default amount (500,000,000 ANM = 500000000000000000 base units)
  {"jsonrpc":"2.0","method":"faucet.request","params":{"address":"anim1..."},"id":1}
  
  # Request custom amount
  {"jsonrpc":"2.0","method":"faucet.request","params":{"address":"anim1...","amount":"1000000000000000"},"id":1}
"""

import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

# Optional helpers (be tolerant during bring-up)
try:
    from pq.py.utils import bech32 as _bech32  # type: ignore
except Exception:  # pragma: no cover
    _bech32 = None  # type: ignore


# ——— Constants ———

# Default faucet amount: 500,000,000 ANM
# ANM has 9 decimals, so 500,000,000 ANM = 500,000,000 * 10^9 base units
DEFAULT_FAUCET_AMOUNT = 500_000_000_000_000_000  # 500 million ANM in base units


# ——— Utilities ———


def _is_hex_addr(s: str) -> bool:
    """Check if string is a valid hex address format."""
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
    """Validate and normalize address (bech32m or hex)."""
    if not isinstance(addr, str) or not addr:
        raise rpc_errors.InvalidParams("address must be a non-empty string")
    a = addr.strip()
    # Accept anim… bech32m or raw hex (0x…)
    if a.lower().startswith("anim"):
        # Fast shape check; full decode only if needed in fallback path
        return a
    if _is_hex_addr(a):
        if not a.lower().startswith("0x"):
            a = "0x" + a
        return a
    raise rpc_errors.InvalidParams("address must be anim… (bech32m) or 0x… (hex)")


def _to_account_key_bytes(addr: str) -> bytes | None:
    """
    Best-effort conversion to the canonical account key (bytes) for direct StateDB access.
    We prefer to let deps.state_service handle formats; this is only used as a fallback.
    """
    if addr.lower().startswith("anim") and _bech32 is not None:
        try:
            hrp, data = _bech32.decode(addr)
            if hrp and data:
                return bytes(
                    data
                )  # payload = (alg_id || sha3_256(pubkey)) per pq/address
        except Exception:
            return None
    # hex
    if _is_hex_addr(addr):
        s = addr.lower()
        if s.startswith("0x"):
            s = s[2:]
        try:
            return bytes.fromhex(s)
        except Exception:
            return None
    return None


def _parse_address(addr: str) -> bytes:
    """
    Parse address to bytes, trying state_service.parse_address first, then fallback.
    """
    # Try state_service.parse_address if available
    try:
        from rpc.state_service import parse_address
        return parse_address(addr)
    except Exception:
        pass
    
    # Fallback to local implementation
    key = _to_account_key_bytes(addr)
    if key is None:
        raise rpc_errors.InvalidParams(f"Invalid address format: {addr}")
    return key


def _check_mainnet() -> None:
    """
    Check if we're on mainnet (chainId == 1) and raise error if so.
    """
    ctx = deps.get_ctx()
    chain_id = ctx.cfg.chain_id
    if chain_id == 1:
        raise rpc_errors.RpcError(
            code=-32600,  # Invalid Request
            message="Faucet is not available on mainnet",
            data={"chainId": chain_id, "reason": "mainnet_disabled"}
        )


def _get_balance(addr_bytes: bytes) -> int:
    """Get current balance from state DB."""
    ctx = deps.get_ctx()
    sdb = ctx.state_db
    
    # Try get_balance if available
    if hasattr(sdb, "get_balance"):
        try:
            return int(sdb.get_balance(addr_bytes))
        except Exception:
            pass
    
    # Try get_account
    if hasattr(sdb, "get_account"):
        try:
            acct = sdb.get_account(addr_bytes)
            if acct is not None:
                if hasattr(acct, "balance"):
                    return int(acct.balance)
                if isinstance(acct, dict) and "balance" in acct:
                    return int(acct["balance"])
        except Exception:
            pass
    
    return 0


def _add_balance(addr_bytes: bytes, amount: int) -> int:
    """
    Add balance to an account, creating it if necessary.
    Returns the new balance.
    """
    ctx = deps.get_ctx()
    sdb = ctx.state_db
    
    # Try add_balance if available (preferred for atomicity)
    if hasattr(sdb, "add_balance"):
        try:
            return int(sdb.add_balance(addr_bytes, amount))
        except Exception:
            pass
    
    # Fallback: manual get/set
    current_balance = _get_balance(addr_bytes)
    new_balance = current_balance + amount
    
    if hasattr(sdb, "set_balance"):
        sdb.set_balance(addr_bytes, new_balance)
        return new_balance
    
    # Last resort: ensure_account + put_account
    if hasattr(sdb, "ensure_account") and hasattr(sdb, "put_account"):
        acct = sdb.ensure_account(addr_bytes)
        acct.balance = new_balance
        sdb.put_account(addr_bytes, acct)
        return new_balance
    
    raise rpc_errors.InternalError("StateDB does not support balance updates")


def _to_hex_quantity(n: int) -> str:
    """Convert int to hex quantity string."""
    if n < 0:
        raise rpc_errors.InternalError("negative quantity not allowed")
    return hex(n)


# ——— RPC Methods ———


@method(
    "faucet.request",
    desc="Request test funds from the devnet/testnet faucet. NOT AVAILABLE ON MAINNET. "
         "Credits the specified address with tokens directly (no block production required). "
         "Default amount: 500,000,000 ANM (500000000000000000 base units).",
)
def faucet_request(address: str, amount: t.Optional[int] = None) -> dict:
    """
    Request test funds from the faucet (devnet/testnet only).
    
    Args:
        address: Recipient address (bech32m anim1... or hex 0x...)
        amount: Optional amount in base units (default: 500,000,000 ANM)
    
    Returns:
        {
            "address": str,        # recipient address (as provided)
            "amount": str,         # amount credited (hex quantity)
            "balance": str,        # new balance after credit (hex quantity)
            "message": str         # confirmation message
        }
    
    Raises:
        RpcError: If called on mainnet (chainId == 1)
        InvalidParams: If address is invalid or amount is negative
    """
    # 1. Check not mainnet
    _check_mainnet()
    
    # 2. Validate address
    addr = _validate_address(address)
    addr_bytes = _parse_address(addr)
    
    # 3. Determine amount
    if amount is None:
        credit_amount = DEFAULT_FAUCET_AMOUNT
    else:
        if not isinstance(amount, int) or amount <= 0:
            raise rpc_errors.InvalidParams("amount must be a positive integer")
        credit_amount = amount
    
    # 4. Credit the account
    try:
        new_balance = _add_balance(addr_bytes, credit_amount)
    except Exception as e:
        raise rpc_errors.InternalError(f"Failed to credit account: {e}")
    
    # 5. Return response
    return {
        "address": addr,
        "amount": _to_hex_quantity(credit_amount),
        "balance": _to_hex_quantity(new_balance),
        "message": f"Credited {credit_amount} base units to {addr}"
    }
