"""Rebuild the canonical by-height index (k_hix: height -> block_hash).

Root cause of the recurring "nodes stuck behind" outage: the block DATA is intact
(every block is retrievable by-hash and the head chain walks cleanly to genesis),
but the secondary canonical height index has holes (e.g. 3591-4609, 4614,
4617-4621, 20000, ...). RPC block_getBlockByNumber resolves height -> hash via
this index, so the seed returns NULL at each hole and CANNOT serve a contiguous
chain. Every syncing peer pins at the last servable height below a hole
(89.x@3590, 3.12.224.189@4617, ...).

This repair is LOSSLESS: it walks the canonical chain from head -> genesis via
header.parentHash (authoritative) and re-writes k_hix(height)=hash for every
height. Block data is never touched. If any block on the chain is missing
by-hash, it ABORTS without writing (that case needs a snapshot restore instead).

Usage:
  # dry run (read-only, safe while node is running):
  python scripts/repair_height_index.py --db /data/chain-1/animica.db
  # apply (RUN WITH THE NODE STOPPED):
  python scripts/repair_height_index.py --db <path> --apply
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/root/animica")

from core.db.block_db import BlockDB, k_hix  # noqa: E402
from core.db.sqlite import open_sqlite_kv  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="path to animica.db")
    ap.add_argument("--apply", action="store_true", help="write the rebuilt index (node MUST be stopped)")
    args = ap.parse_args()

    kv = open_sqlite_kv(args.db, readonly=not args.apply, create=False)
    db = BlockDB(kv)

    head = db.get_head()
    if head is None:
        print("ERROR: no head pointer in DB")
        return 1
    head_height, head_hash = int(head[0]), bytes(head[1])
    print(f"head: height={head_height} hash={head_hash.hex()}")

    # Walk the canonical chain head -> genesis by parentHash.
    chain: dict[int, bytes] = {}
    missing_by_hash: list[tuple[int, str]] = []
    cur_hash = head_hash
    expected_height = head_height
    steps = 0
    t0 = time.time()
    while True:
        steps += 1
        if steps > head_height + 100:
            print("ERROR: walk exceeded head_height+100 (cycle?) — aborting")
            return 1
        hdr = db.get_header_by_hash(cur_hash)
        if hdr is None:
            missing_by_hash.append((expected_height, cur_hash.hex()))
            break
        h = int(hdr.height)
        chain[h] = cur_hash
        if h == 0:
            break
        parent = bytes(hdr.parentHash)
        if parent == b"\x00" * len(parent):
            print(f"WARN: zero parentHash at height {h} but height != 0; stopping")
            break
        cur_hash = parent
        expected_height = h - 1

    walked = len(chain)
    reached_genesis = 0 in chain
    print(f"walked {walked} blocks in {time.time()-t0:.1f}s; reached genesis={reached_genesis}")
    if missing_by_hash:
        print(f"ABORT: {len(missing_by_hash)} block(s) MISSING by-hash (chain data incomplete):")
        for ht, hx in missing_by_hash[:10]:
            print(f"   height~{ht} hash={hx}")
        print("   -> rebuild cannot complete; a snapshot restore is required instead.")
        return 2
    if not reached_genesis:
        print("ABORT: walk did not reach genesis (height 0); not safe to rebuild")
        return 2
    if walked != head_height + 1:
        print(f"WARN: walked {walked} != head_height+1 ({head_height+1}); chain has non-contiguous heights")

    # Compare against the existing index.
    missing_index = 0
    wrong_index = 0
    sample_holes: list[int] = []
    for ht, hsh in chain.items():
        cur = kv.get(k_hix(ht))
        if cur is None:
            missing_index += 1
            if len(sample_holes) < 20:
                sample_holes.append(ht)
        elif bytes(cur) != hsh:
            wrong_index += 1
    print(f"index entries: missing={missing_index} wrong={wrong_index} (of {walked})")
    if sample_holes:
        print(f"sample missing heights: {sorted(sample_holes)}")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply (node stopped) to rebuild.")
        return 0

    if missing_index == 0 and wrong_index == 0:
        print("index already complete and correct; nothing to do")
        return 0

    print(f"\nAPPLYING: rewriting {walked} height-index entries...")
    written = 0
    batch = kv.batch()
    with batch as b:
        for ht, hsh in chain.items():
            b.put(k_hix(ht), hsh)
            written += 1
    # Keep the canonical-height counter consistent with the real head.
    try:
        db.set_canonical_height(head_height)
        commit = getattr(kv, "commit", None)
        if callable(commit):
            commit()
    except Exception as exc:
        print(f"WARN: set_canonical_height failed (non-fatal): {exc}")
    print(f"APPLIED: wrote {written} entries; canonical_height set to {head_height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
