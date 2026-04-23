#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from core.chain.block_import import _theta_to_target, compute_header_hash
from core.types.block import Block
from core.utils.hash import ZERO32
from p2p.deps import AsyncP2PDeps, P2PDeps
from p2p.node.p2p_service import P2PService
from p2p.tests import free_port, tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[1] / "core" / "genesis" / "genesis.json"

os.environ.setdefault("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _make_deps(tmp_path: Path, name: str) -> tuple[P2PDeps, AsyncP2PDeps]:
    db_path = tmp_path / f"{name}.db"
    sync_deps = P2PDeps.open(f"sqlite:///{db_path}", str(GENESIS_PATH))
    return sync_deps, AsyncP2PDeps(sync_deps)


def _mine_blocks(sync_deps: P2PDeps, count: int) -> None:
    height, head_hash = sync_deps.head()
    header = sync_deps.header_by_hash(head_hash) if head_hash else None
    if header is None:
        header = sync_deps.header_by_number(0)
    assert header is not None

    timestamp = int(getattr(header, "timestamp", 0))
    for _ in range(count):
        timestamp += 1
        target = _theta_to_target(int(getattr(header, "thetaMicro", 0)))
        child = None
        for nonce in range(0, 10000):
            candidate = header.build_child(
                timestamp=timestamp,
                state_root=header.stateRoot,
                txs_root=ZERO32,
                receipts_root=ZERO32,
                proofs_root=ZERO32,
                da_root=ZERO32,
                nonce=nonce,
                extra=b"",
            )
            if int.from_bytes(compute_header_hash(candidate), "big") <= target:
                child = candidate
                break
        assert child is not None, "failed to mine test block"
        block = Block(header=child, txs=(), proofs=(), receipts=None)
        ok, reason = sync_deps.import_block(block)
        assert ok, reason
        header = child


async def _wait_for_height(
    deps: AsyncP2PDeps, height: int, timeout: float = 20.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur, _ = await deps.head()
        if cur >= height:
            return True
        await asyncio.sleep(0.2)
    return False


async def _wait_for_peers(node: P2PService, count: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if node.peer_count() >= count:
            return True
        await asyncio.sleep(0.1)
    return False


async def _wait_for_header_responses(
    node: P2PService, timeout: float = 10.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = node.sync_status_snapshot(refresh=True)
        if (
            status.last_header_response_count > 0
            or status.headers_accepted_total > 0
            or status.best_header_height > 0
        ):
            return True
        await asyncio.sleep(0.2)
    return False


async def _run_iteration(iteration: int, sync_timeout_s: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"sync-soak-{iteration:03d}-") as td:
        tmp_path = Path(td)

        deps_a_sync, deps_a = _make_deps(tmp_path, f"node_a_{iteration}")
        deps_b_sync, deps_b = _make_deps(tmp_path, f"node_b_{iteration}")

        addr_a = tcp_multiaddr(free_port())
        addr_b = tcp_multiaddr(free_port())

        node_a = P2PService(
            listen_addrs=[addr_a],
            seeds=[],
            chain_id=deps_a_sync.chain_id,
            deps=deps_a,
            peerstore_path=str(tmp_path / "node_a" / "p2p"),
        )
        node_b = P2PService(
            listen_addrs=[addr_b],
            seeds=[addr_a],
            chain_id=deps_b_sync.chain_id,
            deps=deps_b,
            peerstore_path=str(tmp_path / "node_b" / "p2p"),
        )

        started = time.monotonic()
        await node_a.start()
        await node_b.start()
        try:
            _mine_blocks(deps_a_sync, 3)
            await node_b.dial(addr_a)
            peers_ok = await _wait_for_peers(node_b, 1, timeout=10.0)
            height_ok = await _wait_for_height(deps_b, 3, timeout=sync_timeout_s)
            headers_ok = await _wait_for_header_responses(node_b, timeout=10.0)

            snapshot = node_b.sync_status_snapshot(refresh=True)
            elapsed_s = time.monotonic() - started

            reasons: list[str] = []
            if not peers_ok:
                reasons.append("peer_connect_timeout")
            if not height_ok:
                reasons.append("height_sync_timeout")
            if not headers_ok:
                reasons.append("headers_response_timeout")
            if snapshot.stall_reason:
                reasons.append(f"stall:{snapshot.stall_reason}")
            if snapshot.phase == "STALLED":
                reasons.append("phase_stalled")
            if snapshot.fatal_error:
                reasons.append("fatal_error")

            row = {
                "iteration": iteration,
                "ok": len(reasons) == 0,
                "failure_reasons": reasons,
                "elapsed_s": round(elapsed_s, 3),
                "phase": snapshot.phase,
                "status_reason": snapshot.status_reason,
                "stall_reason": snapshot.stall_reason,
                "stall_elapsed_s": round(snapshot.stall_elapsed_s, 3),
                "synchronized": bool(snapshot.synchronized),
                "at_tip": bool(snapshot.at_tip),
                "head_height": int(snapshot.head_height),
                "target_height": snapshot.target_height,
                "headers_req_timeout": int(node_b._stats.get("headers_req_timeout", 0)),
                "blocks_req_timeout": int(node_b._stats.get("blocks_req_timeout", 0)),
                "sync_inflight_reset": int(node_b._stats.get("sync_inflight_reset", 0)),
                "sync_stall_detected": int(node_b._stats.get("sync_stall_detected", 0)),
                "stall_recoveries": int(node_b._stats.get("stall_recoveries", 0)),
                "recovery_attempts": int(snapshot.recovery_attempts),
                "last_recovery_action": snapshot.last_recovery_action,
                "last_header_error": snapshot.last_header_error,
                "last_block_error": snapshot.last_block_error,
                "eligible_peers_for_headers": list(snapshot.eligible_peers_for_headers),
                "eligible_peers_for_blocks": list(snapshot.eligible_peers_for_blocks),
                "in_flight_headers": int(snapshot.in_flight_headers),
                "in_flight_blocks": int(snapshot.in_flight_blocks),
                "queued_blocks_count": int(snapshot.queued_blocks_count),
            }
            return row
        finally:
            await node_b.stop()
            await node_a.stop()


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for row in rows if row.get("ok"))
    failed = total - passed
    elapsed = [float(row.get("elapsed_s", 0.0)) for row in rows if row.get("ok")]

    failure_reasons = Counter()
    stall_reasons = Counter()
    last_header_errors = Counter()
    last_block_errors = Counter()
    recovery_actions = Counter()
    totals = Counter()
    phase_counts = Counter()
    at_tip_count = 0
    synchronized_count = 0

    for row in rows:
        if row.get("at_tip"):
            at_tip_count += 1
        if row.get("synchronized"):
            synchronized_count += 1
        if row.get("phase"):
            phase_counts[str(row["phase"])] += 1
        for reason in row.get("failure_reasons", []):
            failure_reasons[str(reason)] += 1
        if row.get("stall_reason"):
            stall_reasons[str(row["stall_reason"])] += 1
        if row.get("last_header_error"):
            last_header_errors[str(row["last_header_error"])] += 1
        if row.get("last_block_error"):
            last_block_errors[str(row["last_block_error"])] += 1
        if row.get("last_recovery_action"):
            recovery_actions[str(row["last_recovery_action"])] += 1
        totals["headers_req_timeout"] += int(row.get("headers_req_timeout", 0))
        totals["blocks_req_timeout"] += int(row.get("blocks_req_timeout", 0))
        totals["sync_inflight_reset"] += int(row.get("sync_inflight_reset", 0))
        totals["sync_stall_detected"] += int(row.get("sync_stall_detected", 0))
        totals["stall_recoveries"] += int(row.get("stall_recoveries", 0))
        totals["recovery_attempts"] += int(row.get("recovery_attempts", 0))

    latency = {
        "min_s": round(min(elapsed), 3) if elapsed else 0.0,
        "median_s": round(statistics.median(elapsed), 3) if elapsed else 0.0,
        "p95_s": round(_percentile(elapsed, 95), 3) if elapsed else 0.0,
        "max_s": round(max(elapsed), 3) if elapsed else 0.0,
        "mean_s": round(statistics.mean(elapsed), 3) if elapsed else 0.0,
    }

    return {
        "iterations": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": round((100.0 * passed / total), 2) if total else 0.0,
        "at_tip_rate_pct": round((100.0 * at_tip_count / total), 2) if total else 0.0,
        "synchronized_rate_pct": round((100.0 * synchronized_count / total), 2)
        if total
        else 0.0,
        "synced_phase_rate_pct": round((100.0 * phase_counts.get("SYNCED", 0) / total), 2)
        if total
        else 0.0,
        "latency": latency,
        "totals": dict(totals),
        "phase_counts": dict(phase_counts),
        "failure_reasons": dict(failure_reasons),
        "stall_reasons": dict(stall_reasons),
        "last_header_errors": dict(last_header_errors),
        "last_block_errors": dict(last_block_errors),
        "recovery_actions": dict(recovery_actions),
    }


async def _run_soak(
    *,
    iterations: int,
    sync_timeout_s: float,
    stop_on_failure: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for iteration in range(1, iterations + 1):
        row = await _run_iteration(iteration, sync_timeout_s=sync_timeout_s)
        rows.append(row)
        status = "PASS" if row.get("ok") else "FAIL"
        print(
            f"ITER:{iteration} {status} elapsed={row.get('elapsed_s')}s "
            f"phase={row.get('phase')} stall={row.get('stall_reason')} "
            f"recover={row.get('last_recovery_action')}"
        )
        if stop_on_failure and not row.get("ok"):
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run repeated two-node sync and emit a health report."
    )
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--sync-timeout-s", type=float, default=20.0)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    if not args.verbose:
        logging.basicConfig(level=logging.ERROR)

    started = time.time()
    rows = asyncio.run(
        _run_soak(
            iterations=max(1, int(args.iterations)),
            sync_timeout_s=max(1.0, float(args.sync_timeout_s)),
            stop_on_failure=bool(args.stop_on_failure),
        )
    )
    report = _summarize(rows)
    report["started_at_epoch"] = started
    report["finished_at_epoch"] = time.time()
    report["iterations_executed"] = len(rows)
    if args.include_raw:
        report["raw_iterations"] = rows

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print("\n=== Sync Soak Report ===")
    print(rendered)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"\nWrote report: {out_path}")

    return 0 if int(report.get("failed", 0)) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
