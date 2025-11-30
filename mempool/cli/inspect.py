#!/usr/bin/env python3
"""
Animica mempool inspector

Usage examples:
  # From a JSON snapshot file (array or {"entries":[...]})
  python -m mempool.cli.inspect --input /path/to/mempool_snapshot.json --top 20

  # From STDIN
  cat dump.json | python -m mempool.cli.inspect -i -

  # Try JSON-RPC (if node exposes a mempool.inspect method)
  python -m mempool.cli.inspect --rpc http://127.0.0.1:8645/rpc --top 50

The tool is intentionally backend-agnostic. It understands a few common shapes:
- Flat entries with keys like: hash, sender, nonce, size_bytes, tip, gas, priority
- Nested entries with "tx" and "meta" dicts (e.g., {"tx": {...}, "meta": {...}})
If precise fields are missing, it computes a best-effort "priority".
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import io
import json
import math
import statistics
import sys
import time
import typing as t
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Best-effort version exposure (not required)
try:
    from mempool.cli import __version__ as MEMPOOL_VER  # type: ignore
except Exception:  # pragma: no cover
    MEMPOOL_VER = "0.0.0+dev"

Json = t.Union[dict, list, str, int, float, bool, None]


@dataclasses.dataclass(frozen=True)
class EntryView:
    tx_hash: str
    sender: str
    nonce: int | None
    size_bytes: int | None
    gas: int | None
    tip_per_gas: int | None  # wei-like units if provided
    effective_priority: float  # normalized "priority" used for sorting
    created_ts: float | None  # unix seconds if available

    @property
    def short_hash(self) -> str:
        h = self.tx_hash or ""
        if h.startswith("0x"):
            h = h[2:]
        return (h[:10] + "…") if len(h) > 10 else h

    @property
    def age(self) -> str:
        if not self.created_ts:
            return "—"
        delta = max(0, time.time() - self.created_ts)
        # Friendly mm:ss if <1h, otherwise hh:mm:ss
        if delta < 3600:
            m, s = divmod(int(delta), 60)
            return f"{m:02d}:{s:02d}"
        h, rem = divmod(int(delta), 3600)
        m, s = divmod(rem, 60)
        return f"{h:d}:{m:02d}:{s:02d}"


def _get(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d:
            return d[k]
    return default


def _coerce_int(x) -> int | None:
    try:
        if x is None:
            return None
        if isinstance(x, bool):
            return int(x)
        if isinstance(x, (int,)):
            return int(x)
        if isinstance(x, (float,)):
            return int(x)
        if isinstance(x, str):
            if x.startswith("0x"):
                return int(x, 16)
            return int(x)
    except Exception:
        return None
    return None


def _coerce_float(x) -> float | None:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, str):
            return float(x)
    except Exception:
        return None
    return None


def _infer_size_bytes(entry: dict) -> int | None:
    sz = _get(entry, "size_bytes", "bytes", default=None)
    if sz is not None:
        return _coerce_int(sz)
    raw = _get(entry, "raw", "rawTx", default=None)
    if isinstance(raw, str):
        # Hex string (0x...) → bytes
        h = raw[2:] if raw.startswith("0x") else raw
        try:
            return len(bytes.fromhex(h))
        except Exception:
            return None
    # Maybe nested meta
    meta = _get(entry, "meta", default={}) or {}
    sz2 = _get(meta, "size_bytes", "bytes", default=None)
    return _coerce_int(sz2)


def _extract_entry_view(entry: dict) -> EntryView:
    # Accept both flat and nested ("tx", "meta") shapes
    tx = _get(entry, "tx", default=None) or {}
    meta = _get(entry, "meta", default=None) or {}

    tx_hash = _get(entry, "hash", "tx_hash", default=None) or _get(
        tx, "hash", "tx_hash", default=""
    )

    sender = (
        _get(entry, "sender", "from", default=None)
        or _get(tx, "from", "sender", default="")
        or "?"
    )

    nonce = _coerce_int(
        _get(entry, "nonce", default=None) or _get(tx, "nonce", default=None)
    )

    size_bytes = _infer_size_bytes(entry)

    gas = _coerce_int(
        _get(entry, "gas", "gas_limit", default=None)
        or _get(tx, "gas", "gas_limit", default=None)
    )

    tip_pg = _coerce_int(
        _get(entry, "tip", "tip_per_gas", "maxPriorityFeePerGas", default=None)
        or _get(tx, "maxPriorityFeePerGas", "tip", default=None)
        or _get(meta, "tip_per_gas", default=None)
    )

    # Priority: prefer explicit, then compute a rough heuristic.
    pr_explicit = _coerce_float(
        _get(entry, "priority", "effective_priority", default=None)
    ) or _coerce_float(_get(meta, "effective_priority", default=None))

    if pr_explicit is not None:
        priority = float(pr_explicit)
    else:
        # Heuristic: larger tip per gas wins; lightly normalize by size
        # to avoid tiny spam dominating. This is NOT consensus-critical.
        tip = tip_pg or 0
        sz = max(1, int(size_bytes or 200))  # assume ~200B default
        priority = float(tip) / math.log2(16 + sz)

    created_ts = _coerce_float(
        _get(entry, "created_ts", "timestamp", default=None)
        or _get(meta, "created_ts", "timestamp", default=None)
    )

    return EntryView(
        tx_hash=str(tx_hash or ""),
        sender=str(sender or "?"),
        nonce=nonce,
        size_bytes=size_bytes,
        gas=gas,
        tip_per_gas=tip_pg,
        effective_priority=priority,
        created_ts=created_ts,
    )


def _read_json_file(path: str) -> Json:
    if path == "-":
        data = sys.stdin.read()
        return json.loads(data)
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _flatten_entries(blob: Json) -> list[dict]:
    if isinstance(blob, list):
        return [e for e in blob if isinstance(e, dict)]
    if isinstance(blob, dict):
        # Common wrappers
        if "entries" in blob and isinstance(blob["entries"], list):
            return [e for e in blob["entries"] if isinstance(e, dict)]
        if "pool" in blob and isinstance(blob["pool"], list):
            return [e for e in blob["pool"] if isinstance(e, dict)]
        # Maybe directly keyed by hashes → objects
        vals = [v for v in blob.values() if isinstance(v, (dict, list))]
        if vals and all(isinstance(v, dict) for v in vals):
            return list(t.cast(dict, blob).values())
    return []


def _fetch_via_rpc(url: str) -> list[dict]:
    """
    Try calling a non-standard debug method `mempool.inspect`.
    Expected shape: {"entries":[ ... ]} or a raw array.
    """
    req_body = {"jsonrpc": "2.0", "id": 1, "method": "mempool.inspect", "params": []}
    data = json.dumps(req_body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise SystemExit(f"RPC HTTP error {e.code}: {e.reason}") from e
    except URLError as e:
        raise SystemExit(f"RPC connection error: {e.reason}") from e
    except Exception as e:
        raise SystemExit(f"RPC error: {e}") from e

    if "error" in payload:
        code = payload["error"].get("code")
        msg = payload["error"].get("message")
        raise SystemExit(f"RPC returned error {code}: {msg}")

    result = payload.get("result")
    if result is None:
        return []

    if isinstance(result, list):
        return [e for e in result if isinstance(e, dict)]
    if isinstance(result, dict):
        return _flatten_entries(result)
    return []


def human_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            if u == "B":
                return f"{int(x)}{u}"
            return f"{x:.2f}{u}"
        x /= 1024.0
    return f"{n}B"


def fmt_priority(p: float | None) -> str:
    if p is None:
        return "—"
    # Show in a compact format
    if abs(p) >= 1000:
        return f"{p:,.0f}"
    if abs(p) >= 10:
        return f"{p:,.1f}"
    return f"{p:.3f}"


def fmt_tip(tip_pg: int | None) -> str:
    if tip_pg is None:
        return "—"
    # Keep raw integer (wei-like) to avoid assuming units
    return f"{tip_pg}"


def compute_stats(views: list[EntryView]) -> dict[str, t.Any]:
    total = len(views)
    total_bytes = sum(v.size_bytes or 0 for v in views)
    senders = {v.sender for v in views}
    unique_senders = len(senders)

    # Priority stats
    prios = [v.effective_priority for v in views if v.effective_priority is not None]
    pr_stats = {}
    if prios:
        pr_stats = {
            "min": min(prios),
            "median": statistics.median(prios),
            "p90": (
                statistics.quantiles(prios, n=10)[-1]
                if len(prios) >= 10
                else max(prios)
            ),
            "max": max(prios),
        }

    # Tip stats (if provided)
    tips = [v.tip_per_gas for v in views if v.tip_per_gas is not None]
    tip_stats = {}
    if tips:
        tip_stats = {
            "min": min(tips),
            "median": int(statistics.median(tips)),
            "p90": (
                int(statistics.quantiles(tips, n=10)[-1])
                if len(tips) >= 10
                else max(tips)
            ),
            "max": max(tips),
        }

    return {
        "total_txs": total,
        "total_bytes": total_bytes,
        "unique_senders": unique_senders,
        "priority": pr_stats,
        "tip_per_gas": tip_stats,
    }


def print_header(title: str):
    print(title)
    print("-" * len(title))


def print_stats(stats: dict[str, t.Any]):
    tb = stats["total_bytes"]
    print(f"Total txs         : {stats['total_txs']}")
    print(f"Total size        : {human_bytes(tb)}")
    print(f"Unique senders    : {stats['unique_senders']}")
    pr = stats.get("priority") or {}
    if pr:
        print(
            f"Priority          : min={fmt_priority(pr.get('min'))} "
            f"median={fmt_priority(pr.get('median'))} "
            f"p90={fmt_priority(pr.get('p90'))} "
            f"max={fmt_priority(pr.get('max'))}"
        )
    tips = stats.get("tip_per_gas") or {}
    if tips:
        print(
            f"Tip per gas       : min={tips.get('min')} "
            f"median={tips.get('median')} "
            f"p90={tips.get('p90')} "
            f"max={tips.get('max')}"
        )


def print_top(views: list[EntryView], n: int):
    print()
    print_header(f"Top {min(n, len(views))} by effective priority")
    print(
        f"{'PRIORITY':>10}  {'TIP/pg':>10}  {'SIZE':>8}  {'AGE':>8}  {'NONCE':>7}  {'SENDER':<20}  HASH"
    )
    for v in views[:n]:
        print(
            f"{fmt_priority(v.effective_priority):>10}  "
            f"{fmt_tip(v.tip_per_gas):>10}  "
            f"{human_bytes(v.size_bytes):>8}  "
            f"{v.age:>8}  "
            f"{(str(v.nonce) if v.nonce is not None else '—'):>7}  "
            f"{v.sender:<20}  "
            f"{v.short_hash}"
        )


def print_per_sender(views: list[EntryView], max_senders: int, max_each: int):
    print()
    print_header(
        f"Per-sender queues (top {max_senders} senders by outstanding tx count)"
    )
    by_sender: dict[str, list[EntryView]] = collections.defaultdict(list)
    for v in views:
        by_sender[v.sender].append(v)
    ranked = sorted(by_sender.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for sender, entries in ranked[:max_senders]:
        es = sorted(
            entries,
            key=lambda v: (
                v.nonce if v.nonce is not None else 1 << 62,
                -v.effective_priority,
            ),
        )
        nonces = [e.nonce for e in es if e.nonce is not None]
        # Show the first gaps succinctly
        gaps = []
        if nonces:
            expect = nonces[0]
            for n in nonces:
                while expect is not None and n is not None and expect < n:
                    gaps.append(expect)
                    expect += 1
                if expect is not None and n is not None:
                    expect = n + 1
        gaps_str = " none" if not gaps else f" starts @ {gaps[0]} (first gap)"
        print(f"- {sender} — {len(entries)} txs; nonce gaps:{gaps_str}")
        print(f"  {'NONCE':>7}  {'PRIORITY':>10}  {'TIP/pg':>10}  HASH")
        for e in es[:max_each]:
            print(
                f"  {str(e.nonce) if e.nonce is not None else '—':>7}  "
                f"{fmt_priority(e.effective_priority):>10}  "
                f"{fmt_tip(e.tip_per_gas):>10}  "
                f"{e.short_hash}"
            )
        if len(es) > max_each:
            print(f"  … (+{len(es) - max_each} more)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Inspect Animica mempool snapshots")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-i", "--input", help="JSON file path or '-' for stdin")
    src.add_argument(
        "--rpc", help="JSON-RPC endpoint URL (expects non-standard 'mempool.inspect')"
    )

    p.add_argument(
        "--top", type=int, default=25, help="How many top-priority txs to print"
    )
    p.add_argument(
        "--per-sender", type=int, default=10, help="How many senders to list"
    )
    p.add_argument(
        "--each", type=int, default=10, help="How many txs to show per sender"
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable summary JSON instead of pretty tables",
    )
    p.add_argument("--version", action="store_true", help="Print tool version and exit")

    args = p.parse_args(argv)

    if args.version:
        print(f"mempool.inspect {MEMPOOL_VER}")
        return 0

    # Load entries
    if args.input:
        blob = _read_json_file(args.input)
        raw_entries = _flatten_entries(blob)
        if not raw_entries:
            print("No entries found in input JSON.", file=sys.stderr)
    else:
        raw_entries = _fetch_via_rpc(args.rpc)

    views = [_extract_entry_view(e) for e in raw_entries]
    # Sort by priority desc, break ties by (tip_pg desc, hash asc)
    views.sort(key=lambda v: (-v.effective_priority, -(v.tip_per_gas or -1), v.tx_hash))

    stats = compute_stats(views)

    if args.json:
        out = {
            "version": MEMPOOL_VER,
            "generated_at": int(time.time()),
            "stats": stats,
            "top": [dataclasses.asdict(v) for v in views[: args.top]],
            "per_sender": {
                sender: [
                    dataclasses.asdict(v)
                    for v in sorted(
                        vs, key=lambda x: (x.nonce if x.nonce is not None else 1 << 62)
                    )[: args.each]
                ]
                for sender, vs in sorted(
                    (
                        (s, [v for v in views if v.sender == s])
                        for s in {v.sender for v in views}
                    ),
                    key=lambda kv: (-len(kv[1]), kv[0]),
                )[: args.per_sender]
            },
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    # Pretty
    print_header(f"Animica Mempool — inspect (v{MEMPOOL_VER})")
    print_stats(stats)
    print_top(views, args.top)
    print_per_sender(views, args.per_sender, args.each)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
