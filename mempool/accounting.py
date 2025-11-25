"""
mempool.accounting
==================

Lightweight accounting helpers used at mempool admission time:

• Intrinsic gas calculation (base + data + create + access-list)
• Effective gas price (gasPrice OR EIP-1559-style maxFee/maxPriority)
• Max-spend estimate (value + gasLimit * effectivePrice)
• Balance & (optional) allowance checks

This module is *pure* and side-effect free. It accepts a minimal state
reader protocol for balances/allowances and raises precise exceptions
when checks fail. It is safe to run before pool insertion.

The functions are defensive: they tolerate slightly different Tx shapes
across drafts (e.g., `gas_price` vs. `{max_fee_per_gas,max_priority_fee_per_gas}`).

Notes
-----
• "Allowance" is optional and only used when the caller supplies a
  `required_allowance` (e.g., when a relay policy enforces token-fee
  allowances). Core protocol does not mandate token checks here.

• Access list accounting follows the EIP-2930 style shape if present:
  each entry is either a dict {"address": ..., "storageKeys": [...]} or
  a tuple (address: bytes/str, storage_keys: list[bytes/hexstr]).

• Gas schedule defaults are chosen to be familiar and conservative:
  - base_tx:            21_000
  - create_extra:       32_000
  - data_zero:          4
  - data_nonzero:       16
  - access_list_addr:   2_400
  - access_list_storage: 1_900

Callers
-------
• mempool ingress path (after stateless shape/sig checks)
• rpc.pending_pool preflight
• tests/benchmarks

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple, Iterable, Any

# -----------------------------
# Optional/guarded imports
# -----------------------------

try:
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover
    class Tx:  # type: ignore
        kind: str
        chain_id: int
        gas_limit: int
        to: Optional[bytes]
        data: bytes
        value: int
        gas_price: Optional[int]
        max_fee_per_gas: Optional[int]
        max_priority_fee_per_gas: Optional[int]
        access_list: Optional[list]

        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

try:
    from core.types.params import ChainParams  # type: ignore
except Exception:  # pragma: no cover
    @dataclass
    class ChainParams:  # type: ignore
        chain_id: int = 1
        block_gas_limit: int = 30_000_000
        tx_max_bytes: int = 1_048_576


# -----------------------------
# State reader protocol
# -----------------------------

class BalanceReader(Protocol):
    """Minimal state view dependency used by this module."""
    def get_balance(self, address: bytes | str) -> int: ...
    def get_allowance(self, owner: bytes | str, spender: bytes | str) -> int: ...


# -----------------------------
# Gas schedule & config
# -----------------------------

@dataclass(frozen=True)
class GasSchedule:
    base_tx: int = 21_000
    create_extra: int = 32_000
    data_zero: int = 4
    data_nonzero: int = 16
    access_list_address: int = 2_400
    access_list_storage: int = 1_900


@dataclass(frozen=True)
class AccountingConfig:
    gas: GasSchedule = GasSchedule()
    # If True, intrinsic_gas must be <= tx.gas_limit, otherwise error
    enforce_intrinsic_leq_limit: bool = True


# -----------------------------
# Errors
# -----------------------------

class AccountingError(Exception):
    """Raised when balance/allowance/gas accounting checks fail."""
    code: str

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(msg)
        self.code = code


# -----------------------------
# Public API
# -----------------------------

@dataclass(frozen=True)
class SpendEstimate:
    intrinsic_gas: int
    gas_limit: int
    effective_gas_price: int
    max_fee_paid: int
    value: int
    total_max_spend: int


def intrinsic_gas(tx: "Tx", cfg: AccountingConfig | None = None) -> int:
    """
    Compute the intrinsic gas for a transaction based on its payload
    and kind. No state access.

    Returns:
        Integer gas units.

    Raises:
        AccountingError if enforcement is enabled and intrinsic > gas_limit.
    """
    cfg = cfg or AccountingConfig()
    g = cfg.gas

    kind = (getattr(tx, "kind", "") or "").lower()
    data = bytes(getattr(tx, "data", b"") or b"")
    gas_limit = int(getattr(tx, "gas_limit", 0) or 0)

    zero, nonzero = _count_zero_nonzero(data)
    base = g.base_tx + zero * g.data_zero + nonzero * g.data_nonzero

    if kind in ("deploy", "create"):
        base += g.create_extra

    # Access list (EIP-2930 style)
    al = getattr(tx, "access_list", None)
    if al:
        addr_count, slot_count = _access_list_counts(al)
        base += addr_count * g.access_list_address + slot_count * g.access_list_storage

    if cfg.enforce_intrinsic_leq_limit and gas_limit and base > gas_limit:
        raise AccountingError(
            "IntrinsicGasTooHigh",
            f"intrinsic gas {base} exceeds tx.gas_limit {gas_limit}",
        )
    return base


def effective_gas_price(tx: "Tx", *, base_fee: int = 0) -> int:
    """
    Derive the effective gas price the sender is willing to pay.

    Priority rules:
      1) If `gas_price` present, return it.
      2) Else if EIP-1559 style caps, return min(max_fee_per_gas,
         base_fee + max_priority_fee_per_gas).
      3) Else 0.
    """
    gp = getattr(tx, "gas_price", None)
    if gp is not None:
        return int(gp)

    max_fee = getattr(tx, "max_fee_per_gas", None)
    max_tip = getattr(tx, "max_priority_fee_per_gas", None)
    if max_fee is not None and max_tip is not None:
        mf = int(max_fee)
        tip = int(max_tip)
        return min(mf, base_fee + tip)

    return 0


def estimate_max_spend(
    tx: "Tx",
    *,
    base_fee: int = 0,
    cfg: AccountingConfig | None = None,
) -> SpendEstimate:
    """
    Estimate the maximum spend for a transaction:
        total = value + gas_limit * effective_gas_price

    Returns a SpendEstimate with all components for logging/UX.

    Does *not* check balances; see `assert_affordable(...)`.
    """
    cfg = cfg or AccountingConfig()
    ig = intrinsic_gas(tx, cfg=cfg)
    gas_limit = int(getattr(tx, "gas_limit", 0) or 0)
    price = effective_gas_price(tx, base_fee=base_fee)
    max_fee_paid = gas_limit * price

    value = int(getattr(tx, "value", getattr(tx, "amount", 0)) or 0)
    total = value + max_fee_paid

    return SpendEstimate(
        intrinsic_gas=ig,
        gas_limit=gas_limit,
        effective_gas_price=price,
        max_fee_paid=max_fee_paid,
        value=value,
        total_max_spend=total,
    )


def assert_affordable(
    state: BalanceReader,
    sender: bytes | str,
    tx: "Tx",
    *,
    base_fee: int = 0,
    cfg: AccountingConfig | None = None,
) -> SpendEstimate:
    """
    Check that `sender` has enough balance to cover the worst-case
    fee + value burn for `tx`.

    Returns:
        SpendEstimate (also computed as part of the checks)

    Raises:
        AccountingError("InsufficientFunds", ...)
    """
    est = estimate_max_spend(tx, base_fee=base_fee, cfg=cfg)
    bal = int(state.get_balance(sender))
    if bal < est.total_max_spend:
        raise AccountingError(
            "InsufficientFunds",
            f"balance {bal} < required {est.total_max_spend} "
            f"(value={est.value}, max_fee={est.max_fee_paid})",
        )
    return est


def assert_allowance(
    state: BalanceReader,
    owner: bytes | str,
    spender: bytes | str,
    required_allowance: int,
) -> None:
    """
    Optional allowance check used by policies that require token approvals
    (e.g., fee credits). No effect if not needed by your deployment.

    Raises:
        AccountingError("AllowanceTooLow", ...)
    """
    if required_allowance <= 0:
        return
    try:
        allowance = int(state.get_allowance(owner, spender))
    except AttributeError:
        raise AccountingError("NoAllowanceAPI", "State does not support allowances")
    if allowance < required_allowance:
        raise AccountingError(
            "AllowanceTooLow",
            f"allowance {allowance} < required {required_allowance}",
        )


# -----------------------------
# Internal helpers
# -----------------------------

def _count_zero_nonzero(b: bytes) -> Tuple[int, int]:
    if not b:
        return (0, 0)
    zero = b.count(0)
    return zero, len(b) - zero


def _access_list_counts(al: Iterable[Any]) -> Tuple[int, int]:
    """
    Return (#addresses, #storage_slots). Accepts:
      - [{"address": "...", "storageKeys": ["..", ".."]}, ...]
      - [(addr, [slot, ...]), ...]
    """
    addr_count = 0
    slot_count = 0
    for entry in al:
        if isinstance(entry, dict):
            addr_count += 1
            slots = entry.get("storageKeys") or entry.get("storage_keys") or []
            slot_count += len(list(slots))
        else:
            # Assume tuple-like
            try:
                _, slots = entry
                addr_count += 1
                slot_count += len(list(slots))
            except Exception:
                # Best-effort: count as one address, zero slots
                addr_count += 1
    return addr_count, slot_count
