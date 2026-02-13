#!/usr/bin/env python3
"""Probe tx.sendRawTransaction params schema against an RPC endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests


def load_raw_tx(raw: str | None, raw_file: str | None) -> str:
    if raw and raw_file:
        raise SystemExit("Provide only one of --raw or --raw-file")
    if raw_file:
        return Path(raw_file).read_text(encoding="utf-8").strip()
    if raw:
        return raw.strip()
    data = sys.stdin.read().strip()
    if not data:
        raise SystemExit("No raw tx provided. Use --raw, --raw-file, or stdin.")
    return data


def sanitize(v: Any) -> Any:
    if isinstance(v, str) and v.startswith("0x") and len(v) > 18:
        return f"{v[:10]}...{v[-8:]} (len={len(v)})"
    if isinstance(v, dict):
        return {k: sanitize(val) for k, val in v.items()}
    if isinstance(v, list):
        return [sanitize(x) for x in v]
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://mainnet.animica.org/rpc")
    ap.add_argument("--raw")
    ap.add_argument("--raw-file")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    raw_tx = load_raw_tx(args.raw, args.raw_file)

    candidates: list[tuple[str, Any]] = [
        ("A.positional", [raw_tx]),
        ("B.named.rawTx", {"rawTx": raw_tx}),
        ("B.named.raw_tx", {"raw_tx": raw_tx}),
        ("B.named.tx", {"tx": raw_tx}),
        ("B.named.raw", {"raw": raw_tx}),
        ("C.array.named.rawTx", [{"rawTx": raw_tx}]),
        ("C.array.named.raw_tx", [{"raw_tx": raw_tx}]),
        ("C.array.named.tx", [{"tx": raw_tx}]),
        ("D.positional.withOptions", [raw_tx, {}]),
        ("D.array.named.rawTx.withOptions", [{"rawTx": raw_tx}, {}]),
    ]

    accepted = []
    for idx, (label, params) in enumerate(candidates, start=1):
        payload = {"jsonrpc": "2.0", "id": idx, "method": "tx.sendRawTransaction", "params": params}
        print(f"\n[{label}] request={json.dumps(sanitize(payload), ensure_ascii=False)}")
        try:
            resp = requests.post(args.url, json=payload, timeout=args.timeout)
            text = resp.text
            print(f"status={resp.status_code}")
            print(f"response={text}")
            try:
                out = resp.json()
            except Exception:
                continue
            if isinstance(out, dict) and out.get("error") is None:
                accepted.append(label)
            elif isinstance(out, dict) and "error" not in out and out.get("result") is not None:
                accepted.append(label)
        except Exception as exc:
            print(f"request_error={exc}")

    print("\n=== summary ===")
    if accepted:
        print("accepted schemas:")
        for label in accepted:
            print(f"- {label}")
    else:
        print("no schema accepted (or all failed before validation)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
