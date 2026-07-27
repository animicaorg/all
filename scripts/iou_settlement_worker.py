#!/root/animica/.venv/bin/python
"""Gateway-side on-chain IOU settlement worker (9.0.0, FORK_IOU_SETTLEMENT).

Runs on the gateway box via systemd timer (ops/systemd/animica-iou-settle.*).
Each run either:
  1. CONFIRMS a previously posted settlement anchor (state file carries the
     pending tx hash + the exact per-row entry list) and, once it is included
     at/after the fork height, marks the paid amounts settled in the
     marketplace DB — settledNanm += paid — in ONE transaction; or
  2. GATHERS payable IOUs from the marketplace ledgers and posts ONE anchor
     (≤ current pool cap, ≤ daily cap) via the SAME code path as
     `animica settle post` (animica.cli.settle.sign_and_submit_anchor —
     imported, never shelled out).

Payable ledgers (read-only SQL):
  - MediaMiner: payable = rewardNanm - settledNanm, paid to MediaMiner.address
  - VpnExit joined to its owner Account: paid to Account.address
  - HostingPin joined to its Account: paid to Account.address
  VpnSession is intentionally EXCLUDED: session rewardNanm is per-session
  DETAIL of the exit's aggregate accounting — the exit rows are the payable
  ledger; settling sessions too would double-count.

Safety model (all hard, all fail-closed with nonzero exit + clear log line):
  - exclusive whole-run flock (~/.animica/iou-settle.lock) — a second instance
    exits immediately, so two runs can never post/mark concurrently;
  - never posts while a pending anchor is unconfirmed; an anchor counts as
    confirmed only when buried ANM_SETTLE_CONFIRM_DEPTH blocks (default 12)
    AND included at/after the fork height;
  - marks settled IDEMPOTENTLY: the IouSettlementAnchor row (txHash PK) and
    all settledNanm increments commit in ONE DB transaction, so a crash after
    commit but before the state-file save can never double-apply;
  - over-mark guard: before marking, the including block is re-inspected —
    if it carries any OTHER authority anchor, or the pool cap at the inclusion
    height is below the anchor total (consensus would have scaled pro-rata),
    nothing is marked and the pending record is left for operator review;
  - never posts below the fork height (50,000) — logs the payable total and
    exits 0 ("pre-activation");
  - never posts unless ANM_SETTLE_POST=1 (default = DRY-RUN print);
  - per-run total ≤ settlement_pool_cap(height) (the consensus cap), daily
    total ≤ ANM_SETTLE_DAILY_CAP_NANM (default 500 ANM, tracked in the state
    file ~/.animica/iou-settle-state.json);
  - per-entry threshold ANM_SETTLE_MIN_NANM (default 5 ANM);
  - DATABASE_URL comes from the marketplace .env.production and is never
    printed; no secret material is ever logged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
# Script execution puts scripts/ (not the repo root) on sys.path; make the
# consensus/core packages and python/animica importable regardless of cwd.
for p in (str(REPO), str(REPO / "python")):
    if p not in sys.path:
        sys.path.insert(0, p)

ENV_FILE = REPO / "apps" / "animica-marketplace" / ".env.production"
STATE_FILE = Path.home() / ".animica" / "iou-settle-state.json"
LOCK_FILE = Path.home() / ".animica" / "iou-settle.lock"

ANM = 1_000_000_000
DEFAULT_MIN_NANM = 5 * ANM          # ANM_SETTLE_MIN_NANM
DEFAULT_DAILY_CAP_NANM = 500 * ANM  # ANM_SETTLE_DAILY_CAP_NANM
DEFAULT_CONFIRM_DEPTH = 12          # ANM_SETTLE_CONFIRM_DEPTH
PENDING_NOT_FOUND_GRACE_S = 2 * 3600

# Whitelist: state-file table names -> (SQL table, id column). Never interpolate
# anything else into SQL.
LEDGER_TABLES = {
    "MediaMiner": '"MediaMiner"',
    "VpnExit": '"VpnExit"',
    "HostingPin": '"HostingPin"',
}

log = logging.getLogger("iou-settle")


def _fmt_anm(nanm: int) -> str:
    return f"{Decimal(int(nanm)) / ANM} ANM"


def _acquire_run_lock():
    """Exclusive whole-run lock: two timers/humans must never settle at once.
    Returns the open handle (keep it alive for the process lifetime) or None
    when another instance already holds the lock."""
    import fcntl

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _norm_hex(value) -> str:
    """Normalize an 0x-hex string for comparison (lowercase, no 0x)."""
    if not isinstance(value, str):
        return ""
    v = value.strip().lower()
    return v[2:] if v.startswith("0x") else v


def _read_env_file(path: Path) -> dict:
    """Parse KEY=VALUE lines. Values are secrets — callers must never log them."""
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _db_dsn() -> str:
    if not ENV_FILE.exists():
        raise RuntimeError(f"marketplace env file not found: {ENV_FILE}")
    url = _read_env_file(ENV_FILE).get("DATABASE_URL")
    if not url:
        raise RuntimeError(f"DATABASE_URL missing from {ENV_FILE}")
    # Prisma URLs carry ?schema=public which psycopg rejects — strip it.
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query) if k.lower() != "schema"]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"state file {STATE_FILE} is unreadable/corrupt ({exc}) — refusing to "
                "guess about pending anchors; fix or remove it after manual review"
            ) from exc
    else:
        state = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"] = today
        state["day_total_nanm"] = 0
    state.setdefault("day_total_nanm", 0)
    state.setdefault("pending", None)
    return state


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def _valid_payout_address(addr) -> bool:
    if not isinstance(addr, str) or not addr.startswith("anim1"):
        return False
    # Require ml_dsa_65 (algId 0x1003) — the only real scheme; a broken-scheme address that still
    # decodes to a 32-byte digest would otherwise be paid on-chain and the ANM stranded forever.
    try:
        from pq.py.address import validate_address

        return bool(validate_address(addr, expect_hrp="anim", allowed_alg_ids={0x1003}))
    except Exception:
        pass
    try:
        from core.utils.address import address_to_bytes

        return len(address_to_bytes(addr)) == 32
    except Exception:
        return False


def _payout_column_present(cur) -> bool:
    """Whether VpnExit.payoutAddress exists yet (9.0.7). If the code lands before `db push`,
    fall back to the owner Account.address so the VpnExit query never errors."""
    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'VpnExit' AND column_name = 'payoutAddress'
        """
    )
    return cur.fetchone() is not None


