#!/usr/bin/env python3
"""
Animica treasury auto-staker — bonds ANM arriving in a wallet, block by block.

Watches the chain head and, whenever it advances, bonds whatever spendable
balance sits above a reserve. Runs on the host and shells out to `animica
stake`, so it never handles the secret key itself — the CLI loads it at
signing time, exactly as a human running the same command would.

THE RESERVE IS NOT OPTIONAL. The treasury also funds the academy payout worker
(10 ANM per lesson, capped at 10,000 ANM/day) and transaction fees. Staking the
balance down to zero would lock those coins for the bond term and make every
payout fail with "insufficient balance" — a failure that looks like a bug in the
academy, not like a staking decision. The default reserve is deliberately larger
than a day of payouts.

Environment
-----------
  ANIMICA_RPC_URL                node RPC (default http://127.0.0.1:8545/rpc)
  ANIMICA_AUTOSTAKE_ADDRESS      wallet to stake from (required)
  ANIMICA_AUTOSTAKE_RESERVE_ANM  keep this much spendable (default 50000)
  ANIMICA_AUTOSTAKE_MIN_ANM      don't bond less than this in one go (default 100)
  ANIMICA_AUTOSTAKE_DAYS         bond term in days (default 365)
  ANIMICA_AUTOSTAKE_NAME         display label for the staker (default "treasury")
  ANIMICA_AUTOSTAKE_POLL_S       head poll interval (default 30)
  ANIMICA_AUTOSTAKE_DRY_RUN=1    log what it would bond; never submit
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from decimal import Decimal
from typing import Optional
from urllib import request as urlrequest

RPC_URL = os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
ADDRESS = os.environ.get("ANIMICA_AUTOSTAKE_ADDRESS", "").strip()
RESERVE_ANM = Decimal(os.environ.get("ANIMICA_AUTOSTAKE_RESERVE_ANM", "50000"))
MIN_ANM = Decimal(os.environ.get("ANIMICA_AUTOSTAKE_MIN_ANM", "100"))
DAYS = int(os.environ.get("ANIMICA_AUTOSTAKE_DAYS", "365"))
NAME = os.environ.get("ANIMICA_AUTOSTAKE_NAME", "treasury")
POLL_S = float(os.environ.get("ANIMICA_AUTOSTAKE_POLL_S", "30"))
DRY_RUN = os.environ.get("ANIMICA_AUTOSTAKE_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}
RPC_TIMEOUT = float(os.environ.get("ANIMICA_AUTOSTAKE_RPC_TIMEOUT", "120"))
CLI = os.environ.get("ANIMICA_AUTOSTAKE_CLI", "/root/animica/.venv/bin/animica")

# After submitting, wait for the balance to actually move before bonding again.
# Without this the bot would re-bond the same coins every poll while its own
# transaction is still in the mempool, emptying the reserve in a few blocks.
SETTLE_GRACE_S = float(os.environ.get("ANIMICA_AUTOSTAKE_SETTLE_S", "180"))

# MINIMUM SPACING BETWEEN BONDS — this is a correctness guard, not a preference.
# core.staking.MAX_BONDS_PER_STAKER caps a staker at 64 distinct bonds, and two
# bonds only merge when their unlock times are identical. Every bond here takes
# its unlock from the including block's timestamp, so consecutive bonds never
# merge. Bonding on every block would therefore reach the cap in about an hour,
# after which EVERY stake transaction fails with "too many bonds" until a bond
# matures — which, on a 365-day term, is a year away.
#
# At the default (daily) a 365-day term still accumulates ~365 bonds over its
# life and will hit the cap around day 64. Either widen the interval, shorten
# ANIMICA_AUTOSTAKE_DAYS so bonds mature and can be withdrawn, or plan to
# consolidate. `animica stake status` shows the live bond count.
BOND_INTERVAL_S = float(os.environ.get("ANIMICA_AUTOSTAKE_INTERVAL_S", "86400"))
MAX_BONDS = 64

log = logging.getLogger("animica.autostake")


def rpc(method: str, params) -> Optional[object]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urlrequest.Request(
        RPC_URL, data=body.encode(), headers={"content-type": "application/json"}
    )
    try:
        with urlrequest.urlopen(req, timeout=RPC_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except (OSError, ValueError) as exc:
        log.info("rpc %s unavailable: %s", method, str(exc)[:120])
        return None
    if isinstance(payload, dict) and payload.get("error"):
        log.info("rpc %s error: %s", method, str(payload["error"])[:160])
        return None
    return payload.get("result") if isinstance(payload, dict) else payload


def head_height() -> Optional[int]:
    r = rpc("getblockcount", [])
    try:
        return int(r)
    except (TypeError, ValueError):
        return None


def spendable_anm() -> Optional[Decimal]:
    """Spendable balance in ANM. getbalance returns ANM, not base units."""
    r = rpc("getbalance", [ADDRESS])
    if r is None:
        return None
    try:
        return Decimal(str(r))
    except Exception:
        return None


def bond_count() -> Optional[int]:
    """How many distinct bonds this staker already holds (None if unknown)."""
    r = rpc("stake.get", {"address": ADDRESS})
    if not isinstance(r, dict) or not r.get("available"):
        return None
    try:
        return int(r.get("bondCount") or 0)
    except (TypeError, ValueError):
        return None


def bond(amount_anm: Decimal) -> bool:
    # Trim to 9 decimals — the CLI rejects anything finer than 1 nANM.
    amount = amount_anm.quantize(Decimal("0.000000001"))
    cmd = [
        CLI, "stake", format(amount, "f"), str(DAYS),
        "--name", NAME, "--address", ADDRESS, "--rpc-url", RPC_URL,
    ]
    if DRY_RUN:
        cmd.append("--dry-run")
    log.info("bonding %s ANM for %dd as %r%s", amount, DAYS, NAME,
             " [DRY RUN]" if DRY_RUN else "")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        log.warning("stake command timed out")
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        log.warning("stake failed rc=%d: %s", proc.returncode, out.strip()[:300])
        return False
    for line in out.splitlines():
        if "submitted" in line or "dry run" in line:
            log.info("%s", line.strip()[:200])
    return True


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("ANIMICA_AUTOSTAKE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not ADDRESS:
        log.error("ANIMICA_AUTOSTAKE_ADDRESS is required")
        return 2
    if RESERVE_ANM < 0 or MIN_ANM <= 0:
        log.error("reserve must be >= 0 and minimum bond > 0")
        return 2

    log.info(
        "treasury auto-staker up: address=%s… reserve=%s ANM min-bond=%s ANM "
        "term=%dd name=%r poll=%.0fs%s",
        ADDRESS[:20], RESERVE_ANM, MIN_ANM, DAYS, NAME, POLL_S,
        " [DRY RUN]" if DRY_RUN else "",
    )

    last_height = None
    quiet_until = 0.0
    last_bond_at = 0.0

    while True:
        time.sleep(POLL_S)
        h = head_height()
        if h is None:
            continue
        if h == last_height:
            continue            # only act on a new block
        last_height = h

        if time.time() < quiet_until:
            continue            # a previous bond is still settling
        if last_bond_at and time.time() - last_bond_at < BOND_INTERVAL_S:
            continue            # bond-count guard, see BOND_INTERVAL_S

        bonds_now = bond_count()
        if bonds_now is not None and bonds_now >= MAX_BONDS:
            log.warning(
                "staker already holds %d bonds (cap %d) — not bonding. Withdraw a "
                "matured bond or consolidate; `animica stake status` lists them.",
                bonds_now, MAX_BONDS,
            )
            quiet_until = time.time() + 3600
            continue

        bal = spendable_anm()
        if bal is None:
            continue
        excess = bal - RESERVE_ANM
        if excess < MIN_ANM:
            log.debug("height %d: balance %s ANM, nothing above reserve", h, bal)
            continue

        log.info("height %d: balance %s ANM, %s above the %s reserve (bonds held: %s)",
                 h, bal, excess, RESERVE_ANM,
                 bonds_now if bonds_now is not None else "?")
        if bond(excess):
            last_bond_at = time.time()
            quiet_until = time.time() + SETTLE_GRACE_S


if __name__ == "__main__":
    raise SystemExit(main())
