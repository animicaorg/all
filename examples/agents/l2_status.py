#!/usr/bin/env python3
"""l2_status.py — status + TPS counters of the ANM-native L2 rollup. Stdlib only.

Usage:  python3 l2_status.py

The L2 (validity rollup, l2ChainId 1001) exposes `l2_*` methods on the same
JSON-RPC endpoint as the L1 node: POST https://rpc.animica.org/rpc
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

RPC_URL = "https://rpc.animica.org/rpc"
TIMEOUT = 15
NANO = 10**9


def rpc(method: str, params=None):
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
        raise RuntimeError(f"{method}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"{method}: cannot reach {RPC_URL} ({e})") from e
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(f"{method}: RPC error {err.get('code')}: {err.get('message')}")
    return payload["result"]


def main() -> int:
    try:
        status = rpc("l2_status")
        tps = rpc("l2_getTPS")
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    bridge = status.get("bridge", {})
    print("Animica L2 rollup")
    print(f"  enabled      : {status.get('enabled')}  (mode: {status.get('mode')})")
    print(f"  l2 chain id  : {status.get('l2ChainId')}")
    print(f"  settlement   : {status.get('settlementMode')}")
    print(f"  head batch   : {status.get('headBatch')}  (pending txs: {status.get('pending')})")
    print(f"  state root   : {status.get('stateRoot')}")
    print(f"  bridge addr  : {status.get('bridgeAddress')}")
    locked = bridge.get("lockedOnL1")
    if locked is not None:
        print(f"  locked on L1 : {int(locked) / NANO:,.2f} ANM "
              f"(deposits: {bridge.get('deposits')}, withdrawals: {bridge.get('withdrawals')})")
    print("throughput counters")
    print(f"  executed total : {tps.get('executedTotal')}  ({tps.get('executedTps')} tps)")
    print(f"  soft-confirmed : {tps.get('softConfirmedTotal')}  ({tps.get('softConfirmedTps')} tps)")
    print(f"  settled total  : {tps.get('settledTotal')}  ({tps.get('settledTps')} tps)")
    print(f"  batches total  : {tps.get('batchesTotal')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