def _settled_columns_present(cur) -> bool:
    cur.execute(
        """
        SELECT table_name FROM information_schema.columns
        WHERE table_name = ANY(%s) AND column_name = 'settledNanm'
        """,
        (list(LEDGER_TABLES.keys()),),
    )
    present = {r[0] for r in cur.fetchall()}
    return present >= set(LEDGER_TABLES.keys())


def _gather_payables(cur, threshold: int, settled_migrated: bool) -> list[dict]:
    """Read-only payable scan. Returns [{table, id, address, payable_nanm}, …].

    The settledNanm columns are part of the 9.0.0 marketplace schema; when the
    live DB has not been migrated yet, DRY-RUN display substitutes settled=0
    (posting is refused in that state — see main())."""
    settled = lambda alias: (f'{alias}."settledNanm"' if settled_migrated else "0")  # noqa: E731
    rows: list[dict] = []

    cur.execute(
        f'''
        SELECT m."id", m."address", m."rewardNanm" - {settled('m')} AS payable
        FROM "MediaMiner" m
        WHERE m."address" IS NOT NULL
          AND m."rewardNanm" - {settled('m')} >= %s
        ''',
        (threshold,),
    )
    rows += [
        {"table": "MediaMiner", "id": r[0], "address": r[1], "payable_nanm": int(r[2])}
        for r in cur.fetchall()
    ]

    # 9.0.7: an exit may name an explicit payoutAddress; fall back to the owner's account address.
    # The anim1 filter applies to the effective (coalesced) address so a valid override is not
    # mis-filtered. Feature-detect the column so the code is safe to deploy before `db push`.
    vpn_addr = ('COALESCE(v."payoutAddress", a."address")'
                if _payout_column_present(cur) else 'a."address"')
    cur.execute(
        f'''
        SELECT v."id", {vpn_addr} AS addr,
               v."rewardNanm" - {settled('v')} AS payable
        FROM "VpnExit" v
        JOIN "Account" a ON a."id" = v."ownerId"
        WHERE v."rewardNanm" - {settled('v')} >= %s
          AND {vpn_addr} LIKE 'anim1%%'
        ''',
        (threshold,),
    )
    rows += [
        {"table": "VpnExit", "id": r[0], "address": r[1], "payable_nanm": int(r[2])}
        for r in cur.fetchall()
    ]

    cur.execute(
        f'''
        SELECT h."id", a."address", h."rewardNanm" - {settled('h')} AS payable
        FROM "HostingPin" h
        JOIN "Account" a ON a."id" = h."accountId"
        WHERE h."accountId" IS NOT NULL
          AND h."rewardNanm" - {settled('h')} >= %s
          AND a."address" LIKE 'anim1%%'
        ''',
        (threshold,),
    )
    rows += [
        {"table": "HostingPin", "id": r[0], "address": r[1], "payable_nanm": int(r[2])}
        for r in cur.fetchall()
    ]
    # NOTE: VpnSession.rewardNanm is deliberately NOT queried — sessions are
    # per-session detail under their VpnExit aggregate; paying both would
    # double-count the same relay work.

    payables = []
    for row in rows:
        if not _valid_payout_address(row["address"]):
            log.warning(
                "skipping %s id=%s: reward address is not a valid anim1 account",
                row["table"], row["id"],
            )
            continue
        if row["payable_nanm"] > 0:
            payables.append(row)
    payables.sort(key=lambda r: r["payable_nanm"], reverse=True)
    return payables


