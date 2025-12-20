#!/usr/bin/env python3
"""
Bootstrap explorer cache data using Animica CLI RPC calls.

This script gathers full chain data (blocks + transactions) via `animica rpc call`
and writes a bootstrap JSON payload that the explorer can load on startup.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional


def run_rpc(method: str, params: Optional[list] = None, rpc_url: Optional[str] = None) -> Any:
    cmd = [str(Path(__file__).resolve().parents[1] / "animica"), "rpc", "call", method]
    if params is not None:
        cmd.append(json.dumps(params))
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])

    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout.strip() or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unexpected RPC output for {method}: {proc.stdout}") from exc


def try_methods(methods: List[str], params: Optional[list], rpc_url: Optional[str]) -> Any:
    last_error: Optional[Exception] = None
    for method in methods:
        try:
            return run_rpc(method, params=params, rpc_url=rpc_url)
        except Exception as exc:  # noqa: BLE001 - best-effort RPC probing
            last_error = exc
    if last_error:
        raise last_error
    return None


def resolve_head(rpc_url: Optional[str]) -> dict:
    head = try_methods(["chain.getHead", "chain_getHead"], None, rpc_url)
    if isinstance(head, dict) and head:
        return head

    block = try_methods(
        ["chain.getBlockByNumber", "block_getBlockByNumber"],
        ["latest", True, True],
        rpc_url,
    )
    if not isinstance(block, dict):
        raise RuntimeError("Unable to resolve chain head via RPC")
    return block


def normalize_height(data: dict) -> int:
    height = data.get("height") or data.get("number") or data.get("header", {}).get("height")
    if height is None:
        raise RuntimeError("Head response did not include height/number")
    try:
        return int(height)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid head height: {height}") from exc


def normalize_chain_id(chain_id: Any) -> str:
    if chain_id is None:
        return ""
    return str(chain_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap explorer cache data via animica CLI.")
    parser.add_argument(
        "--rpc-url",
        dest="rpc_url",
        default=os.environ.get("ANIMICA_RPC_URL"),
        help="Override RPC URL (defaults to ANIMICA_RPC_URL/env or CLI config)",
    )
    parser.add_argument(
        "--from-height",
        type=int,
        default=1,
        help="Start height (inclusive). Default: 1",
    )
    parser.add_argument(
        "--to-height",
        type=int,
        default=None,
        help="End height (inclusive). Default: chain head",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("explorer-web/public/bootstrap/chain.json"),
        help="Output JSON path for explorer bootstrap payload",
    )
    parser.add_argument(
        "--include-receipts",
        action="store_true",
        help="Include receipts in block payloads (RPC param includeReceipts)",
    )
    args = parser.parse_args()

    head = resolve_head(args.rpc_url)
    head_height = normalize_height(head)

    to_height = args.to_height if args.to_height is not None else head_height
    from_height = max(1, args.from_height)
    if to_height < from_height:
        raise RuntimeError("to-height must be >= from-height")

    chain_id = try_methods(["chain.getChainId", "chain_getChainId"], None, args.rpc_url)

    blocks: List[dict] = []
    txs: List[dict] = []

    include_receipts = bool(args.include_receipts)
    print(f"Bootstrapping blocks {from_height}..{to_height} (head={head_height})")

    for height in range(to_height, from_height - 1, -1):
        block = try_methods(
            ["chain.getBlockByHeight", "chain.getBlockByNumber", "block_getBlockByNumber"],
            [height, True, include_receipts],
            args.rpc_url,
        )
        if not isinstance(block, dict):
            print(f"Warning: block {height} not found or invalid", file=sys.stderr)
            continue

        blocks.append(block)

        block_txs = block.get("txs") or block.get("transactions") or []
        if isinstance(block_txs, list):
            for tx in block_txs:
                if isinstance(tx, dict) and (tx.get("hash") or tx.get("txHash")):
                    txs.append(tx)

        if height % 100 == 0:
            print(f"... fetched down to height {height}")

    payload = {
        "generatedAt": head.get("timeISO") or head.get("timestamp") or None,
        "chainId": normalize_chain_id(chain_id),
        "head": head,
        "blocks": blocks,
        "txs": txs,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote bootstrap payload to {args.out} ({len(blocks)} blocks, {len(txs)} txs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
