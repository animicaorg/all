# -*- coding: utf-8 -*-
"""
soak_p2p_mesh.py
================

Peer churn & gossip health soak for a multi-node mesh.

This tool opens a configurable number of **WebSocket** subscriber clients
against each node's WS hub (typically the main RPC app at `/ws`), subscribes
to topics (default: `newHeads`, `pendingTxs`), and then *churns* connections
(open/close/reopen) while measuring:

- Connection attempts / successes / failures per node
- Subscription round-trip latency (send sub → first event)
- Event rates per topic
- **Gossip skew** for `newHeads`: time spread between earliest and latest
  arrival across nodes for the *same* block (by hash/number)
- Out-of-order or skipped head numbers per node
- Keepalive behavior (pings)

The script is intentionally tolerant to slight schema differences:
it supports JSON-RPC style frames (default) and a simple `{op:"sub",topic:"…"}`.
You can also customize the subscription method and payload keys.

Dependencies
------------
    pip install websockets msgspec

Examples
--------
# 3 clients per node, churn 20% of conns every 30s, run for 3 minutes
python tests/load/soak_p2p_mesh.py \
  --ws ws://127.0.0.1:8545/ws --ws ws://127.0.0.1:8546/ws \
  --clients-per-node 3 --duration 180 --churn-every 30 --churn-fraction 0.2

# Use a non-standard subscribe method & key names
python tests/load/soak_p2p_mesh.py \
  --ws ws://node:8545/ws --sub-method subscribe --topic-key topic --id-key id

Output
------
Prints a final JSON object with per-node + global stats to stdout.
Progress lines are printed to stderr every --progress-every seconds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import msgspec
import websockets
from websockets.client import WebSocketClientProtocol

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


class Hist:
    """Fixed buckets in milliseconds with simple percentile summaries."""

    def __init__(self, bounds_ms: Optional[List[float]] = None) -> None:
        if bounds_ms is None:
            bounds_ms = [
                5,
                10,
                20,
                30,
                50,
                75,
                100,
                150,
                200,
                300,
                400,
                600,
                800,
                1000,
                1500,
                2000,
                3000,
                5000,
                8000,
                12000,
                20000,
            ]
        self.bounds = list(bounds_ms)
        self.counts = [0] * len(self.bounds)
        self.overflow = 0
        self.n = 0

    def observe(self, ms: float) -> None:
        self.n += 1
        for i, ub in enumerate(self.bounds):
            if ms <= ub:
                self.counts[i] += 1
                return
        self.overflow += 1

    def _quantile(self, q: float) -> float:
        if self.n == 0:
            return 0.0
        target = int(max(0, min(self.n - 1, round(q * (self.n - 1)))))
        cum = 0
        for i, c in enumerate(self.counts):
            cum += c
            if cum > target:
                return float(self.bounds[i])
        return float(self.bounds[-1] * 1.5)

    def summary(self) -> Dict[str, float]:
        return {
            "p50_ms": self._quantile(0.50),
            "p90_ms": self._quantile(0.90),
            "p99_ms": self._quantile(0.99),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bounds_ms": self.bounds,
            "counts": self.counts,
            "overflow": self.overflow,
            "n": self.n,
        } | self.summary()


def get_path(d: Any, path: Optional[str]) -> Any:
    """Dotted-path getter: returns None if any segment is missing."""
    if d is None or path is None or not path:
        return None
    cur = d
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


# ---------------------------------------------------------------------------
# Config / Models
# ---------------------------------------------------------------------------


@dataclass
class WsNode:
    url: str
    name: str


@dataclass
class SubConfig:
    jsonrpc: bool = True  # True → JSON-RPC style subscribe
    sub_method: str = "subscribe"  # JSON-RPC method name
    topic_key: str = "topic"  # key name for topic in params/payload
    id_key: str = "id"  # request id field (JSON-RPC)
    result_key: str = "result"  # where JSON-RPC result lands (if needed)
    topics: Tuple[str, ...] = ("newHeads", "pendingTxs")
    # For event extraction
    head_hash_paths: Tuple[str, ...] = (
        "hash",
        "block.hash",
        "params.hash",
        "params.block.hash",
    )
    head_num_paths: Tuple[str, ...] = (
        "number",
        "block.number",
        "params.number",
        "params.block.number",
    )


@dataclass
class ConnStats:
    attempts: int = 0
    opens: int = 0
    closes: int = 0
    failures: int = 0
    sub_rtt_ms: Hist = field(default_factory=Hist)
    events: Dict[str, int] = field(
        default_factory=lambda: {"newHeads": 0, "pendingTxs": 0}
    )
    out_of_order: int = 0
    last_number: Optional[int] = None


@dataclass
class MeshState:
    per_node: Dict[str, ConnStats] = field(default_factory=dict)
    gossip_skew_ms: Hist = field(
        default_factory=lambda: Hist(
            bounds_ms=[
                1,
                2,
                3,
                5,
                8,
                13,
                21,
                34,
                55,
                89,
                144,
                233,
                377,
                610,
                987,
                1597,
                2584,
            ]
        )
    )  # Fibonacci-ish
    first_event_at: Dict[str, float] = field(
        default_factory=dict
    )  # key: block_id (hash or num) → first ts
    last_event_at: Dict[str, float] = field(
        default_factory=dict
    )  # latest arrival per block
    block_seen_on: Dict[str, set] = field(
        default_factory=dict
    )  # per block → set(node_names)
    first_sub_seen: Dict[str, float] = field(
        default_factory=dict
    )  # per-connection token → first event ts


# ---------------------------------------------------------------------------
# WS client worker
# ---------------------------------------------------------------------------


class JsonEncoder(msgspec.Struct):
    jsonrpc: str = "2.0"
    id: int = 0
    method: str = ""
    params: Any = None


async def subscribe(
    ws: WebSocketClientProtocol, cfg: SubConfig, topic: str, req_id: int
) -> Tuple[bool, float]:
    """
    Send a subscription and wait for the first event on that topic to measure sub RTT.
    We do NOT assume a specific ack frame; we measure time to first matching event.
    """
    # Send subscription
    t0 = time.perf_counter()
    if cfg.jsonrpc:
        payload = {
            "jsonrpc": "2.0",
            cfg.id_key: req_id,
            "method": cfg.sub_method,
            "params": {cfg.topic_key: topic},
        }
    else:
        payload = {"op": "sub", cfg.topic_key: topic}
    await ws.send(msgspec.json.encode(payload).decode())

    # Wait for first event that looks like the topic (best-effort)
    # We allow a brief grace to filter other noise.
    deadline = t0 + 10.0
    while True:
        if time.perf_counter() > deadline:
            return False, (time.perf_counter() - t0) * 1000.0
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            return False, (time.perf_counter() - t0) * 1000.0
        if not isinstance(msg, str):
            # ignore binary pings/frames
            continue
        try:
            jd = msgspec.json.decode(msg)
        except Exception:
            continue
        # Try to match a topic hint quickly
        txt = json.dumps(jd, separators=(",", ":"))[:256]
        if topic in txt or (
            isinstance(jd, dict)
            and (jd.get("topic") == topic or jd.get("method") == topic)
        ):
            dt_ms = (time.perf_counter() - t0) * 1000.0
            return True, dt_ms


def pick_block_id(
    jd: Dict[str, Any], cfg: SubConfig
) -> Tuple[Optional[str], Optional[int]]:
    blk_hash: Optional[str] = None
    blk_num: Optional[int] = None
    for p in cfg.head_hash_paths:
        v = get_path(jd, p)
        if isinstance(v, str) and v:
            blk_hash = v
            break
    for p in cfg.head_num_paths:
        v = get_path(jd, p)
        if isinstance(v, int):
            blk_num = v
            break
        if isinstance(v, str) and v.isdigit():
            blk_num = int(v)
            break
    # Prefer hash if available, else number
    key = blk_hash or (str(blk_num) if blk_num is not None else None)
    return key, blk_num


async def conn_worker(
    node: WsNode,
    token: str,
    topics: Tuple[str, ...],
    cfg: SubConfig,
    mesh: MeshState,
    ping_every: float,
    churn_event: asyncio.Event,
    lifespan_s: float,
) -> None:
    mesh.per_node.setdefault(node.name, ConnStats())
    stats = mesh.per_node[node.name]
    stats.attempts += 1

    try:
        async with websockets.connect(
            node.url, max_size=8 * 1024 * 1024, ping_interval=None
        ) as ws:
            stats.opens += 1
            # Subscribe to all topics and measure first-event RTT for each
            req_id = int((time.time() * 1000) % 1_000_000)
            for t in topics:
                ok, dt = await subscribe(ws, cfg, t, req_id)
                stats.sub_rtt_ms.observe(dt)
                req_id += 1

            start = time.perf_counter()
            last_ping = start
            mesh.first_sub_seen[token] = start

            while True:
                # Churn trigger or lifespan expiry?
                now = time.perf_counter()
                if churn_event.is_set() or (now - start) > lifespan_s:
                    break

                # Keepalive ping
                if ping_every > 0 and (now - last_ping) >= ping_every:
                    try:
                        await ws.ping()
                    finally:
                        last_ping = now

                # Receive with short timeout to allow pings/churn checks
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    break

                if not isinstance(msg, str):
                    continue
                try:
                    jd = msgspec.json.decode(msg)
                except Exception:
                    continue

                # Topic heuristics
                topic = None
                if isinstance(jd, dict):
                    topic = (
                        jd.get("topic")
                        or jd.get("method")
                        or get_path(jd, "params.topic")
                    )
                if topic in stats.events:
                    stats.events[topic] += 1

                # Track newHeads gossip skew
                if topic == "newHeads":
                    blk_id, blk_num = pick_block_id(jd, cfg)
                    if blk_num is not None:
                        last = stats.last_number
                        if last is not None and blk_num < last:
                            stats.out_of_order += 1
                        stats.last_number = max(blk_num, last or blk_num)
                    if blk_id:
                        ts = time.perf_counter()
                        first = mesh.first_event_at.get(blk_id)
                        if first is None:
                            mesh.first_event_at[blk_id] = ts
                            mesh.last_event_at[blk_id] = ts
                            mesh.block_seen_on[blk_id] = {node.name}
                        else:
                            mesh.last_event_at[blk_id] = ts
                            mesh.block_seen_on.setdefault(blk_id, set()).add(node.name)
                            skew_ms = (mesh.last_event_at[blk_id] - first) * 1000.0
                            if skew_ms >= 0:
                                mesh.gossip_skew_ms.observe(skew_ms)

    except Exception:
        stats.failures += 1
    finally:
        stats.closes += 1


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_mesh(
    nodes: List[WsNode],
    clients_per_node: int,
    cfg: SubConfig,
    duration_s: float,
    churn_every_s: float,
    churn_fraction: float,
    min_lifespan_s: float,
    max_lifespan_s: float,
    ping_every_s: float,
    progress_every_s: float,
    seed: int,
) -> Dict[str, Any]:
    rnd = random.Random(seed)
    mesh = MeshState()

    # Active connections registry
    active: Dict[str, Tuple[WsNode, asyncio.Task, asyncio.Event, float]] = {}
    # token → (node, task, churn_event, started_at)

    async def spawn_one(node: WsNode) -> None:
        token = f"{node.name}#{int(time.time()*1e6)}#{rnd.randrange(1<<30)}"
        churn_ev = asyncio.Event()
        lifespan = rnd.uniform(min_lifespan_s, max_lifespan_s)
        task = asyncio.create_task(
            conn_worker(
                node=node,
                token=token,
                topics=cfg.topics,
                cfg=cfg,
                mesh=mesh,
                ping_every=ping_every_s,
                churn_event=churn_ev,
                lifespan_s=lifespan,
            )
        )
        active[token] = (node, task, churn_ev, time.perf_counter())
        task.add_done_callback(lambda _t: active.pop(token, None))

    async def maintain_target_pool() -> None:
        # Ensure target concurrency: clients_per_node per node
        per_node_counts: Dict[str, int] = {n.name: 0 for n in nodes}
        for token, (node, *_rest) in active.items():
            per_node_counts[node.name] = per_node_counts.get(node.name, 0) + 1
        for node in nodes:
            need = clients_per_node - per_node_counts.get(node.name, 0)
            for _ in range(max(0, need)):
                await spawn_one(node)

    start = time.perf_counter()
    next_progress = start + progress_every_s
    next_churn = start + churn_every_s if churn_every_s > 0 else float("inf")
    deadline = start + duration_s

    # Initial fill
    await maintain_target_pool()

    while True:
        now = time.perf_counter()
        if now >= deadline:
            break

        # Progress
        if now >= next_progress:
            # Basic per-node snapshot
            lines = []
            for node in nodes:
                st = mesh.per_node.get(node.name) or ConnStats()
                heads = st.events.get("newHeads", 0)
                pnd = st.events.get("pendingTxs", 0)
                p = st.sub_rtt_ms.summary()
                lines.append(
                    f"{node.name}: conns open={st.opens-st.closes:+} attempts={st.attempts} "
                    f"subRTT p50={p['p50_ms']:.0f} p90={p['p90_ms']:.0f} p99={p['p99_ms']:.0f} "
                    f"heads={heads} pending={pnd} failures={st.failures} ooo={st.out_of_order}"
                )
            skew = mesh.gossip_skew_ms.summary()
            print(
                f"[{now-start:6.1f}s] active={len(active)} "
                f"gossip-skew p50={skew['p50_ms']:.1f}ms p90={skew['p90_ms']:.1f}ms p99={skew['p99_ms']:.1f}ms\n  "
                + "\n  ".join(lines),
                file=sys.stderr,
                flush=True,
            )
            next_progress = now + progress_every_s

        # Churn: flip a fraction of connections
        if now >= next_churn:
            if active:
                to_flip = max(1, int(len(active) * churn_fraction))
                for token in rnd.sample(
                    list(active.keys()), k=min(to_flip, len(active))
                ):
                    node, task, churn_ev, _ts = active.get(token, (None, None, None, None))  # type: ignore
                    if churn_ev is not None:
                        churn_ev.set()
                # give them a bit to close
                await asyncio.sleep(0.2)
            # Refill to target
            await maintain_target_pool()
            next_churn = now + churn_every_s

        # Top up if something died naturally
        await maintain_target_pool()

        await asyncio.sleep(0.05)

    # Drain all
    for _token, (_node, task, churn_ev, _ts) in list(active.items()):
        churn_ev.set()
    if active:
        await asyncio.gather(
            *[t for (_n, t, _e, _s) in active.values()], return_exceptions=True
        )

    # Final summary JSON
    per_node_out: Dict[str, Any] = {}
    for node in nodes:
        st = mesh.per_node.get(node.name) or ConnStats()
        per_node_out[node.name] = {
            "attempts": st.attempts,
            "opens": st.opens,
            "closes": st.closes,
            "failures": st.failures,
            "sub_rtt_ms": st.sub_rtt_ms.to_dict(),
            "events": st.events,
            "out_of_order": st.out_of_order,
        }

    out = {
        "case": "load.soak_p2p_mesh",
        "params": {
            "clients_per_node": clients_per_node,
            "duration_s": duration_s,
            "churn_every_s": churn_every_s,
            "churn_fraction": churn_fraction,
            "min_lifespan_s": min_lifespan_s,
            "max_lifespan_s": max_lifespan_s,
            "ping_every_s": ping_every_s,
            "topics": list(cfg.topics),
            "jsonrpc": cfg.jsonrpc,
            "sub_method": cfg.sub_method,
            "topic_key": cfg.topic_key,
        },
        "gossip_skew_ms": mesh.gossip_skew_ms.to_dict(),
        "per_node": per_node_out,
    }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Peer churn & gossip health over WS hubs.")
    ap.add_argument(
        "--ws",
        action="append",
        required=True,
        help="WebSocket URL for a node (repeatable)",
    )
    ap.add_argument(
        "--name", action="append", help="Optional human name per --ws in order"
    )
    ap.add_argument(
        "--clients-per-node",
        type=int,
        default=2,
        help="Concurrent clients per node (default: 2)",
    )
    ap.add_argument(
        "--duration",
        type=float,
        default=120.0,
        help="Run duration seconds (default: 120)",
    )
    ap.add_argument(
        "--progress-every",
        type=float,
        default=5.0,
        help="Progress print interval seconds",
    )
    ap.add_argument(
        "--ping-every",
        type=float,
        default=15.0,
        help="Send WS ping every N seconds (0 disables)",
    )
    ap.add_argument(
        "--seed", type=int, default=20240913, help="Random seed for churn and lifespans"
    )

    # Churn controls
    ap.add_argument(
        "--churn-every",
        type=float,
        default=30.0,
        help="How often to churn a fraction of connections (default: 30s)",
    )
    ap.add_argument(
        "--churn-fraction",
        type=float,
        default=0.2,
        help="Fraction of active conns to close on each churn (default: 0.2)",
    )
    ap.add_argument(
        "--min-lifespan",
        type=float,
        default=20.0,
        help="Min lifespan for a connection before natural close (default: 20s)",
    )
    ap.add_argument(
        "--max-lifespan",
        type=float,
        default=90.0,
        help="Max lifespan for a connection before natural close (default: 90s)",
    )

    # Subscription / payload config
    ap.add_argument(
        "--jsonrpc",
        type=int,
        default=1,
        help="Use JSON-RPC style subscribe (1) or plain op (0)",
    )
    ap.add_argument(
        "--sub-method",
        default="subscribe",
        help="JSON-RPC subscription method name (default: subscribe)",
    )
    ap.add_argument(
        "--topic-key",
        default="topic",
        help="Key name for the topic in params/payload (default: topic)",
    )
    ap.add_argument(
        "--id-key", default="id", help="JSON-RPC request id field name (default: id)"
    )
    ap.add_argument(
        "--topics",
        default="newHeads,pendingTxs",
        help="Comma-separated topics to subscribe (default: newHeads,pendingTxs)",
    )

    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.clients_per_node <= 0 or args.duration <= 0:
        print("clients-per-node and duration must be > 0", file=sys.stderr)
        return 2
    if not (0.0 <= args.churn_fraction <= 1.0):
        print("--churn-fraction must be between 0 and 1", file=sys.stderr)
        return 2
    if args.max_lifespan < args.min_lifespan:
        print("--max-lifespan must be >= --min-lifespan", file=sys.stderr)
        return 2

    nodes: List[WsNode] = []
    for i, url in enumerate(args.ws):
        name = args.name[i] if args.name and i < len(args.name) else f"node{i+1}"
        nodes.append(WsNode(url=url, name=name))

    cfg = SubConfig(
        jsonrpc=bool(args.jsonrpc),
        sub_method=args.sub_method,
        topic_key=args.topic_key,
        id_key=args.id_key,
        topics=tuple([t.strip() for t in args.topics.split(",") if t.strip()]),
    )

    result = asyncio.run(
        run_mesh(
            nodes=nodes,
            clients_per_node=int(args.clients_per_node),
            cfg=cfg,
            duration_s=float(args.duration),
            churn_every_s=float(args.churn_every),
            churn_fraction=float(args.churn_fraction),
            min_lifespan_s=float(args.min_lifespan),
            max_lifespan_s=float(args.max_lifespan),
            ping_every_s=float(args.ping_every),
            progress_every_s=float(args.progress_every),
            seed=int(args.seed),
        )
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
