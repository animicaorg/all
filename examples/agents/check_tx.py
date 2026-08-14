#!/usr/bin/env python3
"""check_tx.py — look up an Animica transaction by hash. Stdlib only.

Usage:  python3 check_tx.py 0x<64-hex-chars>

Tries the explorer REST API first (rich data: block, addresses, value,
classification), then falls back to node JSON-RPC `tx.getStatus`
(positional params). A missing tx is reported cleanly, not as a crash.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

EXPLORER = "https://explorer.animica.org/api/tx/"
RPC_URL = "https://rpc.animica.org/rpc"
TIMEOUT = 15
NANO = 10**9


def http_json(url: str, data: bytes | None = None):
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def from_explorer(tx_hash: str):
    """Returns tx dict, 'not_found', or None (explorer unreachable)."""
    try:
        return http_json(EXPLORER + tx_hash)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "not_found"
        print(f"note: explorer returned HTTP {e.code}, falling back to RPC", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"note: explorer unreachable ({e}), falling back to RPC", file=sys.stderr)
    return None


def from_rpc(tx_hash: str):
    """tx.getStatus takes the hash as a positional parameter."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tx.getStatus", "params": [tx_hash]}
    ).encode()
    payload = http_json(RPC_URL, body)
    if "error" in payload:
        raise RuntimeError(f"RPC error: {payload['error'].get('message')}")
    return payload["result"]


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"0x[0-9a-fA-F]{64}", sys.argv[1]):
        print("usage: check_tx.py 0x<64 hex chars>", file=sys.stderr)
        return 2
    tx_hash = sys.argv[1].lower()

    tx = from_explorer(tx_hash)
    if tx == "not_found":
        print(f"Transaction {tx_hash} not found (explorer).")
        return 0
    if isinstance(tx, dict):
        print(f"tx      : {tx['hash']}")
        print(f"status  : {tx.get('status')}  (confirmations: {tx.get('confirmations')})")
        print(f"block   : height {tx.get('blockHeight')}  {tx.get('blockHash')}")
        print(f"from    : {tx.get('from')}")
        print(f"to      : {tx.get('to')}")
        if tx.get("value") is not None:
            base = int(tx["value"])
            print(f"value   : {base / NANO:.9f} ANM ({base} base units)")
        cls = tx.get("classification") or {}
        print(f"type    : {cls.get('type')}  failed={cls.get('failed')}")
        return 0

    # Explorer down — the node still knows inclusion status.
    try:
        st = from_rpc(tx_hash)
    except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
        print(f"error: both explorer and RPC failed: {e}", file=sys.stderr)
        return 1
    if st.get("status") == "not_found":
        print(f"Transaction {tx_hash} not found (node RPC).")
        return 0
    print(f"tx      : {st['hash']}")
    print(f"status  : {st.get('status')} / {st.get('state')}")
    print(f"block   : height {st.get('included_height')}  {st.get('included_in_block_hash')}")
    print(f"confs   : {st.get('confirmations')}  finalized_in_pow={st.get('finalized_in_pow')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
