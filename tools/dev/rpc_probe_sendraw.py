#!/usr/bin/env python3
"""Probe tx.sendRawTransaction param shapes against an RPC endpoint."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any


def _strip_0x(v: str) -> str:
    return v[2:] if v.startswith(("0x", "0X")) else v


def _to_0x(v: str) -> str:
    h = _strip_0x(v)
    return "0x" + h


def _post_json(url: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="RPC URL")
    ap.add_argument("--raw-tx", required=True, help="Raw tx hex (with or without 0x)")
    args = ap.parse_args()

    raw_0x = _to_0x(args.raw_tx)
    raw_no = _strip_0x(args.raw_tx)

    attempts: list[tuple[str, Any]] = [
        ("params:[0x]", [raw_0x]),
        ("params:[no0x]", [raw_no]),
        ("params:{rawTx:0x}", {"rawTx": raw_0x}),
        ("params:{rawTx:no0x}", {"rawTx": raw_no}),
        ("params:{raw_tx:0x}", {"raw_tx": raw_0x}),
        ("params:{raw_tx:no0x}", {"raw_tx": raw_no}),
        ("params:[{rawTx:0x}]", [{"rawTx": raw_0x}]),
        ("params:[{raw_tx:0x}]", [{"raw_tx": raw_0x}]),
        ("params:[{tx:0x}]", [{"tx": raw_0x}]),
        ("params:{tx:0x}", {"tx": raw_0x}),
        ("params:[0x,{}]", [raw_0x, {}]),
        ("params:[{rawTx:0x},{}]", [{"rawTx": raw_0x}, {}]),
    ]

    winners: list[str] = []

    for idx, (label, params) in enumerate(attempts, start=1):
        payload = {
            "jsonrpc": "2.0",
            "id": idx,
            "method": "tx.sendRawTransaction",
            "params": params,
        }
        try:
            res = _post_json(args.url, payload)
        except Exception as exc:
            print(f"[{idx:02d}] {label}: transport_error={exc}")
            continue

        if "error" in res:
            err = res.get("error", {})
            code = err.get("code")
            msg = err.get("message")
            data = err.get("data")
            print(f"[{idx:02d}] {label}: error code={code} message={msg!r} data={data!r}")
            if code != -32602:
                winners.append(label)
                break
        else:
            print(f"[{idx:02d}] {label}: result={res.get('result')!r}")
            winners.append(label)
            break

    if winners:
        print("\nwinning schema(s):")
        for w in winners:
            print(f" - {w}")
        return 0

    print("\nNo non--32602 response observed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
