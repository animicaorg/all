from __future__ import annotations

import typing as t

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

# Optional helpers (be tolerant during bring-up)
try:
    from pq.py.utils import bech32 as _bech32  # type: ignore
except Exception:  # pragma: no cover
    _bech32 = None  # type: ignore


# ——— Utilities ———


def _is_hex_addr(s: str) -> bool:
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
    if not isinstance(addr, str) or not addr:
        raise rpc_errors.InvalidParams("address must be a non-empty string")
    a = addr.strip()
    # Accept anim… bech32m, system:…, or raw hex (0x…)
    if a.lower().startswith("anim"):
        # Fast shape check; full decode only if needed in fallback path
        return a
    if a.lower().startswith("system:"):
        # Allow system addresses for genesis/treasury accounts
        return a
    if _is_hex_addr(a):
        if not a.lower().startswith("0x"):
            a = "0x" + a
        return a
    raise rpc_errors.InvalidParams("address must be anim… (bech32m), system:… or 0x… (hex)")


def _to_account_key_bytes(addr: str) -> bytes | None:
    """
    Best-effort conversion to the canonical account key (bytes) for direct StateDB access.
    We prefer to let deps.state_service handle formats; this is only used as a last-resort fallback.
    """
    if addr.lower().startswith("anim") and _bech32 is not None:
        try:
            hrp, data = _bech32.decode(addr)
            if hrp and data:
                payload = bytes(data)
                if len(payload) == 34:
                    return payload[2:34]
                return payload  # payload = (alg_id || sha3_256(pubkey)) per pq/address
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


def _to_hex_quantity(n: int) -> str:
    if n < 0:
        raise rpc_errors.InternalError("negative quantity not allowed")
    return hex(n)


# ——— Service Adapters ———


def _svc_balance(addr: str, *, tag: str = "latest") -> int:
    """
    Query balance (in smallest unit) using the best available dependency.
    Returns an integer. Returns 0 if account does not exist.
    """
    # Preferred: dedicated state_service (handles address parsing)
    try:
        from rpc.state_service import get_balance as state_svc_get_balance
        return int(state_svc_get_balance(addr))
    except Exception:
        pass

    # Fallback: raw StateDB with manual address parsing
    # CRITICAL: state_db is stored in the RpcContext, not as a module attribute
    try:
        ctx = deps.get_ctx()
        sdb = ctx.state_db
    except Exception:
        sdb = None
    
    if sdb is not None:
        # Parse address (bech32m or hex) to bytes
        try:
            from rpc.state_service import parse_address
            key = parse_address(addr)
        except Exception:
            key = _to_account_key_bytes(addr)
        
        if key is not None:
            # Try get_balance if available
            if hasattr(sdb, "get_balance"):
                try:
                    return int(sdb.get_balance(key))  # type: ignore[no-any-return]
                except Exception:
                    pass
            
            # Try get_account (handles both dict and Account objects)
            if hasattr(sdb, "get_account"):
                try:
                    acct = sdb.get_account(key)  # type: ignore[attr-defined]
                    if acct is not None:
                        # Handle Account object (with .balance attribute)
                        if hasattr(acct, "balance"):
                            return int(acct.balance)
                        # Handle dict
                        if isinstance(acct, dict) and "balance" in acct:
                            return int(acct["balance"])
                except Exception:
                    pass
    
    # Return 0 for non-existent accounts (standard behavior)
    return 0


def _svc_nonce(addr: str, *, tag: str = "latest") -> int:
    """
    Query account nonce using the best available dependency.
    Returns 0 if account does not exist (standard behavior for new accounts).
    """
    # Preferred: dedicated state_service (handles address parsing)
    try:
        from rpc.state_service import get_nonce as state_svc_get_nonce
        return int(state_svc_get_nonce(addr))
    except Exception:
        pass

    # Fallback: raw StateDB with manual address parsing
    # CRITICAL: state_db is stored in the RpcContext, not as a module attribute
    try:
        ctx = deps.get_ctx()
        sdb = ctx.state_db
    except Exception:
        sdb = None
    
    if sdb is not None:
        # Parse address (bech32m or hex) to bytes
        try:
            from rpc.state_service import parse_address
            key = parse_address(addr)
        except Exception:
            key = _to_account_key_bytes(addr)
        
        if key is not None:
            # Try get_nonce if available
            if hasattr(sdb, "get_nonce"):
                try:
                    return int(sdb.get_nonce(key))  # type: ignore[no-any-return]
                except Exception:
                    pass
            
            # Try get_account (handles both dict and Account objects)
            if hasattr(sdb, "get_account"):
                try:
                    acct = sdb.get_account(key)  # type: ignore[attr-defined]
                    if acct is not None:
                        # Handle Account object (with .nonce attribute)
                        if hasattr(acct, "nonce"):
                            return int(acct.nonce)
                        # Handle dict
                        if isinstance(acct, dict) and "nonce" in acct:
                            return int(acct["nonce"])
                except Exception:
                    pass
    
    # Return 0 for non-existent accounts (standard behavior)
    return 0


# ——— RPC Methods ———


@method(
    "state.getBalance",
    desc="Return the account balance for an address at a given block tag. Returns a hex quantity string (e.g. 0x0).",
)
def state_get_balance(address: str, tag: str = "latest") -> str:
    addr = _validate_address(address)
    tag = (tag or "latest").lower()
    if tag not in (
        "latest",
        "pending",
        "safe",
        "finalized",
    ):  # be liberal; ignore unknowns as 'latest'
        tag = "latest"
    value = _svc_balance(addr, tag=tag)
    return _to_hex_quantity(value)