def _anchor_paid_in_full(
    rpc: str, block_no: int, tx_hash: str, total_nanm: int, params: dict
) -> tuple[bool, str]:
    """OVER-MARK GUARD: re-derive that the including block paid OUR anchor in
    full before settledNanm is incremented. Consensus scales outputs pro-rata
    when a block's anchors exceed the pool cap, and this worker never guesses
    scaled amounts — anything short of "our single anchor, under the cap at the
    inclusion height" fails closed for operator review."""
    from animica.cli.settle import MAINNET_CHAIN_ID, authority_address, current_pool_cap_nanm, rpc_call
    from consensus.iou_settlement import SETTLEMENT_MAGIC
    from core.utils.address import address_to_bytes

    cap_at_inclusion = current_pool_cap_nanm(block_no, MAINNET_CHAIN_ID, params)
    if total_nanm > cap_at_inclusion:
        return False, (
            f"pool cap at inclusion height {block_no} is {cap_at_inclusion} nANM but the "
            f"anchor requested {total_nanm} nANM — consensus scaled the outputs"
        )

    block = rpc_call(rpc, "chain.getBlockByHeight", [int(block_no), True])
    txs = None
    if isinstance(block, dict):
        txs = block.get("txs") or block.get("transactions")
    if not isinstance(txs, list) or not all(isinstance(x, dict) for x in txs):
        return False, f"cannot inspect the txs of block {block_no} (unexpected RPC shape)"

    authority_hex = address_to_bytes(authority_address()).hex()
    magic_hex = SETTLEMENT_MAGIC.hex()
    anchor_hashes = []
    for tv in txs:
        data_hex = _norm_hex(tv.get("data"))
        from_hex = _norm_hex(tv.get("from"))
        if data_hex.startswith(magic_hex) and from_hex == authority_hex:
            anchor_hashes.append(_norm_hex(tv.get("hash")))

    ours = _norm_hex(tx_hash)
    if len(anchor_hashes) != 1 or anchor_hashes[0] != ours:
        return False, (
            f"block {block_no} carries {len(anchor_hashes)} authority anchor(s) "
            f"{anchor_hashes!r}, expected exactly ours ({ours}) — pool cap was shared, "
            "per-entry amounts may have been scaled"
        )
    return True, ""


