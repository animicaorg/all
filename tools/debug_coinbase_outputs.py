#!/usr/bin/env python3
"""Print coinbase outputs by height for local RPC debugging."""

from __future__ import annotations

import argparse
import json
import urllib.request


def rpc(url: str, method: str, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read().decode())
    if "error" in body:
        raise RuntimeError(f"{method} failed: {body['error']}")
    return body["result"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default="http://127.0.0.1:8545")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--count", type=int, default=10)
    args = ap.parse_args()

    for h in range(args.start, args.start + args.count):
        blk = rpc(args.rpc, "chain.getBlockByNumber", [h, True])
        if not blk:
            print(f"height={h} missing")
            continue
        txs = blk.get("transactions", [])
        coinbase_outputs = []
        for tx in txs:
            # coinbase txs have sender zero / type=coinbase; read transfer target+value if present
            tx_from = (tx.get("from") or "").lower() if isinstance(tx, dict) else ""
            is_coinbase = tx.get("kind") == 3 or tx.get("type") == "coinbase" or tx_from in {"0x" + "0" * 64, "0x" + "0" * 40}
            if not is_coinbase:
                continue
            to = tx.get("to") if isinstance(tx, dict) else None
            amount = tx.get("value", tx.get("amount", 0)) if isinstance(tx, dict) else 0
            coinbase_outputs.append((to, amount))

        payout = blk.get("miner") or blk.get("coinbase")
        print(
            f"height={h} coinbase_outputs={coinbase_outputs} "
            f"miner_payout_addr={payout} dev_fee_addr=None"
        )


if __name__ == "__main__":
    main()