@method(
    "state.getNonce",
    desc="Return the transaction nonce (account sequence) for an address at a given block tag. Returns a JSON number.",
)
def state_get_nonce(address: str, tag: str = "latest") -> int:
    addr = _validate_address(address)
    tag = (tag or "latest").lower()
    if tag not in ("latest", "pending", "safe", "finalized"):
        tag = "latest"
    nonce = int(_svc_nonce(addr, tag=tag))
    
    # For "pending" tag, check mempool for higher nonce
    if tag == "pending":
        pending_nonce = _svc_pending_nonce(addr)
        return max(nonce, pending_nonce)
    
    return nonce


def _svc_pending_nonce(addr: str) -> int:
    """
    Calculate pending nonce by checking mempool for pending transactions.
    
    Returns the highest nonce found in pending transactions + 1, or committed nonce if no pending txs.
    """
    committed_nonce = _svc_nonce(addr, tag="latest")
    
    # Try to access pending pool to find highest pending nonce
    # Import is inside function to avoid circular dependencies
    try:
        from rpc.methods import tx as tx_methods
        
        # Check _PEND first (same priority as _pending_put)
        pend = getattr(tx_methods, "_PEND", None)
        pending_map = {}
        
        if pend is not None:
            # Try to get items from _PEND
            if hasattr(pend, "items") and callable(pend.items):
                try:
                    pending_map = dict(pend.items())
                except Exception:
                    pass
            elif hasattr(pend, "list_raw") and callable(pend.list_raw):
                try:
                    items = pend.list_raw()
                    pending_map = dict(items)
                except Exception:
                    pass
        
        # Fallback to _FALLBACK_PENDING if _PEND is None or didn't provide items
        if not pending_map:
            fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
            pending_map = fallback
        
        if not pending_map:
            return committed_nonce
        
        # Normalize address to bytes for robust comparison
        # Supports both bech32 (anim1...) and hex (0x...) formats
        try:
            addr_bytes = _to_account_key_bytes(addr)
            if addr_bytes is None:
                # If we can't parse the address, just return committed nonce
                return committed_nonce
        except Exception:
            return committed_nonce
        
        # Start at committed_nonce - 1 so any pending nonce >= committed_nonce will be detected
        # This ensures we return the highest pending nonce + 1
        highest_pending_nonce = committed_nonce - 1
        
        for tx_hash_hex, raw in pending_map.items():
            try:
                # Decode transaction to check sender and nonce
                decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                
                # Extract sender from body (RPC envelope format)
                body = obj.get("body", {}) if isinstance(obj, dict) else {}
                tx_from = body.get("from", body.get("sender", ""))
                
                # Convert tx sender to bytes for comparison
                if isinstance(tx_from, (bytes, bytearray)):
                    tx_from_bytes = bytes(tx_from)
                elif isinstance(tx_from, str):
                    # Try to parse as bech32 or hex
                    tx_from_bytes = _to_account_key_bytes(tx_from)
                    if tx_from_bytes is None:
                        continue
                else:
                    continue
                
                # Check if this tx is from our address (compare bytes)
                if tx_from_bytes == addr_bytes:
                    tx_nonce = body.get("nonce", 0)
                    if isinstance(tx_nonce, int) and tx_nonce > highest_pending_nonce:
                        highest_pending_nonce = tx_nonce
            except Exception:
                # Skip transactions we can't decode
                continue
        
        # Return highest pending nonce + 1, or committed nonce if no pending txs
        if highest_pending_nonce >= committed_nonce:
            return highest_pending_nonce + 1
        
    except Exception:
        # If anything fails, return committed nonce
        pass
    
    return committed_nonce


@method(
    "state.getPendingNonce",
    desc="Return the pending nonce for an address (includes pending transactions in mempool).",
    aliases=("state_getPendingNonce",),
)
def state_get_pending_nonce(address: str) -> int:
    """
    Get pending nonce for an address (committed nonce + count of pending transactions).
    
    This is the nonce that should be used for the next transaction submission
    to avoid nonce reuse.
    """
    addr = _validate_address(address)
    return int(_svc_pending_nonce(addr))


@method(
    "state.getAccount",
    desc="Return the full account state (address, nonce, balance) for an address. Useful for debugging.",
    aliases=("state_getAccount",),
)
def state_get_account(address: str, tag: str = "latest") -> dict:
    """
    Get full account state for an address.
    
    Args:
        address: Address in bech32 (anim1...), system:... or hex (0x...) format
        tag: Block tag (latest, pending, safe, finalized)
        
    Returns:
        dict: {
            "address": str,     # Original address format
            "nonce": int,       # Transaction count/sequence number
            "balance": str,     # Balance in hex (e.g., "0x0" for 0 nANM)
        }
    """
    addr = _validate_address(address)
    tag = (tag or "latest").lower()
    if tag not in ("latest", "pending", "safe", "finalized"):
        tag = "latest"
    
    balance = _svc_balance(addr, tag=tag)
    nonce = _svc_nonce(addr, tag=tag)
    
    return {
        "address": addr,
        "nonce": int(nonce),
        "balance": _to_hex_quantity(balance),
    }