def _apply_confirmed_anchor(dsn: str, tx_hash: str, entries: list, block_no: int) -> bool:
    """Mark the anchor's entries settled — IDEMPOTENTLY. The IouSettlementAnchor
    row (txHash PK) and ALL settledNanm increments commit in ONE transaction;
    if the row already exists, a previous run applied this anchor (crash before
    its state-file save) and nothing is incremented again.

    Returns True when applied now, False when it was already applied."""
    import psycopg

    total = sum(int(e["paid_nanm"]) for e in entries)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if not _settled_columns_present(cur):
                raise RuntimeError(
                    "settledNanm columns missing from the marketplace DB — cannot mark "
                    "the confirmed anchor settled; run the 9.0.0 schema migration first"
                )
            cur.execute(
                'INSERT INTO "IouSettlementAnchor" ("txHash", "entriesJson", "totalNanm", "height") '
                'VALUES (%s, %s, %s, %s) ON CONFLICT ("txHash") DO NOTHING',
                (tx_hash, json.dumps(entries, sort_keys=True), total, int(block_no)),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False
            for e in entries:
                table_sql = LEDGER_TABLES.get(e.get("table"))
                if table_sql is None:
                    raise RuntimeError(f"state entry references unknown table: {e!r}")
                cur.execute(
                    f'UPDATE {table_sql} SET "settledNanm" = "settledNanm" + %s WHERE "id" = %s',
                    (int(e["paid_nanm"]), e["id"]),
                )
        conn.commit()
    return True


def _confirm_pending(
    state: dict, rpc: str, fork_h: int, dsn: str, chain_height: int, params: dict
) -> int:
    """Handle an existing pending anchor. Returns process exit code."""
    from animica.cli.settle import rpc_call

    confirm_depth = int(os.environ.get("ANM_SETTLE_CONFIRM_DEPTH") or DEFAULT_CONFIRM_DEPTH)
    pending = state["pending"]
    tx_hash = pending.get("tx_hash")
    if not tx_hash:
        log.error(
            "pending anchor has NO tx hash (crash between intent and submit?) — "
            "fail closed; verify on-chain manually, then edit/clear 'pending' in %s",
            STATE_FILE,
        )
        return 1

    status = rpc_call(rpc, "tx.getTransactionStatus", [tx_hash])
    st = (status or {}).get("status") if isinstance(status, dict) else None
    block_no = (status or {}).get("blockNumber") if isinstance(status, dict) else None
    log.info("pending anchor %s status=%s block=%s", tx_hash, st, block_no)

    if st == "pending":
        log.info("anchor still in mempool — will not post another; exiting")
        return 0
    if st == "rejected":
        log.error(
            "anchor %s was REJECTED by the node (%s) — fail closed; investigate, then "
            "clear 'pending' in %s to resume", tx_hash, (status or {}).get("reason"), STATE_FILE,
        )
        return 1
    if st == "not_found":
        age = time.time() - float(pending.get("posted_at_ts") or 0)
        if age < PENDING_NOT_FOUND_GRACE_S:
            log.info("anchor not visible yet (age %.0fs) — waiting; exiting", age)
            return 0
        log.error(
            "anchor %s not found after %.0fs (dropped? reorged?) — fail closed; verify "
            "on-chain, then clear 'pending' in %s", tx_hash, age, STATE_FILE,
        )
        return 1
    if st != "confirmed":
        log.error("unexpected tx status %r for anchor %s — fail closed", status, tx_hash)
        return 1

    if block_no is None or int(block_no) < fork_h:
        log.error(
            "anchor %s confirmed at height %s BELOW fork height %d — it settled NOTHING "
            "on-chain (inert transfer); fail closed, operator must review %s",
            tx_hash, block_no, fork_h, STATE_FILE,
        )
        return 1
    block_no = int(block_no)

    # CONFIRMATION DEPTH: an anchor at the tip can still reorg away; only mark
    # settled once it is buried ANM_SETTLE_CONFIRM_DEPTH blocks deep.
    if chain_height < block_no + confirm_depth:
        log.info(
            "anchor included at %d but only %d/%d confirmations — waiting",
            block_no, max(0, chain_height - block_no), confirm_depth,
        )
        return 0

    entries = pending.get("entries") or []
    total = sum(int(e["paid_nanm"]) for e in entries)

    # OVER-MARK GUARD: verify the including block actually paid our anchor in
    # full (sole authority anchor, under the cap at that height). Never guess
    # pro-rata amounts — fail closed for operator review instead.
    ok, why = _anchor_paid_in_full(rpc, block_no, tx_hash, total, params)
    if not ok:
        log.error(
            "REFUSING to mark anchor %s settled: %s — the on-chain payout may be LESS "
            "than recorded; leaving 'pending' in %s for operator review (fail closed)",
            tx_hash, why, STATE_FILE,
        )
        return 1

    applied = _apply_confirmed_anchor(dsn, tx_hash, entries, block_no)
    if applied:
        log.info(
            "anchor %s CONFIRMED at height %d (depth %d) — marked %s settled across %d "
            "ledger row(s)", tx_hash, block_no, confirm_depth, _fmt_anm(total), len(entries),
        )
    else:
        log.info(
            "anchor %s was ALREADY applied (IouSettlementAnchor row exists — crash "
            "before state save?) — clearing pending without re-applying", tx_hash,
        )
    state["pending"] = None
    _save_state(state)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="iou-settle %(levelname)s: %(message)s", stream=sys.stdout
    )

    from animica.cli.settle import (
        MAINNET_CHAIN_ID,
        current_pool_cap_nanm,
        get_chain_height,
        get_fork_height,
        load_consensus_params,
        resolve_rpc_url,
        sign_and_submit_anchor,
    )
    from consensus.iou_settlement import MAX_ENTRIES_PER_ANCHOR

    # MUTUAL EXCLUSION: one settlement run at a time, for the WHOLE run (the
    # handle must stay referenced until process exit).
    run_lock = _acquire_run_lock()
    if run_lock is None:
        log.info("another instance holds the lock (%s) — exiting", LOCK_FILE)
        return 0

    threshold = int(os.environ.get("ANM_SETTLE_MIN_NANM") or DEFAULT_MIN_NANM)
    daily_cap = int(os.environ.get("ANM_SETTLE_DAILY_CAP_NANM") or DEFAULT_DAILY_CAP_NANM)
    posting = os.environ.get("ANM_SETTLE_POST") == "1"
    rpc = resolve_rpc_url(None)
    dsn = _db_dsn()  # secret — never logged

    fork_h = get_fork_height(MAINNET_CHAIN_ID)
    if fork_h is None:
        log.error("cannot resolve FORK_IOU_SETTLEMENT activation height — fail closed")
        return 1
    height = get_chain_height(rpc)
    if height is None:
        log.error("cannot resolve chain height from %s — fail closed", rpc)
        return 1

    state = _load_state()
    params = load_consensus_params(MAINNET_CHAIN_ID)

    # 1) A pending anchor owns the run: confirm-or-wait, never post another.
    if state.get("pending"):
        return _confirm_pending(state, rpc, fork_h, dsn, height, params)

    # 2) No pending anchor: gather payables (read-only).
    pool_cap = current_pool_cap_nanm(height, MAINNET_CHAIN_ID, params)
    day_used = int(state.get("day_total_nanm") or 0)
    budget = min(pool_cap, max(0, daily_cap - day_used))

    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.read_only = True  # payable scan is strictly read-only
        with conn.cursor() as cur:
            settled_migrated = _settled_columns_present(cur)
            if not settled_migrated:
                log.warning(
                    "settledNanm columns not migrated yet in the marketplace DB — "
                    "payables shown assume settled=0; POSTING IS DISABLED until the "
                    "9.0.0 schema migration lands"
                )
            payables = _gather_payables(cur, threshold, settled_migrated)

    total_payable = sum(r["payable_nanm"] for r in payables)
    if not payables:
        log.info(
            "no payables >= %s (threshold) — nothing to settle; exiting", _fmt_anm(threshold)
        )
        return 0

    # Clip largest-first to the remaining budget (per-run pool cap + daily cap).
    entries: list[dict] = []
    remaining = budget
    for row in payables:
        if remaining <= 0 or len(entries) >= MAX_ENTRIES_PER_ANCHOR:
            break
        pay = min(row["payable_nanm"], remaining)
        if pay <= 0:
            continue
        entries.append({**row, "paid_nanm": pay})
        remaining -= pay
    total_to_pay = sum(e["paid_nanm"] for e in entries)

    log.info(
        "payable: %s across %d ledger row(s); pool cap %s, daily remaining %s -> "
        "this anchor pays %s across %d entr%s",
        _fmt_anm(total_payable), len(payables), _fmt_anm(pool_cap),
        _fmt_anm(max(0, daily_cap - day_used)), _fmt_anm(total_to_pay),
        len(entries), "y" if len(entries) == 1 else "ies",
    )
    for e in entries:
        log.info(
            "  %-10s id=%s  %s  -> %s", e["table"], e["id"], e["address"], _fmt_anm(e["paid_nanm"])
        )

    if total_to_pay <= 0:
        log.info("budget exhausted (daily cap reached?) — nothing payable this run")
        return 0

    # 3) Height gate: the fork is not active yet — anchors would be inert.
    if height < fork_h:
        log.info(
            "pre-activation: %s payable, waiting for height %d (chain at %d)",
            _fmt_anm(total_payable), fork_h, height,
        )
        return 0

    # 4) DRY-RUN unless explicitly armed.
    if not posting:
        log.info("DRY-RUN (ANM_SETTLE_POST != '1'): distribution printed above; not posting")
        return 0
    if not settled_migrated:
        log.error(
            "refusing to post: settledNanm columns are not migrated — a confirmed anchor "
            "could not be marked settled (double-pay hazard); fail closed"
        )
        return 1

    # 5) Post ONE anchor via the exact `animica settle post` code path
    #    (animica.cli.settle prepare/broadcast — imported, never shelled out).
    #    ALL fallible pre-broadcast work (wallet, fees, nonce, sign, local
    #    verify) runs FIRST with no side effects; the intent record is saved
    #    only once the signed tx is ready, immediately before the single
    #    broadcast call — so a transient pre-broadcast failure retries cleanly
    #    next run, while a crash/failure AT broadcast (outcome unknown) leaves
    #    a hash-less pending record that blocks future runs for review.
    from animica.cli.settle import authority_nonce_lock, broadcast_anchor, prepare_anchor_tx

    anchor_entries = [(e["address"], e["paid_nanm"]) for e in entries]
    with authority_nonce_lock():
        prepared = prepare_anchor_tx(anchor_entries, rpc_url=rpc)

        state["pending"] = {
            "entries": [
                {"table": e["table"], "id": e["id"], "address": e["address"],
                 "paid_nanm": e["paid_nanm"]}
                for e in entries
            ],
            "total_nanm": total_to_pay,
            "tx_hash": None,
            "posted_at_ts": time.time(),
            "posted_at_height": height,
        }
        state["day_total_nanm"] = day_used + total_to_pay
        _save_state(state)

        tx_hash = broadcast_anchor(prepared)

    state["pending"]["tx_hash"] = tx_hash
    _save_state(state)
    log.info(
        "anchor POSTED tx=%s total=%s entries=%d — will mark settled after confirmation",
        tx_hash, _fmt_anm(total_to_pay), len(entries),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail closed, loudly, nonzero
        log.error("settlement run failed: %r", exc)
        raise SystemExit(1)
