from __future__ import annotations

import logging
import os
import typing as t
from typing import Any, Dict

from rpc import deps
from rpc import errors as rpc_errors
from rpc.methods import method

# Optional helpers (be tolerant during bring-up)
try:
    from pq.py.utils import bech32 as _bech32  # type: ignore
except Exception:  # pragma: no cover
    _bech32 = None  # type: ignore


log = logging.getLogger("animica.rpc.state")

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
            # Use decode_address which properly converts 5-bit data to bytes
            payload = _bech32.decode_address(addr)
            if payload:
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


def _coerce_hex_bytes(value: Any, *, field: str) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if not isinstance(value, str):
        raise rpc_errors.InvalidParams(f"{field} must be a hex string")
    text = value.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if not text:
        return b""
    if len(text) % 2 != 0:
        text = "0" + text
    try:
        return bytes.fromhex(text)
    except Exception as exc:
        raise rpc_errors.InvalidParams(f"{field} must be valid hex") from exc


def _parse_state_call_args(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, bytes, str | None]:
    """
    Supported shapes:
      - object style: [{"to":"...","data":"0x...","from":"..."}]
      - positional style: ["<to>", "0x...", "<from_or_null>", null]
      - named object style: {"to":"...","data":"0x...","from":"..."}
    """
    payload: dict[str, Any] | None = None
    if kwargs:
        payload = dict(kwargs)
    elif len(args) == 1 and isinstance(args[0], dict):
        payload = dict(args[0])
    elif len(args) >= 2:
        to_val = args[0]
        data_val = args[1]
        from_val = args[2] if len(args) >= 3 else None
        if not isinstance(to_val, str):
            raise rpc_errors.InvalidParams("state.call positional argument 0 must be contract address string")
        return to_val, _coerce_hex_bytes(data_val, field="data"), (
            str(from_val) if isinstance(from_val, str) and from_val.strip() else None
        )
    else:
        raise rpc_errors.InvalidParams(
            "state.call expects either [{to,data,from?}] or [to,data,from?,block?]"
        )

    to_val = payload.get("to")
    data_val = payload.get("data")
    from_val = payload.get("from")
    if not isinstance(to_val, str) or not to_val.strip():
        raise rpc_errors.InvalidParams("state.call payload requires non-empty 'to' field")
    if data_val is None:
        raise rpc_errors.InvalidParams("state.call payload requires 'data' field")
    from_text: str | None = None
    if isinstance(from_val, str) and from_val.strip():
        from_text = from_val.strip()
    return to_val.strip(), _coerce_hex_bytes(data_val, field="data"), from_text


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


