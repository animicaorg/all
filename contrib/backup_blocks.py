#!/usr/bin/env python3
"""Back up every Animica block to a zip archive (one JSON file per block).

Queries the local RPC node (chain.getBlockByNumber) for blocks 0..head with full
transaction objects + receipts and writes each into a zip as block-XXXXXXXX.json.
A manifest.json records chain id, head, count and the export timestamp.

Usage: python3 backup_blocks.py [OUTPUT_ZIP]
"""
from __future__ import annotations

import json
import sys
import time
import zipfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

RPC = "http://127.0.0.1:8545/rpc"
WORKERS = 16
RETRIES = 5


def rpc(method: str, params=None):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params or []}).encode()
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(RPC, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                doc = json.load(r)
            if "error" in doc and doc["error"]:
                raise RuntimeError(doc["error"])
            return doc["result"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"{method}({params}) failed after {RETRIES} tries: {last}")


def fetch_block(n: int):
    # includeTxObjects=True, includeReceipts=True for a complete record
    return n, rpc("chain.getBlockByNumber", [n, True, True])


def main() -> int:
    head = rpc("chain.getHead")
    height = int(head["height"])
    chain_id = head.get("chainId")
    head_hash = head.get("hash")
    count = height + 1
    print(f"chain {chain_id}: head height {height} ({count} blocks incl. genesis)")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = sys.argv[1] if len(sys.argv) > 1 else \
        f"/root/animica/animica-blocks-backup-chain{chain_id}-h{height}-{ts}.zip"

    width = max(8, len(str(height)))
    done = 0
    t0 = time.time()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(fetch_block, n): n for n in range(count)}
            for fut in as_completed(futs):
                n, blk = fut.result()
                zf.writestr(f"block-{n:0{width}d}.json",
                            json.dumps(blk, separators=(",", ":")))
                done += 1
                if done % 500 == 0 or done == count:
                    rate = done / max(1e-9, time.time() - t0)
                    print(f"  {done}/{count} ({rate:.0f}/s)", flush=True)

        manifest = {
            "network": "animica",
            "chainId": chain_id,
            "headHeight": height,
            "headHash": head_hash,
            "blockCount": count,
            "exportedAt": ts,
            "rpc": RPC,
            "format": "one JSON file per block (chain.getBlockByNumber, "
                      "includeTxObjects+includeReceipts)",
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    import os
    size = os.path.getsize(out)
    print(f"\nWrote {out}\n  {count} blocks + manifest.json, {size/1e6:.1f} MB, "
          f"{time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
