#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request


def rpc_call(url: str, method: str, params, req_id: int):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_generic_invalid_params(resp: dict) -> bool:
    err = resp.get("error")
    if not isinstance(err, dict):
        return False
    if err.get("code") != -32602:
        return False
    data = err.get("data")
    return not isinstance(data, dict) or not data.get("expected")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce tx invalid-params scenarios")
    parser.add_argument("--url", default="http://127.0.0.1:8545/rpc")
    parser.add_argument("--method", default="tx.send")
    args = parser.parse_args()

    cases = [
        ["0xdeadbeef"],
        {"tx": "0xdeadbeef"},
        "0xdeadbeef",
        [{"rawTx": "0xdeadbeef"}, {"broadcast": True}],
        {"params": ["0xdeadbeef"]},
        {"tx": "!!!not-valid!!!"},
    ]

    for i, params in enumerate(cases, 1):
        resp = rpc_call(args.url, args.method, params, i)
        print(f"\nCase {i}: params={params!r}")
        print(json.dumps(resp, indent=2, sort_keys=True))
        assert not is_generic_invalid_params(resp), "generic invalid params without rich error.data"


if __name__ == "__main__":
    main()