def _nonce_disabled(method: str) -> None:
    raise rpc_errors.RpcMethodRestricted(
        detail="Nonce tracking is disabled for nonce-less transactions.",
        method=method,
    )


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
    "state.call",
    aliases=(
        "execution.simulateCall",
        "vm.simulateCall",
        "state.simulateCall",
        "call.simulate",
        "contracts.simulate",
    ),
    desc=(
        "Read-only contract simulation call. Accepts payload {to,data,from?} or "
        "positional [to,data,from?,block?]. Returns ABI-encoded return bytes as 0x-hex."
    ),
)
def state_call(*args: Any, **kwargs: Any) -> str:
    to_addr_raw, calldata, sender_raw = _parse_state_call_args(args, kwargs)

    try:
        from rpc.state_service import parse_address
    except Exception as exc:  # pragma: no cover
        raise rpc_errors.InternalError("state address parser unavailable") from exc

    try:
        contract_addr = parse_address(to_addr_raw)
    except Exception as exc:
        raise rpc_errors.InvalidParams(
            "malformed contract address",
            address=to_addr_raw,
        ) from exc

    sender_addr: bytes | None = None
    if sender_raw:
        try:
            sender_addr = parse_address(sender_raw)
        except Exception as exc:
            raise rpc_errors.InvalidParams(
                "malformed sender address",
                sender=sender_raw,
            ) from exc

    try:
        ctx = deps.get_ctx()
        state_db = getattr(ctx, "state_db", None)
    except Exception:
        state_db = None
    if state_db is None:
        raise rpc_errors.InternalError("State DB not available")

    try:
        from execution.runtime import contracts as runtime_contracts
    except Exception as exc:
        raise rpc_errors.InternalError(
            "contract simulation runtime unavailable",
            error=str(exc),
        ) from exc

    try:
        raw = runtime_contracts.simulate_call(
            state=state_db,
            contract_addr=contract_addr,
            calldata=calldata,
            sender=sender_addr,
        )
    except runtime_contracts.ContractNotFoundError as exc:
        raise rpc_errors.NotFound("contract") from exc
    except runtime_contracts.InvalidCalldataError as exc:
        raise rpc_errors.InvalidParams(str(exc)) from exc
    except runtime_contracts.ContractCallError as exc:
        raise rpc_errors.InternalError(str(exc)) from exc
    except Exception as exc:
        raise rpc_errors.InternalError("contract simulation failed", error=str(exc)) from exc

    if not isinstance(raw, (bytes, bytearray)):
        raise rpc_errors.InternalError(
            "contract simulation returned unsupported result type",
            result_type=type(raw).__name__,
        )
    return "0x" + bytes(raw).hex()


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
    # Read-through: when the local node is behind a trusted upstream (still
    # syncing / stalled / freshly bootstrapped) return the AUTHORITATIVE balance
    # instead of a stale 0. A fully-synced node returns its own local value.
    # Consensus/execution/tx-validation always use LOCAL state, never this.
    try:
        import os as _os
        from rpc.upstream_balance import authoritative as _authoritative
        _head = deps.get_head() or {}
        _cid = _head.get("chainId", _head.get("chain_id"))
        if _cid is None:
            try:
                _cid = int(_os.environ.get("ANIMICA_CHAIN_ID") or 0) or None
            except Exception:
                _cid = None
        value = _authoritative(addr, "state.getBalance", int(value), _head.get("height"), _cid)
    except Exception:
        pass
    return _to_hex_quantity(value)


@method(
    "state.getAddressBalance",
    aliases=("wallet.getAddressBalance", "account.getBalance"),
    desc=(
        "Return a rich balance object for an address with unit info, availability flags, "
        "and head context. Stable schema for Studio and Explorer integration."
    ),
)
def state_get_address_balance(address: str = "", **kwargs) -> dict:
    """
    Return stable balance schema for Studio/Explorer:
    {
      address, exists, confirmed_balance, pending_incoming, pending_outgoing,
      spendable_balance, unit, display_decimals, as_of_head_height, as_of_head_hash
    }

    Zero balance is explicit (exists=true, confirmed_balance="0").
    Invalid address returns INVALID_PARAMS error.
    Temporarily unavailable returns structured service error.
    """
    import typing as t

    # Accept object params or positional
    if not address:
        address = str(kwargs.get("address") or "")
    if not address:
        raise rpc_errors.InvalidParams("address is required", field="address")

    addr = _validate_address(address)

    # Get head context
    head_height: t.Optional[int] = None
    head_hash: t.Optional[str] = None
    try:
        head = deps.get_head()
        head_height = head.get("height")
        head_hash = head.get("hash")
    except Exception:
        pass

    # Get confirmed balance
    exists = True
    confirmed_balance = "0"
    try:
        value = _svc_balance(addr, tag="latest")
        confirmed_balance = str(int(value))
    except rpc_errors.RpcError:
        raise
    except Exception:
        # Address doesn't exist in state = zero balance, not error
        confirmed_balance = "0"
        exists = True

    return {
        "address": addr,
        "exists": exists,
        "confirmed_balance": confirmed_balance,
        "pending_incoming": None,
        "pending_outgoing": None,
        "spendable_balance": confirmed_balance,
        "unit": "nANM",
        "display_decimals": 9,
        "as_of_head_height": head_height,
        "as_of_head_hash": head_hash,
    }


