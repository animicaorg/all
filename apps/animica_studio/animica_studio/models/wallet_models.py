"""Wallet domain models for Animica Studio."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from decimal import Decimal, InvalidOperation
from typing import Any


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------

_BECH32M_RE = re.compile(r"^anim1[ac-hj-np-z02-9]{10,}$")


def is_valid_address(addr: str) -> bool:
    """Return True if *addr* is a plausible Animica bech32m address."""
    return bool(_BECH32M_RE.match(addr))


def shorten_address(addr: str, head: int = 8, tail: int = 6) -> str:
    """Return a shortened address like ``anim1abc…xyz123``."""
    if not addr:
        return ""
    if len(addr) <= head + tail + 1:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

_WEI_PER_ANM = 10 ** 18


def format_amount(wei: int, decimals: int = 18) -> str:
    """Format *wei* (raw integer) as a human-readable string.

    Returns e.g. ``"1.234567890123456789 ANM"`` trimming trailing zeros.
    """
    if decimals == 0:
        return f"{wei} ANM"
    divisor = Decimal(10 ** decimals)
    try:
        raw = Decimal(int(wei)) / divisor
    except (InvalidOperation, TypeError):
        return f"{wei} (raw)"
    # Format with enough precision, strip trailing zeros
    formatted = f"{raw:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{formatted} ANM"


def format_amount_compact(wei: int, decimals: int = 18, max_frac: int = 6) -> str:
    """Format *wei* similar to the extension wallet balance display.

    Shows at most ``max_frac`` decimal places, trimming trailing zeros,
    and keeps the ``ANM`` suffix.
    """
    if decimals <= 0:
        return f"{int(wei)} ANM"

    n = int(wei)
    neg = n < 0
    abs_n = -n if neg else n
    base = 10 ** decimals
    whole = abs_n // base
    frac = abs_n % base

    frac_str = str(frac).rjust(decimals, "0").rstrip("0")
    if len(frac_str) > max_frac:
        frac_str = frac_str[:max_frac].rstrip("0")

    number = f"{whole}.{frac_str}" if frac_str else str(whole)
    if neg:
        number = f"-{number}"
    return f"{number} ANM"


def parse_amount_to_wei(text: str, decimals: int = 18) -> int:
    """Parse a human-readable amount string into raw integer wei.

    Accepts:
    * ``"1.5"`` → 1.5 * 10^decimals
    * ``"100"`` → 100 * 10^decimals

    Raises
    ------
    ValueError
        If *text* cannot be parsed or results in a negative value.
    """
    cleaned = text.strip().upper().replace(",", ".")
    # Remove unit suffix if present
    for suffix in (" ANM", "ANM"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()

    try:
        val = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {text!r}") from exc

    if val < 0:
        raise ValueError(f"Amount must not be negative: {text!r}")

    multiplier = Decimal(10 ** decimals)
    wei_dec = val * multiplier
    if wei_dec != wei_dec.to_integral_value():
        raise ValueError(
            f"Amount {text!r} has more than {decimals} decimal places"
        )
    return int(wei_dec)


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------


@dataclass
class Account:
    """A watched/managed wallet account."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    label: str = "Account"
    address: str = ""
    created_ts: float = field(default_factory=time.time)
    last_used_ts: float = field(default_factory=time.time)
    sig_scheme: str = "dilithium3"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "address": self.address,
            "created_ts": self.created_ts,
            "last_used_ts": self.last_used_ts,
            "sig_scheme": self.sig_scheme,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Account":
        return cls(
            id=str(d.get("id", str(uuid.uuid4()))),
            label=str(d.get("label", "Account")),
            address=str(d.get("address", "")),
            created_ts=float(d.get("created_ts", time.time())),
            last_used_ts=float(d.get("last_used_ts", time.time())),
            sig_scheme=str(d.get("sig_scheme", "dilithium3")),
        )


# ---------------------------------------------------------------------------
# BalanceState
# ---------------------------------------------------------------------------



class BalanceSource(str, Enum):
    RPC = "rpc"
    EXPLORER = "explorer"

@dataclass
class BalanceState:
    """Per-address balance snapshot."""

    address: str
    balance_wei: int = 0
    formatted: str = "—"
    updated_ts: float = field(default_factory=time.time)
    error: str | None = None
    source: BalanceSource | None = None
    is_stale: bool = False
    tooltip: str | None = None

    def is_ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# PendingTx
# ---------------------------------------------------------------------------


@dataclass
class PendingTx:
    """A locally-tracked pending transaction."""

    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    from_addr: str = ""
    to_addr: str = ""
    amount_wei: int = 0
    nonce: int = 0
    fee: int = 0
    memo: str | None = None
    raw_tx_hex: str | None = None
    tx_hash: str | None = None
    status: str = "CREATED"  # CREATED / SENT / PENDING / CONFIRMED / FAILED
    error: str | None = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_id": self.local_id,
            "from_addr": self.from_addr,
            "to_addr": self.to_addr,
            "amount_wei": self.amount_wei,
            "nonce": self.nonce,
            "fee": self.fee,
            "memo": self.memo,
            "raw_tx_hex": self.raw_tx_hex,
            "tx_hash": self.tx_hash,
            "status": self.status,
            "error": self.error,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PendingTx":
        return cls(
            local_id=str(d.get("local_id", str(uuid.uuid4()))),
            from_addr=str(d.get("from_addr", "")),
            to_addr=str(d.get("to_addr", "")),
            amount_wei=int(d.get("amount_wei", 0)),
            nonce=int(d.get("nonce", 0)),
            fee=int(d.get("fee", 0)),
            memo=d.get("memo"),
            raw_tx_hex=d.get("raw_tx_hex"),
            tx_hash=d.get("tx_hash"),
            status=str(d.get("status", "CREATED")),
            error=d.get("error"),
            created_ts=float(d.get("created_ts", time.time())),
            updated_ts=float(d.get("updated_ts", time.time())),
        )
