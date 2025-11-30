#!/usr/bin/env python3
"""
Animica mempool replay tool

Replay transactions from a file into a node at a controlled rate to load-test
admission, JSON-RPC, and mempool behavior.

Features
- Reads JSON or CBOR snapshots (supports output from mempool.cli.flush)
- Flexible extraction of raw CBOR tx bytes (hex string or bytes)
- Token-bucket rate control with optional jitter and ramp-up
- Concurrent submitters (thread pool, stdlib-only)
- Periodic stats and final latency summary
- Shuffle, loop, max-count, and dry-run modes

Examples
  # Basic: 100 tx/s, 8 workers, from a JSON file created by mempool.cli.flush
  python -m mempool.cli.replay --rpc http://127.0.0.1:8645/rpc \
      --rate 100 --concurrency 8 --input /tmp/mempool.json

  # From canonical CBOR snapshot, with jitter and ramp over 10s
  python -m mempool.cli.replay --rpc http://127.0.0.1:8645/rpc \
      --rate 250 --concurrency 16 --input /tmp/mempool.cbor \
      --jitter 0.20 --ramp-seconds 10

  # Randomize order, send only 5k txs, print progress every 2s
  python -m mempool.cli.replay --rpc http://127.0.0.1:8645/rpc \
      --rate 500 --concurrency 12 --input txs.json \
      --shuffle --max 5000 --progress 2

Input expectations
- JSON:
    {
      "entries": [
        {"raw": "0x...."},                # preferred
        {"tx": {"raw": "0x...."}},        # ok
        {"raw": "...."},                  # hex without 0x also ok
        ... or a list ["0x....", "..."]   # also accepted
      ]
    }
  Or a top-level list of entries in any of the above shapes.
- CBOR:
  The top-level must be either:
    - an array of byte strings (each one a CBOR-encoded Tx),
    - or a map with key "entries" -> array of maps where "raw" is a byte string
      or hex string.

Notes
- This tool uses only stdlib HTTP (urllib) with a small thread pool.
- It sends JSON-RPC method 'tx.sendRawTransaction' with a single hex param.
- The raw parameter should be CBOR-encoded Tx bytes, hex-encoded (0x-prefixed).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from itertools import count
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Optional CBOR reader (for CBOR snapshots)
try:
    import cbor2  # type: ignore
except Exception:  # pragma: no cover
    cbor2 = None  # type: ignore[assignment]

Json = Union[dict, list, str, int, float, bool, None]

# --------- I/O helpers ---------


def _load_json(path: str) -> Json:
    if path == "-":
        return json.loads(sys.stdin.read())
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _load_cbor(path: str) -> Any:
    if cbor2 is None:
        raise SystemExit(
            "CBOR input requested but 'cbor2' is not installed. Install via: pip install cbor2"
        )
    with open(path, "rb") as f:
        return cbor2.load(f)


def _normalize_hex(x: Union[str, bytes, bytearray]) -> Optional[str]:
    """
    Convert various representations to 0x-prefixed hex string.
    Return None if not representable.
    """
    if isinstance(x, (bytes, bytearray)):
        return "0x" + bytes(x).hex()
    if isinstance(x, str):
        s = x.strip()
        if s.startswith("0x") or s.startswith("0X"):
            # quick sanity: even length after 0x
            body = s[2:]
            if len(body) % 2 == 1:
                # pad if odd-length hex (be forgiving)
                s = "0x0" + body
            return s.lower()
        # treat as bare hex
        try:
            _ = bytes.fromhex(s)
            if len(s) % 2 == 1:
                s = "0" + s
            return "0x" + s.lower()
        except ValueError:
            return None
    return None


def _extract_entries_from_json(blob: Json) -> List[Json]:
    if isinstance(blob, list):
        return list(blob)
    if isinstance(blob, dict):
        if "entries" in blob and isinstance(blob["entries"], list):
            return list(blob["entries"])
        # fall back: values may be a dict keyed by hash -> entry
        vals = [v for v in blob.values() if isinstance(v, (list, dict))]
        if vals and isinstance(vals[0], list):
            return list(vals[0])
    return []


def _entries_to_hex(entries: Sequence[Json]) -> List[str]:
    """
    Attempt to turn arbitrary entry shapes into 0x-hex strings of CBOR-encoded Txs.
    Accepted forms:
      - entry is a string "0x..hex.."
      - entry is a dict with "raw" (hex string or bytes)
      - entry is a dict with "tx" containing {"raw": "..."}
      - entry is raw bytes (CBOR) if CBOR snapshot provided
    """
    out: List[str] = []
    for e in entries:
        # Direct hex string
        if isinstance(e, str):
            hx = _normalize_hex(e)
            if hx:
                out.append(hx)
                continue
        # Raw bytes (from a CBOR list of byte strings)
        if isinstance(e, (bytes, bytearray)):
            out.append("0x" + bytes(e).hex())
            continue
        # Dict with common layouts
        if isinstance(e, dict):
            # Most canonical: {"raw": "..."} or {"raw": <bytes>}
            if "raw" in e:
                hx = _normalize_hex(e["raw"])
                if hx:
                    out.append(hx)
                    continue
            # Nested: {"tx": {"raw": "..."}}
            tx = e.get("tx")
            if isinstance(tx, dict) and "raw" in tx:
                hx = _normalize_hex(tx["raw"])
                if hx:
                    out.append(hx)
                    continue
            # Some dumps may use "cbor" key for bytes
            if "cbor" in e:
                hx = _normalize_hex(e["cbor"])
                if hx:
                    out.append(hx)
                    continue
        # If we get here, we couldn't parse that entry; skip it.
    return out


def _load_inputs(path: str) -> List[str]:
    """
    Load input file (json or cbor) and return a list of 0x-hex strings.
    """
    if path.endswith(".cbor") or path.endswith(".cbor2"):
        blob = _load_cbor(path)
        # CBOR may be list[bytes] or {entries: list[ {raw: bytes/hex} ]}
        if isinstance(blob, list):
            return _entries_to_hex(blob)
        if isinstance(blob, dict) and "entries" in blob:
            return _entries_to_hex(blob["entries"])  # type: ignore[index]
        # last resort: if it's a dict of byte strings
        return _entries_to_hex([blob])
    else:
        blob = _load_json(path)
        if isinstance(blob, list):
            entries = blob
        else:
            entries = _extract_entries_from_json(blob)
        return _entries_to_hex(entries)


# --------- Rate limiting & stats ---------


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = float(rate_per_sec)
        self.capacity = max(1.0, float(burst))
        self.tokens = self.capacity
        self.t_last = time.perf_counter()
        self._lock = Lock()

    def take(self, n: float = 1.0) -> bool:
        with self._lock:
            now = time.perf_counter()
            dt = now - self.t_last
            self.t_last = now
            self.tokens = min(self.capacity, self.tokens + dt * self.rate)
            if self.tokens >= n:
                self.tokens -= n
                return True
            return False


@dataclass
class Stats:
    sent: int = 0
    ok: int = 0
    fail: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    last_report_ts: float = field(default_factory=time.time)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add_sent(self, n: int = 1) -> None:
        with self._lock:
            self.sent += n

    def add_result(self, ok: bool, latency_ms: float) -> None:
        with self._lock:
            if ok:
                self.ok += 1
            else:
                self.fail += 1
            self.latencies_ms.append(latency_ms)

    def snapshot(self) -> Tuple[int, int, int, float, float, float]:
        with self._lock:
            s, o, f = self.sent, self.ok, self.fail
            lat = list(self.latencies_ms)
        p50 = _percentile(lat, 50.0)
        p90 = _percentile(lat, 90.0)
        p99 = _percentile(lat, 99.0)
        return s, o, f, p50, p90, p99


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs_sorted = sorted(xs)
    k = max(0, min(len(xs_sorted) - 1, int(round((p / 100.0) * (len(xs_sorted) - 1)))))
    return xs_sorted[k]


# --------- RPC ---------

_rpc_id = count(1)


def _rpc_submit(
    url: str, method: str, raw_hex: str, timeout: float = 10.0
) -> Tuple[bool, Optional[str]]:
    body = {
        "jsonrpc": "2.0",
        "id": next(_rpc_id),
        "method": method,
        "params": [raw_hex],
    }
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        t0 = time.perf_counter()
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        dt_ms = (time.perf_counter() - t0) * 1000.0
    except HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:
        return False, f"RPC error: {e}"

    if "error" in payload and payload["error"]:
        err = payload["error"]
        return False, f"{err.get('code')} {err.get('message')}"
    return True, f"{dt_ms:.2f}ms"


# --------- Worker pool ---------


def _worker(
    url: str, method: str, jobs: Queue, stats: Stats, stop_ev: Event, timeout: float
) -> None:
    while not stop_ev.is_set():
        try:
            raw_hex = jobs.get(timeout=0.2)
        except Empty:
            continue
        if raw_hex is None:
            jobs.task_done()
            break
        t0 = time.perf_counter()
        ok, msg = _rpc_submit(url, method, raw_hex, timeout=timeout)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        stats.add_result(ok, dt_ms)
        jobs.task_done()


# --------- Main replay loop ---------


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay transactions into a node at a controlled rate."
    )
    ap.add_argument(
        "--rpc",
        default="http://127.0.0.1:8645/rpc",
        help="JSON-RPC endpoint (default: %(default)s)",
    )
    ap.add_argument(
        "--method",
        default="tx.sendRawTransaction",
        help="JSON-RPC method to call (default: %(default)s)",
    )
    ap.add_argument(
        "--input",
        required=True,
        help="Path to input (.json/.cbor) or '-' for stdin JSON",
    )
    ap.add_argument(
        "--rate",
        type=float,
        default=100.0,
        help="Target rate (tx/s) [token bucket] (default: %(default)s)",
    )
    ap.add_argument(
        "--burst",
        type=int,
        default=200,
        help="Bucket size (burst) (default: %(default)s)",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Worker threads (default: %(default)s)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout seconds (default: %(default)s)",
    )
    ap.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="Uniform jitter fraction on pacing (0..1). Applied to inter-arrival. (default: %(default)s)",
    )
    ap.add_argument(
        "--ramp-seconds",
        type=float,
        default=0.0,
        help="Linearly ramp from 0 to --rate over this many seconds",
    )
    ap.add_argument("--shuffle", action="store_true", help="Shuffle input order")
    ap.add_argument(
        "--loop",
        action="store_true",
        help="Loop over inputs indefinitely (until --max or --duration)",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=0,
        help="Stop after sending at most this many txs (0 = unlimited)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Stop after N seconds (0 = unlimited)",
    )
    ap.add_argument(
        "--progress",
        type=float,
        default=5.0,
        help="Print stats every N seconds (0=off) (default: %(default)s)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send RPC calls; just pace and count",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for shuffle/jitter (0 = entropy)",
    )
    args = ap.parse_args(argv)

    if args.seed:
        random.seed(args.seed)

    # Load inputs
    try:
        raws = _load_inputs(args.input)
    except Exception as e:
        raise SystemExit(f"Failed to load inputs from {args.input}: {e}") from e

    if not raws:
        raise SystemExit("No transactions found in input.")

    if args.shuffle:
        random.shuffle(raws)

    stats = Stats()
    jobs: Queue = Queue(maxsize=max(1, args.burst * 2))
    stop_ev = Event()

    # Start workers
    workers: List[Thread] = []
    if not args.dry_run:
        for _ in range(max(1, args.concurrency)):
            t = Thread(
                target=_worker,
                args=(args.rpc, args.method, jobs, stats, stop_ev, args.timeout),
                daemon=True,
            )
            t.start()
            workers.append(t)

    bucket = TokenBucket(args.rate if args.rate > 0 else 1e9, burst=args.burst)

    t_start = time.perf_counter()
    t_last_report = time.time()

    sent_total = 0
    idx = 0

    def target_rate_now() -> float:
        if args.ramp_seconds and args.ramp_seconds > 0:
            elapsed = time.perf_counter() - t_start
            frac = min(1.0, max(0.0, elapsed / args.ramp_seconds))
            return args.rate * frac
        return args.rate

    # Feeder loop
    try:
        while True:
            # Duration cap
            if args.duration > 0 and (time.perf_counter() - t_start) >= args.duration:
                break
            # Max cap
            if args.max > 0 and sent_total >= args.max:
                break

            # Rate adapt (for ramp): adjust bucket's rate dynamically
            bucket.rate = max(0.0, target_rate_now())

            # Pace: wait until we can take 1 token
            if bucket.take(1.0):
                # Jitter on inter-arrival: sleep a small randomized fraction if requested
                if args.jitter > 0:
                    base_interval = 1.0 / args.rate if args.rate > 0 else 0.0
                    if base_interval > 0:
                        jitter = (random.random() * 2 - 1) * args.jitter  # [-j, +j]
                        sleep_dt = max(0.0, base_interval * (1.0 + jitter))
                        # keep small (sub-ms sleeps are fine)
                        if sleep_dt > 0:
                            time.sleep(min(sleep_dt, 0.050))

                raw_hex = raws[idx]
                idx += 1
                if idx >= len(raws):
                    if args.loop:
                        idx = 0
                        if args.shuffle:
                            random.shuffle(raws)
                    else:
                        # No more inputs
                        if args.max == 0 or sent_total >= args.max:
                            # we're done
                            pass
                        else:
                            # Inputs exhausted before --max; stop anyway
                            pass

                stats.add_sent(1)
                sent_total += 1

                if args.dry_run:
                    # Simulate a small latency and mark ok
                    fake = 3.0 + random.random() * 2.0
                    stats.add_result(True, fake)
                else:
                    # Enqueue for workers (backpressure via queue)
                    while True:
                        try:
                            jobs.put(raw_hex, timeout=0.1)
                            break
                        except:
                            # allow graceful exit checks
                            if stop_ev.is_set():
                                break

            else:
                # Not enough tokens yet; short sleep to avoid busy spin
                time.sleep(0.001)

            # Periodic progress
            if args.progress and (time.time() - t_last_report) >= args.progress:
                t_last_report = time.time()
                s, o, f, p50, p90, p99 = stats.snapshot()
                elapsed = time.perf_counter() - t_start
                rate_achieved = (o + f) / elapsed if elapsed > 0 else 0.0
                print(
                    f"[{elapsed:8.2f}s] sent={s} ok={o} fail={f} "
                    f"rate={rate_achieved:7.2f}/s lat(ms): p50={p50:6.1f} p90={p90:6.1f} p99={p99:6.1f}",
                    file=sys.stderr,
                )

            # Exit if we've hit caps and no looping
            if args.max > 0 and sent_total >= args.max:
                break
            if not args.loop and idx >= len(raws):
                break

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.", file=sys.stderr)
    finally:
        # Drain and stop workers
        stop_ev.set()
        try:
            # Signal workers to exit
            for _ in workers:
                jobs.put_nowait(None)  # type: ignore[arg-type]
        except Exception:
            pass
        for t in workers:
            t.join(timeout=1.0)

    # Final stats
    s, o, f, p50, p90, p99 = stats.snapshot()
    elapsed = max(1e-9, time.perf_counter() - t_start)
    rate_achieved = (o + f) / elapsed
    print(
        f"\n=== Replay complete ===\n"
        f"Elapsed: {elapsed:.3f}s\n"
        f"Sent:    {s} (ok={o}, fail={f})\n"
        f"Rate:    {rate_achieved:.2f} tx/s\n"
        f"Latency: p50={p50:.1f} ms  p90={p90:.1f} ms  p99={p99:.1f} ms",
        file=sys.stderr,
    )
    return 0 if f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