@method(
    "state.getNonce",
    desc="Return the account nonce for an address at a given block tag (latest|pending).",
)
def state_get_nonce(address: str, tag: str = "latest") -> int:
    addr = _validate_address(address)
    tag = (tag or "latest").lower()
    if tag in ("pending", "latest", "safe", "finalized"):
        pass
    else:
        tag = "latest"

    if tag == "pending":
        local_nonce = int(_svc_pending_nonce(addr))
    else:
        local_nonce = int(_svc_nonce(addr, tag=tag))
    # Read-through: a node behind a trusted upstream returns a stale (too-low)
    # nonce, which breaks withdrawal construction. Use the authoritative nonce
    # when behind; never return lower than what we computed locally.
    try:
        import os as _os
        from rpc.upstream_balance import authoritative as _authoritative
        _head = deps.get_head() or {}
        _cid = _head.get("chainId", _head.get("chain_id"))
        if _cid is None:
            try:
                _cid = int(_os.environ.get("ANIMICA_CHAIN_ID") or 0) or None
            except Exception:
                _cid = None
        _auth = _authoritative(addr, "state.getNonce", local_nonce, _head.get("height"), _cid,
                               extra_params=[tag])
        return max(local_nonce, int(_auth))
    except Exception:
        return local_nonce


def _svc_pending_nonce(addr: str) -> int:
    """
    Calculate pending nonce by checking mempool for pending transactions.
    
    Args:
        addr: The account address (bech32, system:, or hex format)
    
    Returns:
        The next usable nonce for the address, accounting for both committed 
        state and pending mempool transactions. Returns the highest pending 
        nonce + 1, or committed nonce if no pending transactions exist.
    """
    import os
    _DEBUG_NONCE = os.environ.get("ANIMICA_DEBUG_NONCE") == "1"
    
    committed_nonce = _svc_nonce(addr, tag="latest")
    
    if _DEBUG_NONCE:
        log.info(
            "state.getNextNonce: computing for address",
            extra={"address": addr, "confirmed_nonce": committed_nonce},
        )
    else:
        log.debug(
            "state.getNextNonce: computing for address",
            extra={"address": addr, "confirmed_nonce": committed_nonce},
        )

    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None

    mempool_service = None
    try:
        from rpc.methods import tx as tx_methods

        resolver = getattr(tx_methods, "_get_mempool_service", None)
        if callable(resolver):
            mempool_service = resolver()
    except Exception:
        mempool_service = None
    if mempool_service is None and ctx is not None:
        mempool_service = getattr(ctx, "mempool", None)
    if mempool_service is not None:
        try:
            addr_bytes = _to_account_key_bytes(addr)
        except Exception:
            addr_bytes = None
        if addr_bytes is None:
            if _DEBUG_NONCE:
                log.warning(
                    "state.getNextNonce: failed to parse address",
                    extra={"address": addr},
                )
            else:
                log.debug(
                    "state.getNextNonce: failed to parse address",
                    extra={"address": addr},
                )
            return committed_nonce
        
        # Acquire per-sender lock to prevent TOCTOU race with tx admission
        sender_hex = "0x" + addr_bytes.hex() if isinstance(addr_bytes, bytes) else str(addr_bytes)
        sender_lock = mempool_service._get_sender_lock(sender_hex)

        with sender_lock:
            # Use the authoritative nonce tracker from mempool service
            # This is the same calculation used during tx admission to prevent TOCTOU
            computed_next = mempool_service.get_next_nonce(addr_bytes, committed_nonce)

            # Get pending info for logging
            pending_nonces = mempool_service.pending_nonces(addr_bytes)
            pending_next = (
                mempool_service.pending_nonce(addr_bytes, committed_nonce)
                if pending_nonces
                else None
            )

            if _DEBUG_NONCE:
                log.info(
                    "state.getNextNonce: authoritative calculation (locked)",
                    extra={
                        "address": addr,
                        "confirmed_nonce": committed_nonce,
                        "highest_pending_nonce": max(pending_nonces) if pending_nonces else None,
                        "pending_next_nonce": pending_next,
                        "returned_next_nonce": computed_next,
                    },
                )
            else:
                log.debug(
                    "state.getNextNonce: found pending transactions" if pending_next is not None else "state.getNextNonce: no pending transactions",
                    extra={
                        "address": addr,
                        "confirmed_nonce": committed_nonce,
                        "pending_next": pending_next,
                        "computed_next": computed_next,
                    },
                )
        return computed_next
    
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
            log.debug(
                "state.getNextNonce: no pending pool available",
                extra={"address": addr, "chain_nonce": committed_nonce, "pending_next": None, "computed_next": committed_nonce},
            )
            return committed_nonce
        
        # Normalize address to bytes for robust comparison
        # Supports both bech32 (anim1...) and hex (0x...) formats
        try:
            addr_bytes = _to_account_key_bytes(addr)
            if addr_bytes is None:
                # If we can't parse the address, just return committed nonce
                log.debug(
                    "state.getNextNonce: failed to parse address for pending check",
                    extra={"address": addr},
                )
                return committed_nonce
        except Exception:
            return committed_nonce
        
        pending_nonces: set[int] = set()

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
                    if isinstance(tx_nonce, int):
                        pending_nonces.add(tx_nonce)
            except Exception:
                # Skip transactions we can't decode
                continue
        
        if pending_nonces:
            computed_next = committed_nonce
            while computed_next in pending_nonces:
                computed_next += 1
            log.debug(
                "state.getNextNonce: found pending transactions in fallback pool",
                extra={
                    "address": addr,
                    "chain_nonce": committed_nonce,
                    "highest_pending": max(pending_nonces),
                    "computed_next": computed_next,
                },
            )
            return computed_next
        
    except Exception:
        # If anything fails, return committed nonce
        pass
    
    log.debug(
        "state.getNextNonce: using committed nonce (no pending found)",
        extra={"address": addr, "chain_nonce": committed_nonce, "pending_next": None, "computed_next": committed_nonce},
    )
    return committed_nonce


