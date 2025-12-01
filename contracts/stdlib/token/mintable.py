# -*- coding: utf-8 -*-
"""
Mint/Burn extensions for Animica-20 tokens
==========================================

This module augments the base fungible token with role-gated minting and flexible
burn-from semantics. It composes with the generic role manager under
`contracts.stdlib.access.roles` and recognizes the token's owner (as set by
`contracts.stdlib.token.fungible.init`) as the default administrator.

Key ideas
---------
- **MINTER_ROLE** holders (or the token **owner**) may mint to any address.
- **BURNER_ROLE** holders may burn *from any address without allowance* (use with care).
- Callers *without* BURNER_ROLE may still use standard `burn_from` which requires allowance.
- Minimal helper admin functions to grant/revoke roles, gated by **owner OR admin role**.

This file intentionally avoids changing the base ABI in `fungible.py`; instead it
exposes additional entrypoints prefixed with `role_` and small admin helpers.
"""

from __future__ import annotations

from typing import Final

from stdlib import abi, events, storage  # type: ignore

from ..math.safe_uint import u256_sub  # type: ignore
# Token package utilities (stable keys & checks)
from . import (EVT_TRANSFER, key_balance, require_address,  # type: ignore
               require_amount)
# Base fungible implementation and internals we need to update totals atomically
from .fungible import K_TOTAL  # total supply key
from .fungible import ZERO_ADDR  # event "from" sentinel for mint/burn
from .fungible import _mint_to  # internal, safe to reuse (validates inputs)
from .fungible import _set_u256  # u256 <-> storage helpers
from .fungible import _get_u256
from .fungible import owner as token_owner  # type: ignore
from .fungible import total_supply

# Roles/RBAC (optional richer admin model)
try:
    from ..access.roles import DEFAULT_ADMIN_ROLE  # bytes32
    from ..access.roles import \
        has_role  # (role_id: bytes, account: bytes) -> bool
    from ..access.roles import (  # (caller: bytes, role_id: bytes, account: bytes) -> None; type: ignore; (caller: bytes, role_id: bytes, account: bytes) -> None
        grant_role, revoke_role)
except Exception:  # pragma: no cover
    # Lightweight compatibility fallbacks if the roles module is not linked yet.
    # These degrade to "owner-only" control; role checks always return False.
    DEFAULT_ADMIN_ROLE: Final[bytes] = b"role:admin".ljust(32, b"\x00")

    def has_role(role_id: bytes, account: bytes) -> bool:  # type: ignore
        return False

    def grant_role(caller: bytes, role_id: bytes, account: bytes) -> None:  # type: ignore
        _require_owner_only(caller)

    def revoke_role(caller: bytes, role_id: bytes, account: bytes) -> None:  # type: ignore
        _require_owner_only(caller)


# ------------------------------------------------------------------------------
# Role IDs (bytes32). Fixed, readable tags padded to 32 bytes.
# ------------------------------------------------------------------------------

MINTER_ROLE: Final[bytes] = b"tok:role:minter".ljust(32, b"\x00")
BURNER_ROLE: Final[bytes] = b"tok:role:burner".ljust(32, b"\x00")


# ------------------------------------------------------------------------------
# Local guards
# ------------------------------------------------------------------------------


def _is_owner(addr: bytes) -> bool:
    o = token_owner()
    return bool(o) and addr == o


def _require_owner_only(caller: bytes) -> None:
    require_address(caller)
    if not _is_owner(caller):
        abi.revert(b"TOKEN:NOT_OWNER")


def _require_owner_or_admin(caller: bytes) -> None:
    """
    Admin gate for role changes: token owner OR DEFAULT_ADMIN_ROLE holder.
    """
    require_address(caller)
    if _is_owner(caller):
        return
    if has_role(DEFAULT_ADMIN_ROLE, caller):
        return
    abi.revert(b"TOKEN:NOT_ADMIN")


# ------------------------------------------------------------------------------
# Introspection
# ------------------------------------------------------------------------------


def is_minter(account: bytes) -> bool:
    require_address(account)
    # Owner always acts as a minter; explicit role is optional.
    return _is_owner(account) or has_role(MINTER_ROLE, account)


