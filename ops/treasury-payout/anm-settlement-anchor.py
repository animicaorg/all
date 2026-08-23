#!/usr/bin/env python3
"""Post an ANMSETL1 settlement anchor when inference has been used.

WHAT THIS EXISTS FOR
--------------------
Consensus carves 25% of every block's subsidy (75 ANM) away from the miner as
"inference money". Where it lands is decided by whether that block contains a
settlement anchor:

    no anchor  ->  the whole 75 ANM rolls to the foundation treasury
    anchor     ->  the whole 75 ANM goes to the claiming providers, pro-rata

For the first 411 blocks after activation it always went to the treasury, not
by policy but because `animica tx send` had no way to attach payload data, so an
anchor could not be constructed at all. That was fixed in 10.2.7 (`--data`).

THE RULE THIS IMPLEMENTS: anchor when inference is used. If providers earned
anything since the last anchor, post one. If nothing was served, post nothing and
the carve rolls to the treasury, which is the correct fallback.

READ THIS BEFORE CHANGING ANYTHING
----------------------------------
1. ANCHOR AMOUNTS ARE WEIGHTS, NOT INVOICES. `split_carve` scales the entries UP
   to consume the entire carve:  extra = (residual * amt) // paid. A provider
   ALWAYS receives more than it asked for, and there is no such thing as a small
   anchor — every anchored block moves exactly 75 ANM. Only the RATIOS matter.

2. THE CHAIN'S "PENDING" COUNTER IS CREDIT-ONLY. `earnings_pending_animica`
   accumulates and is never debited by settlement, and `earnings_paid_animica`
   stays 0. So "pending" does NOT mean unpaid. This job therefore keeps its OWN
   state of what it has already anchored and nets that out. Trusting the chain
   counter would re-anchor the same debt forever.

3. THE BLOCK-IMPORT LOG UNDERCOUNTS. "settled N nANM across 1 account" reports
   anchor-derived claimants before the treasury residual is appended, so it reads
   "1" even on a fully-claimed block. Judge success from the STATE_CREDIT lines,
   not that string.

Refuses to run unless --send is passed. Default is a dry-run proposal.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import Dict, List, Tuple

sys.path.insert(0, "/root/animica-mainnet-601")
from consensus.iou_settlement import (  # noqa: E402
    MAX_ENTRIES_PER_ANCHOR, _decode_payout_address, encode_anchor_payload,
    parse_anchor_payload)

TREASURY = os.environ.get(
    "ANIMICA_TREASURY_ADDRESS",
    "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga")
JOBS_DB = os.environ.get(
    "ANIMICA_AICF_JOBS_DB",
    "/var/lib/docker/volumes/animica_mainnet_chain_1_31ae91ca_data/_data/aicf_jobs.db")
STATE = os.environ.get("ANIMICA_ANCHOR_STATE",
                       "/var/lib/animica-ai/settlement-anchor-state.json")
ANIMICA = os.environ.get("ANIMICA_BIN", "/root/animica/.venv/bin/animica")
NANM = 1_000_000_000
# 25% of the 300 ANM block subsidy. Every anchored block moves exactly this,
# split pro-rata by the anchor's weights, regardless of what was claimed.
CARVE_NANM = 75 * NANM
MIN_INTERVAL_S = int(os.environ.get("ANIMICA_ANCHOR_MIN_INTERVAL_S", "300"))
MIN_NEW_EARNINGS_NANM = int(os.environ.get("ANIMICA_ANCHOR_MIN_NEW_NANM", "1000"))


def load_state() -> Dict:
    """Load anchored-so-far state. FAILS CLOSED: an unreadable state file must
    never be treated as 'nothing anchored yet', or the same debt is re-anchored."""
    if not os.path.exists(STATE):
        return {"anchored_nanm": {}, "last_anchor_ts": 0, "anchors": 0}
    try:
        with open(STATE, "r") as fh:
            s = json.load(fh)
        if not isinstance(s.get("anchored_nanm"), dict):
            raise ValueError("malformed anchored_nanm")
        return s
    except Exception as exc:
        raise SystemExit(f"REFUSING TO RUN: state file {STATE} unreadable ({exc}). "
                         "Fix or remove it deliberately — running blind would "
                         "re-anchor debt that was already settled.")


def save_state(s: Dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(s, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def read_earnings() -> Dict[str, int]:
    """Provider -> lifetime earned, in nANM, from the chain's AICF ledger."""
    con = sqlite3.connect(f"file:{JOBS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out: Dict[str, int] = {}
    for r in con.execute(
            "SELECT address, earnings_pending_animica FROM workers "
            "WHERE earnings_pending_animica > 0"):
        addr = str(r["address"] or "").strip()
        if addr:
            out[addr] = int(round(float(r["earnings_pending_animica"]) * NANM))
    con.close()
    return out


def compute_outstanding(earned: Dict[str, int], state: Dict) -> List[Tuple[str, int]]:
    anchored = state.get("anchored_nanm", {})
    rows = []
    for addr, total in earned.items():
        owed = total - int(anchored.get(addr, 0))
        if owed <= 0:
            continue
        # Workers register with ANY string as their payout identity; consensus only pays
        # real bech32m anim1… addresses, and encode_anchor_payload hard-fails on the first
        # bad one. One garbage registration must never block EVERYONE's settlement (that
        # crash-looped this service on 2026-08-23) — skip and warn instead. The invalid
        # identity keeps its IOU on the ledger; it simply can never be paid out.
        if _decode_payout_address(addr) is None:
            print(f"skipping unpayable worker identity {addr!r} "
                  f"({owed / NANM:.9f} ANM owed — not a bech32m anim1 address)")
            continue
        rows.append((addr, owed))
    rows.sort(key=lambda kv: -kv[1])
    return rows[:MAX_ENTRIES_PER_ANCHOR]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually broadcast (default: propose only)")
    ap.add_argument("--force", action="store_true",
                    help="ignore the minimum interval between anchors")
    args = ap.parse_args()

    state = load_state()
    earned = read_earnings()
    outstanding = compute_outstanding(earned, state)

    if not outstanding:
        print("no new inference since the last anchor — nothing to post "
              "(carve rolls to treasury, which is correct)")
        return 0

    total = sum(a for _, a in outstanding)
    if total < MIN_NEW_EARNINGS_NANM:
        print(f"new earnings {total/NANM:.9f} ANM below floor "
              f"{MIN_NEW_EARNINGS_NANM/NANM:.9f} — holding")
        return 0

    since = time.time() - float(state.get("last_anchor_ts") or 0)
    if since < MIN_INTERVAL_S and not args.force:
        print(f"last anchor {since:.0f}s ago (< {MIN_INTERVAL_S}s) — holding")
        return 0

    payload = encode_anchor_payload(outstanding)
    if parse_anchor_payload(payload) is None:
        raise SystemExit("REFUSING: encoded anchor failed strict re-parse")

    print(f"claimants        : {len(outstanding)}")
    print(f"new earnings     : {total/NANM:.9f} ANM  (these are WEIGHTS)")
    print(f"payload          : {len(payload)} bytes")
    print("carve moved      : 75.000000 ANM (the whole carve, always)")
    print("projected split  :")
    for addr, amt in outstanding[:8]:
        print(f"   {addr[:40]}…  {75*amt/total:>12.6f} ANM")
    if len(outstanding) > 8:
        print(f"   … and {len(outstanding)-8} more")

    if not args.send:
        print("\n(dry run — pass --send to broadcast)")
        return 0

    cmd = [ANIMICA, "tx", "send", "--from", TREASURY, "--to", TREASURY,
           "--value-nanm", "1", "--data", "0x" + payload.hex()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    ok = res.returncode == 0 and "rejected" not in (res.stdout + res.stderr).lower()
    print(f"\nbroadcast: {'OK' if ok else 'FAILED'}")
    if not ok:
        print((res.stdout + res.stderr)[-600:])
        return 1

    # Credit what each provider will actually RECEIVE, not what it claimed.
    # split_carve scales entries up to consume the whole carve, so a provider
    # gets carve*amt/total — roughly 2.9x its claim at current volumes. Crediting
    # the claim instead would leave most of the debt standing after it had in fact
    # been paid, and the next run would pay it all over again.
    anchored = state.setdefault("anchored_nanm", {})
    for addr, amt in outstanding:
        received = (CARVE_NANM * amt) // total
        anchored[addr] = int(anchored.get(addr, 0)) + received
    state["last_anchor_ts"] = time.time()
    state["anchors"] = int(state.get("anchors", 0)) + 1
    save_state(state)
    print(f"state updated — {len(outstanding)} providers marked anchored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
