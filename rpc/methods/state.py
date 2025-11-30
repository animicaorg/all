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
    We prefer to let deps.state_service handle formats; this is only used as a last-resort fallback.
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


def _to_hex_quantity(n: int) -> str:
    if n < 0:
        raise rpc_errors.InternalError("negative quantity not allowed")
    return hex(n)


# ——— Service Adapters ———


def _svc_balance(addr: str, *, tag: str = "latest") -> int:
    """
    Query balance (in smallest unit) using the best available dependency.
    Returns an integer.
    """
    # Preferred: dedicated state_service
    svc = getattr(deps, "state_service", None)
    if svc is not None:
        if hasattr(svc, "get_balance"):
            return int(svc.get_balance(addr, tag=tag))  # type: ignore[no-any-return]
        if hasattr(svc, "balance"):
            return int(svc.balance(addr, tag=tag))  # type: ignore[no-any-return]

    # Direct helpers on deps
    if hasattr(deps, "get_balance"):
        return int(deps.get_balance(addr, tag=tag))  # type: ignore[no-any-return]
    if hasattr(deps, "balance"):
        return int(deps.balance(addr, tag=tag))  # type: ignore[no-any-return]

    # Fallback: raw StateDB (best effort)
    sdb = getattr(deps, "state_db", None)
    if sdb is not None:
        if hasattr(sdb, "get_balance"):
            return int(sdb.get_balance(addr))  # type: ignore[no-any-return]
        key = _to_account_key_bytes(addr)
        if key is not None:
            # heuristics: try get_account → dict with "balance"; else try get(key,"balance")
            if hasattr(sdb, "get_account"):
                acct = sdb.get_account(key)  # type: ignore[attr-defined]
                if acct and isinstance(acct, dict) and "balance" in acct:
                    return int(acct["balance"])
            if hasattr(sdb, "get"):
                val = sdb.get(key, b"balance")  # type: ignore[attr-defined]
                if val is not None:
                    try:
                        return int(val)
                    except Exception:
                        pass

    raise rpc_errors.InternalError("balance service unavailable")


def _svc_nonce(addr: str, *, tag: str = "latest") -> int:
    """
    Query account nonce using the best available dependency.
    """
    svc = getattr(deps, "state_service", None)
    if svc is not None:
        if hasattr(svc, "get_nonce"):
            return int(svc.get_nonce(addr, tag=tag))  # type: ignore[no-any-return]
        if hasattr(svc, "nonce"):
            return int(svc.nonce(addr, tag=tag))  # type: ignore[no-any-return]

    if hasattr(deps, "get_nonce"):
        return int(deps.get_nonce(addr, tag=tag))  # type: ignore[no-any-return]
    if hasattr(deps, "nonce"):
        return int(deps.nonce(addr, tag=tag))  # type: ignore[no-any-return]

    sdb = getattr(deps, "state_db", None)
    if sdb is not None:
        if hasattr(sdb, "get_nonce"):
            return int(sdb.get_nonce(addr))  # type: ignore[no-any-return]
        key = _to_account_key_bytes(addr)
        if key is not None:
            if hasattr(sdb, "get_account"):
                acct = sdb.get_account(key)  # type: ignore[attr-defined]
                if acct and isinstance(acct, dict) and "nonce" in acct:
                    return int(acct["nonce"])
            if hasattr(sdb, "get"):
                val = sdb.get(key, b"nonce")  # type: ignore[attr-defined]
                if val is not None:
                    try:
                        return int(val)
                    except Exception:
                        pass

    raise rpc_errors.InternalError("nonce service unavailable")


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
    return int(_svc_nonce(addr, tag=tag))