def is_burner(account: bytes) -> bool:
    require_address(account)
    # Owner also acts as a burner by policy.
    return _is_owner(account) or has_role(BURNER_ROLE, account)


# ------------------------------------------------------------------------------
# Admin helpers (owner OR DEFAULT_ADMIN_ROLE)
# ------------------------------------------------------------------------------


def grant_minter(caller: bytes, account: bytes) -> None:
    _require_owner_or_admin(caller)
    require_address(account)
    grant_role(caller, MINTER_ROLE, account)


def revoke_minter(caller: bytes, account: bytes) -> None:
    _require_owner_or_admin(caller)
    require_address(account)
    revoke_role(caller, MINTER_ROLE, account)


def grant_burner(caller: bytes, account: bytes) -> None:
    _require_owner_or_admin(caller)
    require_address(account)
    grant_role(caller, BURNER_ROLE, account)


def revoke_burner(caller: bytes, account: bytes) -> None:
    _require_owner_or_admin(caller)
    require_address(account)
    revoke_role(caller, BURNER_ROLE, account)


# ------------------------------------------------------------------------------
# Role-gated mint/burn entrypoints
# ------------------------------------------------------------------------------


def role_mint(caller: bytes, to: bytes, amount: int) -> bool:
    """
    Mint tokens to `to` if `caller` is the token owner or holds MINTER_ROLE.
    Emits a standard Transfer(ZERO_ADDR -> to, amount).
    """
    require_address(caller)
    require_address(to)
    require_amount(amount)
    if amount == 0:
        return True

    if not (_is_owner(caller) or has_role(MINTER_ROLE, caller)):
        abi.revert(b"TOKEN:NOT_MINTER")

    _mint_to(to, amount)
    events.emit(EVT_TRANSFER, {b"from": ZERO_ADDR, b"to": to, b"value": amount})
    return True


def role_burn_from(caller: bytes, owner: bytes, amount: int) -> bool:
    """
    Burn `amount` from `owner`.

    - If `caller` holds BURNER_ROLE (or is the token owner), the burn bypasses allowance.
    - Otherwise, this behaves like standard `burn_from` and requires sufficient allowance.
    """
    require_address(caller)
    require_address(owner)
    require_amount(amount)
    if amount == 0:
        return True

    # Fast path: privileged burner bypasses allowance.
    if _is_owner(caller) or has_role(BURNER_ROLE, caller):
        _burn_balance_only(owner, amount)
        return True

    # Fallback: enforce allowance path by delegating to the base logic.
    # Import here to avoid a circular import at module load time.
    from .fungible import burn_from as base_burn_from  # type: ignore

    return base_burn_from(
        caller, owner, owner, amount
    )  # spender=caller, to=owner (burn-to-zero)


# ------------------------------------------------------------------------------
# Internals (balance+total update, no allowance checks)
# ------------------------------------------------------------------------------


def _burn_balance_only(owner_addr: bytes, amount: int) -> None:
    """
    Reduce `owner_addr` balance and total supply without touching allowances.
    Emits Transfer(owner -> ZERO_ADDR, amount).
    """
    require_address(owner_addr)
    require_amount(amount)

    bal_key = key_balance(owner_addr)
    cur_bal = _get_u256(bal_key)
    if cur_bal < amount:
        abi.revert(b"TOKEN:INSUFFICIENT_BALANCE")

    # owner balance
    _set_u256(bal_key, u256_sub(cur_bal, amount))
    # total supply
    _set_u256(K_TOTAL, u256_sub(total_supply(), amount))

    events.emit(
        EVT_TRANSFER,
        {
            b"from": owner_addr,
            b"to": ZERO_ADDR,
            b"value": amount,
        },
    )


__all__ = [
    # role ids
    "MINTER_ROLE",
    "BURNER_ROLE",
    # role checks
    "is_minter",
    "is_burner",
    # admin
    "grant_minter",
    "revoke_minter",
    "grant_burner",
    "revoke_burner",
    # actions
    "role_mint",
    "role_burn_from",
]
