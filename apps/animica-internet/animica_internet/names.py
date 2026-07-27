"""
Name reservation — the in-app "pay ANM to reserve a .anm site name" flow.

The reservation fee is paid ON-CHAIN to the Animica Foundation address (config.FOUNDATION_ADDRESS),
carrying a memo that binds the payment to the exact name+term so it can't be replayed for another
name. Once the payment is broadcast, the app calls the registry's /names/reserve endpoint, which
re-verifies the on-chain payment before registering the name to the payer.

  validate_name(name)            -> None | raises ReserveError
  reservation_quote(name, years) -> {name, years, feeAnm, feeNanm, foundation}
  reserve_memo(name, years)      -> str  (also enforced server-side)
  reserve(wallet, reg, name, ...)-> {txid, name, ...}  (pays foundation, then reserves)

Pure logic + a thin orchestration; the payment/registry calls are injected so it unit-tests
without a chain or a server.
"""

from __future__ import annotations

import re

from .config import (FOUNDATION_ADDRESS, NAME_RE, NANM_PER_ANM, RESERVED_NAMES,
                     registration_fee_anm)

_NAME_RE = re.compile(NAME_RE)
MEMO_PREFIX = "anmreserve"


class ReserveError(RuntimeError):
    pass


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


def reserve_memo(name: str, years: int) -> str:
    """Binds an on-chain payment to a specific name+term (server enforces the same string)."""
    return f"{MEMO_PREFIX}:{normalize(name)}:{int(years)}"


def memo_to_data_hex(memo: str) -> str:
    return "0x" + memo.encode("utf-8").hex()


def reserve(wallet, reg, name: str, *, years: int = 1, kind: str = "app",
            address: str | None = None) -> dict:
    """Pay the Foundation the reservation fee (with a name-bound memo), then reserve the name.

    `wallet` = animica_internet.wallet.Wallet, `reg` = registry_client.RegistryClient.
    Returns {name, years, feeAnm, txid, reservation}. Raises ReserveError on any failure."""
    q = reservation_quote(name, years)
    addr = address or wallet.primary_address()
    memo = reserve_memo(q["name"], q["years"])
    try:
        res = wallet.send(FOUNDATION_ADDRESS, q["feeNanm"], from_address=addr,
                          data_hex=memo_to_data_hex(memo))
    except Exception as e:  # noqa: BLE001
        raise ReserveError(f"payment to the Foundation failed: {e}") from e
    txid = res.get("tx_hash") or res.get("txid") or res.get("hash")
    if not txid:
        raise ReserveError(f"payment sent but no tx id returned: {res}")
    try:
        reservation = reg.reserve(q["name"], years=q["years"], address=addr,
                                  payment_txid=txid, kind=kind)
    except Exception as e:  # noqa: BLE001
        # Payment is on-chain; reservation can be retried with the same txid (idempotent server-side).
        raise ReserveError(f"paid {q['feeAnm']} ANM (tx {txid}) but reserve call failed: {e}. "
                           f"Retry from 'My names' — the payment is not lost.") from e
    return {"name": q["name"], "years": q["years"], "feeAnm": q["feeAnm"],
            "txid": txid, "reservation": reservation}
