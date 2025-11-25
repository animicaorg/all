#!/usr/bin/env python3
from __future__ import annotations

"""
Animica: one-shot getWork helper

Fetches the current mining template from a local/remote node via JSON-RPC and
prints it as JSON. Useful for quick sanity checks and debugging miners.

Usage:
  python -m mining.cli.getwork --rpc-url http://127.0.0.1:8547 --chain-id 1
  ANIMICA_RPC_URL=http://127.0.0.1:8547 python -m mining.cli.getwork
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional


def _env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if v is not None and v != "" else fallback


def json_rpc_call(url: str, method: str, params: Any | None, timeout: float, retries: int) -> Dict[str, Any]:
    """
    Minimal JSON-RPC HTTP POST using stdlib urllib; returns the parsed envelope.
    Raises on HTTP/network errors or when JSON-RPC 'error' is present.
    """
    import urllib.request
    from urllib.error import URLError, HTTPError

    url = url.rstrip("/") + "/rpc"
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) & 0x7FFFFFFF,
        "method": method,
        "params": params or [],
    }
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    backoff = 0.25
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                env = json.loads(body)
                if "error" in env and env["error"]:
                    raise RuntimeError(f"RPC error: {env['error']}")
                return env
        except (URLError, HTTPError, TimeoutError) as e:
            if attempt >= max(1, retries):
                raise
            time.sleep(backoff)
            backoff = min(4.0, backoff * 2.0)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="omni getwork",
        description="Print the current mining template JSON from the node (miner.getWork).",
    )
    p.add_argument(
        "--rpc-url",
        type=str,
        default=_env_default("ANIMICA_RPC_URL", "http://127.0.0.1:8547"),
        help="Node base URL (default: %(default)s or $ANIMICA_RPC_URL)",
    )
    p.add_argument(
        "--chain-id",
        type=int,
        default=int(_env_default("ANIMICA_CHAIN_ID", "1") or "1"),
        help="Chain ID to request work for (default: %(default)s or $ANIMICA_CHAIN_ID)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout seconds (default: %(default)s)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="HTTP retry attempts on transient errors (default: %(default)s)",
    )
    fmt = p.add_mutually_gradable = p.add_mutually_exclusive_group()
    fmt.add_argument("--compact", action="store_true", help="Print compact (no whitespace)")
    fmt.add_argument("--pretty", action="store_true", help="Pretty-print with indentation (default)")

    p.add_argument(
        "--full",
        action="store_true",
        help="Print full JSON-RPC envelope instead of just the 'result'",
    )

    args = p.parse_args(argv)

    try:
        env = json_rpc_call(
            url=args.rpc_url,
            method="miner.getWork",
            params=[{"chainId": args.chain_id}],
            timeout=args.timeout,
            retries=args.retries,
        )
        obj = env if args.full else env.get("result")
        if obj is None:
            raise RuntimeError("Empty result from miner.getWork")

        if args.compact and not args.pretty:
            print(json.dumps(obj, separators=(",", ":")))
        else:
            print(json.dumps(obj, indent=2, sort_keys=True))
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