@method(
    "state.getPendingNonce",
    desc="Return the next usable nonce for an address, accounting for pending mempool transactions.",
    aliases=("state_getPendingNonce",),
)
def state_get_pending_nonce(address: str) -> int:
    addr = _validate_address(address)
    return int(_svc_pending_nonce(addr))


@method(
    "state.getNextNonce",
    desc="Return the next usable nonce for an address, accounting for pending mempool transactions.",
    aliases=("state_getNextNonce",),
)
def state_get_next_nonce(address: str) -> int:
    addr = _validate_address(address)
    return int(_svc_pending_nonce(addr))


@method(
    "state.getAccount",
    desc="Return the full account state (address, balance) for an address. Useful for debugging.",
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
            "balance": str,     # Balance in hex (e.g., "0x0" for 0 nANM)
        }
    """
    addr = _validate_address(address)
    tag = (tag or "latest").lower()
    if tag not in ("latest", "pending", "safe", "finalized"):
        tag = "latest"
    
    balance = _svc_balance(addr, tag=tag)
    
    return {
        "address": addr,
        "balance": _to_hex_quantity(balance),
    }


@method(
    "state.getRichList",
    desc="Return addresses sorted by balance (descending). Supports pagination with limit/offset.",
)
def state_get_rich_list(limit: int = 100, offset: int = 0) -> dict:
    """
    Get rich list - addresses sorted by balance in descending order.
    
    Args:
        limit: Maximum number of addresses to return (default: 100, max: 1000)
        offset: Number of addresses to skip (for pagination)
        
    Returns:
        dict: {
            "height": int,              # Chain height when list was generated
            "totalAddresses": int,      # Total number of non-zero balance addresses
            "items": [
                {
                    "rank": int,        # Position in ranking (1-indexed)
                    "address": str,     # Address in bech32 format
                    "balance": str,     # Balance in hex (e.g., "0x1234")
                }
            ]
        }
    """
    # Validate and clamp parameters
    limit = max(1, min(int(limit), 1000))  # Max 1000 to prevent excessive load
    offset = max(0, int(offset))
    
    # Get state DB from context
    try:
        ctx = deps.get_ctx()
        sdb = ctx.state_db
    except Exception:
        raise rpc_errors.InternalError("State DB not available")
    
    if sdb is None:
        raise rpc_errors.InternalError("State DB not available")
    
    # Get current head height
    try:
        from rpc.methods.chain import chain_get_head
        head = chain_get_head()
        height = head.get("height", 0)
    except Exception:
        height = 0
    
    # Collect all accounts and balances
    accounts: list[tuple[bytes, int]] = []
    
    try:
        # Use iter_accounts to enumerate all accounts
        if hasattr(sdb, "iter_accounts"):
            for addr_bytes, account in sdb.iter_accounts():
                balance = account.balance if hasattr(account, "balance") else (
                    account.get("balance", 0) if isinstance(account, dict) else 0
                )
                # Only include accounts with non-zero balance
                if balance > 0:
                    accounts.append((addr_bytes, int(balance)))
        else:
            raise rpc_errors.InternalError("State DB does not support account iteration")
    except Exception as e:
        log.error("Failed to iterate accounts", exc_info=e)
        raise rpc_errors.InternalError(f"Failed to enumerate accounts: {str(e)}")
    
    # Sort by balance descending
    accounts.sort(key=lambda x: x[1], reverse=True)
    
    total_addresses = len(accounts)
    
    # Apply pagination
    start_idx = offset
    end_idx = min(offset + limit, total_addresses)
    page_accounts = accounts[start_idx:end_idx]
    
    # Format results
    items = []
    for idx, (addr_bytes, balance) in enumerate(page_accounts):
        rank = start_idx + idx + 1  # 1-indexed rank
        
        # Convert address to bech32 format
        try:
            if _bech32 is not None:
                # Encode as bech32m with "anim" prefix
                # Address payload format: 0x00 (version) + 0x01 (algo_id) + addr_bytes
                # For simplicity, if addr_bytes is 32 bytes, prepend version/algo
                if len(addr_bytes) == 32:
                    payload = b'\x00\x01' + addr_bytes  # version 0, algo 1
                else:
                    payload = addr_bytes
                
                words = _bech32.convertbits(payload, 8, 5)
                if words is not None:
                    addr_str = _bech32.encode("anim", 1, words)  # bech32m (variant 1)
                else:
                    addr_str = "0x" + addr_bytes.hex()
            else:
                addr_str = "0x" + addr_bytes.hex()
        except Exception:
            addr_str = "0x" + addr_bytes.hex()
        
        items.append({
            "rank": rank,
            "address": addr_str,
            "balance": _to_hex_quantity(balance)
        })
    
    return {
        "height": height,
        "totalAddresses": total_addresses,
        "items": items
    }


@method(
    "state.getTotalSupply",
    desc="Return the total supply (sum of all account balances).",
)
def state_get_total_supply() -> dict:
    """
    Get total supply by summing all account balances.
    
    Returns:
        dict: {
            "height": int,           # Chain height when computed
            "totalSupply": str,      # Total supply in hex
            "addressCount": int,     # Number of accounts with non-zero balance
        }
    """
    # Get state DB from context
    try:
        ctx = deps.get_ctx()
        sdb = ctx.state_db
    except Exception:
        raise rpc_errors.InternalError("State DB not available")
    
    if sdb is None:
        raise rpc_errors.InternalError("State DB not available")
    
    # Get current head height
    try:
        from rpc.methods.chain import chain_get_head
        head = chain_get_head()
        height = head.get("height", 0)
    except Exception:
        height = 0
    
    # Sum all balances
    total_supply = 0
    address_count = 0
    
    try:
        if hasattr(sdb, "iter_accounts"):
            for addr_bytes, account in sdb.iter_accounts():
                balance = account.balance if hasattr(account, "balance") else (
                    account.get("balance", 0) if isinstance(account, dict) else 0
                )
                if balance > 0:
                    total_supply += int(balance)
                    address_count += 1
        else:
            raise rpc_errors.InternalError("State DB does not support account iteration")
    except Exception as e:
        log.error("Failed to compute total supply", exc_info=e)
        raise rpc_errors.InternalError(f"Failed to compute total supply: {str(e)}")
    
    return {
        "height": height,
        "totalSupply": _to_hex_quantity(total_supply),
        "addressCount": address_count
    }


@method(
    "state.getAicfSummary",
    desc="Get global AICF credit totals and summary statistics"
)
def state_get_aicf_summary() -> Dict[str, Any]:
    """
    Get global AICF credit accounting summary.
    
    Returns:
        Dict with:
        - balance_total: Total AICF credits available
        - minted_total: Total credits minted from mining
        - spent_total: Total credits spent on jobs
        - last_update_height: Last block height that updated credits
        - last_update_hash: Last block hash that updated credits
    """
    import os
    from aicf.protocol.state import ProtocolState
    
    try:
        # Get AICF state DB path
        data_dir = os.getenv("ANIMICA_DATA_DIR", os.path.expanduser("~/.animica/data"))
        aicf_db_path = os.path.join(data_dir, "aicf_protocol.db")
        
        # Initialize protocol state
        protocol_state = ProtocolState(aicf_db_path)
        
        # Get totals
        totals = protocol_state.get_aicf_totals()
        
        return {
            "balance_total": totals.balance_total,
            "minted_total": totals.minted_total,
            "spent_total": totals.spent_total,
            "last_update_height": totals.last_update_height,
            "last_update_hash": totals.last_update_hash,
            "updated_at": totals.updated_at,
        }
    except Exception as e:
        log.error(f"Failed to get AICF summary: {e}", exc_info=True)
        raise rpc_errors.InternalError(f"Failed to get AICF summary: {str(e)}")


@method(
    "state.getAicfMinerCredits",
    desc="Get AICF credit balance for a specific miner address"
)
def state_get_aicf_miner_credits(address: str) -> Dict[str, Any]:
    """
    Get AICF credit balance and statistics for a miner.
    
    Args:
        address: Miner address (hex, bech32, or system format)
    
    Returns:
        Dict with:
        - miner_address: Normalized miner address
        - balance: Current AICF credit balance
        - lifetime_earned: Total credits ever earned
        - lifetime_spent: Total credits ever spent
        - last_mint_height: Last block height that minted credits
        - last_mint_hash: Last block hash that minted credits
    """
    import os
    from aicf.protocol.state import ProtocolState
    
    # Normalize address (remove 0x prefix if present)
    addr_norm = _validate_address(address)
    addr_bytes = _to_account_key_bytes(addr_norm)
    if addr_bytes is None:
        raise rpc_errors.InvalidParams(f"Invalid address: {address}")
    
    miner_addr_hex = addr_bytes.hex()
    
    try:
        # Get AICF state DB path
        data_dir = os.getenv("ANIMICA_DATA_DIR", os.path.expanduser("~/.animica/data"))
        aicf_db_path = os.path.join(data_dir, "aicf_protocol.db")
        
        # Initialize protocol state
        protocol_state = ProtocolState(aicf_db_path)
        
        # Get miner credits
        credits = protocol_state.get_miner_credits(miner_addr_hex)
        
        return {
            "miner_address": "0x" + miner_addr_hex,
            "balance": credits.balance,
            "lifetime_earned": credits.lifetime_earned,
            "lifetime_spent": credits.lifetime_spent,
            "last_mint_height": credits.last_mint_height,
            "last_mint_hash": credits.last_mint_hash,
            "created_at": credits.created_at,
            "updated_at": credits.updated_at,
        }
    except Exception as e:
        log.error(f"Failed to get miner credits for {address}: {e}", exc_info=True)
        raise rpc_errors.InternalError(f"Failed to get miner credits: {str(e)}")
