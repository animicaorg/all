#!/usr/bin/env python3
import argparse
import json
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8545")
ap.add_argument("--count", type=int, default=200, help="how many blocks to mine")
ap.add_argument("--batch", type=int, default=1, help="blocks per RPC call")
args = ap.parse_args()


def rpc(method, params=[]):
    data = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        args.url, data=data, headers={"content-type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req).read())


start_hex = rpc("eth_blockNumber")["result"]
start = int(start_hex, 16)
t0 = time.time()
mined = 0

while mined < args.count:
    n = min(args.batch, args.count - mined)
    rpc("evm_mine", [n])
    mined += n

t1 = time.time()
end_hex = rpc("eth_blockNumber")["result"]
end = int(end_hex, 16)
delta = end - start
rate = delta / max(1e-6, (t1 - t0))
print(f"Mined {delta} blocks in {t1 - t0:.2f}s  (~{rate:.1f} blk/s)")
