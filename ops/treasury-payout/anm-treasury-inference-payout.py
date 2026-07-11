#!/usr/bin/env python3
"""Treasury inference-reward payout — settles registered workers' on-chain
earnings from the treasury wallet (on-chain settlement is dormant).

The chain tracks, per registered AICF worker, its unsettled earnings
(`aicf.workerEarnings` → earnings_pending_animica, "settlement_pending_..."). This
job pays that from the treasury via `animica tx send`, tracking what we have
already CONFIRMED-paid so it only ever pays the delta.

SAFE BY CONSTRUCTION:
  * Only pays wallets returned by `aicf.work.listWorkers` (registered on-chain with
    completed jobs). An address that merely appears somewhere is never paid.
  * Dedups per wallet; owed = on-chain pending earnings − already-confirmed-paid.
  * CONFIRMATION-AWARE: a payment is only counted as paid once its tx confirms in a
    block. While a wallet has an unconfirmed payment in flight it is never paid
    again (no double-pay); a payment that never confirms simply blocks further pay
    to that wallet until it clears (no lost double-spend).
  * Treasury floor, per-run cap, per-worker per-run cap, minimum payout.
  * Optional reputation / failure filters.

`--dry-run` prints the proposal without sending.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

STATE    = os.environ.get("ANIMICA_PAYOUT_STATE", "/var/lib/animica-ai/treasury-payout-state.json")
TREASURY = os.environ.get("ANIMICA_TREASURY_ADDRESS", "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga")
RPC      = os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
ANIMICA  = os.environ.get("ANIMICA_BIN", "/root/animica/.venv/bin/animica")
PER_RUN_CAP    = int(os.environ.get("ANIMICA_PAYOUT_PER_RUN_CAP_NANM", "100000000000"))
PER_WORKER_CAP = int(os.environ.get("ANIMICA_PAYOUT_PER_WORKER_CAP_NANM", "10000000000"))
TREASURY_FLOOR = int(os.environ.get("ANIMICA_TREASURY_FLOOR_NANM", "1000000000000"))
MIN_PAYOUT     = int(os.environ.get("ANIMICA_MIN_PAYOUT_NANM", "1000000"))
MIN_REPUTATION = int(os.environ.get("ANIMICA_PAYOUT_MIN_REPUTATION", "0"))
MAX_FAIL_RATIO = float(os.environ.get("ANIMICA_PAYOUT_MAX_FAIL_RATIO", "0.5"))
NANM = 10 ** 9
DRY_RUN = "--dry-run" in sys.argv
_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json", "Host": "rpc.animica.org"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    if d.get("error"):
        raise RuntimeError(d["error"])
    return d["result"]


def load():
    try:
        s = json.load(open(STATE))
    except Exception:
        s = {}
    s.setdefault("paid", {})       # wallet -> cumulative nANM CONFIRMED paid
    s.setdefault("pending", {})    # wallet -> {"tx": hash, "amt": nanm, "at": ts}
    return s


def save(s):
    d = os.path.dirname(STATE)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"))
    os.replace(tmp, STATE)


def balance(addr):
    v = rpc("state.getBalance", [addr])
    return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v)


def tx_confirmed(txh):
    """True = confirmed in a block, False = still pending/unknown."""
    try:
        t = rpc("tx.getTransaction", [txh])
    except Exception:
        return False
    if not t:
        return False
    for k in ("blockHash", "block_hash", "blockNumber", "block_number", "height", "confirmations"):
        v = t.get(k)
        if v not in (None, "", 0, "0x0"):
            return True
    return bool(t.get("confirmed") or t.get("status") == "confirmed")


def main():
    st = load()

    # 1) reconcile in-flight payments: confirm → move to paid; else leave pending
    for wallet, p in list(st["pending"].items()):
        if tx_confirmed(p.get("tx", "")):
            st["paid"][wallet] = int(st["paid"].get(wallet, 0)) + int(p.get("amt", 0))
            del st["pending"][wallet]
            print(f"confirmed {p.get('amt')} nANM -> {wallet}")
    if not DRY_RUN:
        save(st)

    try:
        ws = rpc("aicf.work.listWorkers", {}).get("workers", [])
    except Exception as e:
        print("listWorkers failed:", e); return
    if not ws:
        print("no registered workers on-chain"); return

    agg = {}
    for w in ws:
        addr = str(w.get("wallet_address") or "").strip()
        if not addr.startswith("anim1") or addr == TREASURY:
            continue
        a = agg.setdefault(addr, {"completed": 0, "failed": 0, "rep": 0})
        a["completed"] += int(w.get("completed_jobs") or 0)
        a["failed"] += int(w.get("failed_jobs") or 0)
        a["rep"] = max(a["rep"], int(w.get("reputation_score") or 0))

    candidates = []
    for addr, a in agg.items():
        if addr in st["pending"]:
            continue  # payment in flight — never double-submit
        if a["completed"] <= 0 or a["rep"] < MIN_REPUTATION:
            continue
        tot = a["completed"] + a["failed"]
        if tot > 0 and a["failed"] / tot > MAX_FAIL_RATIO:
            continue
        try:
            e = rpc("aicf.workerEarnings", {"address": addr})
            earned = int(round(float(e.get("earnings_pending_animica") or 0) * NANM))
        except Exception:
            continue
        owed = min(max(0, earned - int(st["paid"].get(addr, 0))), PER_WORKER_CAP)
        if owed >= MIN_PAYOUT:
            candidates.append((addr, owed, a["completed"]))

    if not candidates:
        print(f"{len(agg)} wallets; nothing owed above min (in-flight: {len(st['pending'])})")
        return
    try:
        bal = balance(TREASURY)
    except Exception as e:
        print("treasury balance check failed:", e); return
    budget = min(PER_RUN_CAP, max(0, bal - TREASURY_FLOOR))
    if budget < MIN_PAYOUT:
        print(f"budget too low (treasury {bal} vs floor {TREASURY_FLOOR})"); return

    print(f"treasury={bal} nANM  budget={budget} nANM  candidates={len(candidates)}  mode={'DRY-RUN' if DRY_RUN else 'SEND'}")
    used = 0
    for addr, owed, jobs in sorted(candidates, key=lambda x: -x[1]):
        amt = min(owed, budget - used)
        if amt < MIN_PAYOUT:
            continue
        if DRY_RUN:
            print(f"  would pay {amt} nANM -> {addr}  ({jobs} jobs)")
            used += amt
        else:
            try:
                r = subprocess.run(
                    [ANIMICA, "tx", "send", "--from", TREASURY, "--to", addr, "--value-nanm", str(amt)],
                    capture_output=True, text=True, timeout=120,
                )
                m = _HASH.search(r.stdout or "")
                if r.returncode == 0 and m:
                    st["pending"][addr] = {"tx": m.group(0), "amt": amt, "at": int(time.time())}
                    used += amt
                    save(st)
                    print(f"  submitted {amt} nANM -> {addr}  tx={m.group(0)[:14]}… (awaiting confirmation)")
                else:
                    print(f"  send failed -> {addr}: {((r.stderr or r.stdout) or '').strip()[:160]}")
            except Exception as e:
                print(f"  send error -> {addr}: {e}")
        if budget - used < MIN_PAYOUT:
            break

    if not DRY_RUN:
        st["last_run"] = int(time.time())
        save(st)
    print(f"run complete: {used} nANM {'proposed' if DRY_RUN else 'submitted (confirms when a block is mined)'}")


if __name__ == "__main__":
    main()
