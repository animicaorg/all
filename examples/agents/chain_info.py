#!/usr/bin/env python3
"""chain_info.py — query Animica L1 head + total supply. Stdlib only.

Usage:  python3 chain_info.py
RPC:    POST https://rpc.animica.org/rpc  (JSON-RPC 2.0)

Note: use the /rpc path — the bare domain root returns a 301 redirect
that breaks naive POST clients.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

RPC_URL = "https://rpc.animica.org/rpc"
TIMEOUT = 15
NANO = 10**9  # 1 ANM = 10^9 base units


def rpc(method: str, params=None):
    """Single JSON-RPC 2.0 call; returns the `result` or raises RuntimeError."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    req = urllib.request.Request(
        RPC_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method}: HTTP {e.code} from {RPC_URL}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"{method}: cannot reach {RPC_URL} ({e.reason})") from e
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(f"{method}: RPC error {err.get('code')}: {err.get('message')}")
    return payload["result"]


def main() -> int:
    try:
        head = rpc("chain.getHead")
        supply = rpc("state.getTotalSupply")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    total_base = int(supply["totalSupply"], 16)  # hex string of base units
    print("Animica L1 (chain id 1)")
    print(f"  height        : {head['height']}")
    print(f"  block hash    : {head['hash']}")
    print(f"  theta (micro) : {head['thetaMicro']}")
    print(f"  nonce         : {head['nonce']}")
    print(f"  total supply  : {total_base / NANO:,.2f} ANM ({total_base} base units)")
    print(f"  addresses     : {supply['addressCount']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
