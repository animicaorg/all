"""
Name reservation — the in-app "pay ANM to reserve a .anm site name" flow.

SECURITY NOTE. The chain today commits a zero state root (state-commitment armed but inert), so
tx receipts carry status=null: INCLUSION != EXECUTION. A raw wallet payment to the Foundation
therefore CANNOT be trusted as proof of value moved (an included-but-reverted transfer is
indistinguishable from a real one — the phantom-deposit class). So reservation does NOT verify a
loose on-chain tx. Instead the fee is debited from the buyer's marketplace balance (which is
funded via unique-address deposits with 12-conf balance-delta finality — the audited safe path)
and credited to the Foundation ledger account server-side (lib/ledger.payNameFee). The Foundation
withdraws its accrued balance on-chain. Net effect: reservation ANM routes to the Foundation,
safely.

  validate_name(name)            -> str | raises ReserveError
  reservation_quote(name, years) -> {name, years, feeAnm, feeNanm, foundation}
  reserve(wallet, reg, name, ...) -> {name, years, feeAnm, domain}  (login + register)

Pure logic + thin orchestration; the wallet/registry calls are injected so it unit-tests without
a chain or a server.
"""

from __future__ import annotations

import re

from .config import (FOUNDATION_ADDRESS, NAME_RE, NANM_PER_ANM, RESERVED_NAMES,
                     registration_fee_anm)

_NAME_RE = re.compile(NAME_RE)


class ReserveError(RuntimeError):
    pass


class InsufficientBalance(ReserveError):
    """The marketplace balance can't cover the fee — the caller should fund it (deposit ANM)."""

    def __init__(self, message: str, *, deposit_address: str = "", fee_anm: int = 0):
        super().__init__(message)
        self.deposit_address = deposit_address
        self.fee_anm = fee_anm


def normalize(name: str) -> str:
    s = (name or "").strip().lower()
    if s.endswith(".anm"):
        s = s[:-4]
    return s


def validate_name(name: str) -> str:
    s = normalize(name)
    if not (2 <= len(s) <= 63):
        raise ReserveError("name must be 2–63 characters")
    if not _NAME_RE.match(s):
        raise ReserveError("only a–z, 0–9 and internal hyphens are allowed")
    if "--" in s:
        raise ReserveError("consecutive hyphens are not allowed")
    if s in RESERVED_NAMES:
        raise ReserveError(f"'{s}' is reserved")
    return s


def reservation_quote(name: str, years: int = 1) -> dict:
    s = validate_name(name)
    years = max(1, min(10, int(years)))
    fee_anm = registration_fee_anm(s, years)
    return {
        "name": s, "years": years,
        "feeAnm": fee_anm, "feeNanm": fee_anm * NANM_PER_ANM,
        "foundation": FOUNDATION_ADDRESS,
    }


def _is_insufficient_funds(err: Exception) -> bool:
    s = str(err).lower()
    return "insufficient_funds" in s or "insufficient funds" in s or "402" in s


def reserve(wallet, reg, name: str, *, years: int = 1, kind: str = "app",
            address: str | None = None) -> dict:
    """Reserve a name: authenticate with the wallet, then register it. The fee is debited from
    the buyer's marketplace balance and credited to the Foundation server-side.

    `wallet` = animica_internet.wallet.Wallet, `reg` = registry_client.RegistryClient.
    Returns {name, years, feeAnm, domain}. Raises InsufficientBalance (with the deposit address)
    when the balance can't cover the fee, or ReserveError on any other failure."""
    q = reservation_quote(name, years)
    try:
        reg.login(wallet, address=address)
    except Exception as e:  # noqa: BLE001
        raise ReserveError(f"could not authenticate with the registry: {e}") from e
    try:
        res = reg.register(q["name"], years=q["years"], kind=kind)
    except Exception as e:  # noqa: BLE001
        if _is_insufficient_funds(e):
            dep = ""
            try:
                dep = reg.deposit_address()
            except Exception:  # noqa: BLE001
                pass
            raise InsufficientBalance(
                f"reserving {q['name']}.anm costs {q['feeAnm']} ANM — your marketplace balance is "
                f"too low. Fund it by sending ANM to your deposit address, then reserve again.",
                deposit_address=dep, fee_anm=q["feeAnm"]) from e
        raise ReserveError(f"reservation failed: {e}") from e
    return {"name": q["name"], "years": q["years"], "feeAnm": q["feeAnm"],
            "domain": res.get("domain") or res}


def fund_balance(wallet, reg, amount_anm: int, *, address: str | None = None) -> dict:
    """'Pay in the browser': send ANM from the wallet to the buyer's marketplace deposit address
    to top up the balance that reservation fees are drawn from. Returns {txid, depositAddress}."""
    dep = reg.deposit_address()
    if not dep:
        raise ReserveError("could not obtain a deposit address")
    res = wallet.send(dep, int(amount_anm) * NANM_PER_ANM, from_address=address)
    txid = res.get("tx_hash") or res.get("txid") or res.get("hash")
    return {"txid": txid, "depositAddress": dep}
