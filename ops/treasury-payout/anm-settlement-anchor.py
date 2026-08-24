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
import re
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
# The systemd timer cadence — published in the status feed so the Serve & Earn
# page can show an honest countdown to the next payout window.
TIMER_S = int(os.environ.get("ANIMICA_ANCHOR_TIMER_S", "600"))
STATUS_JSON = os.environ.get(
    "ANIMICA_ANCHOR_STATUS_JSON", "/var/www/pool.animica.org/serve-payouts.json")
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


def write_status(state: Dict, *, posted: bool = False, txid: str = "",
                 claimants: int = 0, moved_nanm: int = 0) -> None:
    """Publish the payout cadence for dashboards (pool.animica.org/serve reads
    this to render the countdown). Written after EVERY run — holds included —
    so next_eta always moves. Best-effort."""
    try:
        now = time.time()
        doc = {
            "ts": now,
            "interval_s": TIMER_S,
            "next_eta_ts": now + TIMER_S,
            "last_anchor_ts": float(state.get("last_anchor_ts") or 0),
            "last_anchor_txid": str(state.get("last_anchor_txid") or ""),
            "anchors_total": int(state.get("anchors", 0)),
            "carve_anm": CARVE_NANM / NANM,
            "floor_anm": MIN_NEW_EARNINGS_NANM / NANM,
            "last_run_posted": bool(posted),
            "last_run_claimants": int(claimants),
            "last_run_moved_anm": moved_nanm / NANM,
        }
        tmp = STATUS_JSON + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        os.replace(tmp, STATUS_JSON)
    except Exception as e:  # noqa: BLE001 — the feed must never fail a payout
        print(f"status feed write failed (non-fatal): {e}")


def mirror_paid_to_ledger(state: Dict) -> None:
    """Mirror both ledgers into the node's DB so aicf.workerEarnings reports
    the truth: workers.earnings_paid_animica := cumulative ON-CHAIN ANM
    received, and anchor_ledger.weights_consumed := the weights already paid
    for (the RPC derives earnings_unpaid = pending - weights_consumed from
    it). Column-level / own-table writes only; best-effort — a locked DB just
    means the next run mirrors it."""
    received = state.get("received_nanm") or {}
    consumed = state.get("anchored_nanm") or {}
    try:
        con = sqlite3.connect(JOBS_DB, timeout=5)
        con.execute(
            "CREATE TABLE IF NOT EXISTS anchor_ledger ("
            "  address TEXT PRIMARY KEY,"
            "  weights_consumed_animica REAL NOT NULL DEFAULT 0,"
            "  received_animica REAL NOT NULL DEFAULT 0,"
            "  updated REAL NOT NULL DEFAULT 0)"
        )
        for addr in set(received) | set(consumed):
            r = int(received.get(addr, 0)) / NANM
            w = int(consumed.get(addr, 0)) / NANM
            con.execute(
                "UPDATE workers SET earnings_paid_animica=? WHERE address=?",
                (r, addr),
            )
            con.execute(
                "INSERT INTO anchor_ledger (address, weights_consumed_animica, received_animica, updated) "
                "VALUES (?,?,?,?) ON CONFLICT(address) DO UPDATE SET "
                "  weights_consumed_animica=excluded.weights_consumed_animica,"
                "  received_animica=excluded.received_animica,"
                "  updated=excluded.updated",
                (addr, w, r, time.time()),
            )
        con.commit()
        con.close()
    except Exception as e:  # noqa: BLE001
        print(f"paid-mirror failed (non-fatal, retries next run): {e}")


def migrate_ledger_v2(state: Dict, earned: Dict[str, int]) -> None:
    """One-time switch to WEIGHT-consuming payouts (operator decision 2026-08-24:
    "make the payouts smoother — credit weights instead").

    v1 credited what a provider RECEIVED (its share of the whole 75 ANM carve),
    so a small provider paid one windfall was then overdrawn until it re-earned
    ~75 ANM of weights — one big payout, then a long dry spell. v2 keeps two
    ledgers: `anchored_nanm` now holds consumed WEIGHTS (drives outstanding, so
    new inference is payable again the very next block) and `received_nanm`
    holds cumulative ON-CHAIN ANM (drives the paid figure users see).

    Migration: received := old anchored (those were received amounts); anchored
    := min(old anchored, earned weights) — windfall recipients stop being
    overdrawn, while never-paid backlogs keep their full queued weight."""
    if state.get("ledger_v2"):
        return
    old_anchored = {k: int(v) for k, v in (state.get("anchored_nanm") or {}).items()}
    state["received_nanm"] = dict(old_anchored)
    state["anchored_nanm"] = {
        addr: min(int(v), int(earned.get(addr, 0))) for addr, v in old_anchored.items()
    }
    state["ledger_v2"] = True
    print(f"ledger migrated to v2 (weights-consuming) — {len(old_anchored)} addresses")


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
    migrate_ledger_v2(state, earned)
    outstanding = compute_outstanding(earned, state)

    if not outstanding:
        print("no new inference since the last anchor — nothing to post "
              "(carve rolls to treasury, which is correct)")
        mirror_paid_to_ledger(state)
        save_state(state)
        write_status(state)
        return 0

    total = sum(a for _, a in outstanding)
    if total < MIN_NEW_EARNINGS_NANM:
        print(f"new earnings {total/NANM:.9f} ANM below floor "
              f"{MIN_NEW_EARNINGS_NANM/NANM:.9f} — holding")
        write_status(state)
        return 0

    since = time.time() - float(state.get("last_anchor_ts") or 0)
    if since < MIN_INTERVAL_S and not args.force:
        print(f"last anchor {since:.0f}s ago (< {MIN_INTERVAL_S}s) — holding")
        write_status(state)
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
        write_status(state)
        return 0

    cmd = [ANIMICA, "tx", "send", "--from", TREASURY, "--to", TREASURY,
           "--value-nanm", "1", "--data", "0x" + payload.hex()]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    ok = res.returncode == 0 and "rejected" not in (res.stdout + res.stderr).lower()
    print(f"\nbroadcast: {'OK' if ok else 'FAILED'}")
    if not ok:
        print((res.stdout + res.stderr)[-600:])
        write_status(state)
        return 1
    m_tx = re.search(r"0x[0-9a-fA-F]{64}", res.stdout + res.stderr)
    if m_tx:
        state["last_anchor_txid"] = m_tx.group(0)
        print(f"anchor tx        : {m_tx.group(0)}")

    # v2 (2026-08-24): consume the WEIGHTS, track the RECEIVED ANM separately.
    # Consuming weights keeps payouts smooth — a provider is payable again the
    # next block it earns anything. received_nanm is what actually landed
    # on-chain (each provider's pro-rata share of the whole 75 ANM carve) and
    # is what dashboards show as "paid out".
    anchored = state.setdefault("anchored_nanm", {})
    received_ledger = state.setdefault("received_nanm", {})
    for addr, amt in outstanding:
        received = (CARVE_NANM * amt) // total
        anchored[addr] = int(anchored.get(addr, 0)) + int(amt)
        received_ledger[addr] = int(received_ledger.get(addr, 0)) + received
    state["last_anchor_ts"] = time.time()
    state["anchors"] = int(state.get("anchors", 0)) + 1
    mirror_paid_to_ledger(state)
    write_status(state, posted=True, txid=str(state.get("last_anchor_txid") or ""),
                 claimants=len(outstanding), moved_nanm=CARVE_NANM)
    save_state(state)
    print(f"state updated — {len(outstanding)} providers marked anchored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
