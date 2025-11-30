#!/usr/bin/env python3
"""Kill any process listening on a given TCP port using /proc data.

This avoids dependencies on tools like lsof or ss. Example:
    python scripts/kill_port.py 8545
    python scripts/kill_port.py 8545 --signal SIGKILL
"""

from __future__ import annotations

import argparse
import os
import signal
from typing import Dict, Iterable, List, Set


def _parse_tcp_table(path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            next(f, None)  # skip header
            for line in f:
                parts = line.split()
                if len(parts) < 10:
                    continue
                rows.append(
                    {
                        "local": parts[1],
                        "state": parts[3],
                        "inode": parts[9],
                    }
                )
    except FileNotFoundError:
        return []
    return rows


def _listening_inodes_for_port(port: int) -> Set[str]:
    inodes: Set[str] = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        for row in _parse_tcp_table(path):
            try:
                _, hex_port = row["local"].split(":")
                local_port = int(hex_port, 16)
            except ValueError:
                continue
            if local_port != port:
                continue
            if row["state"].upper() == "0A":  # LISTEN
                inodes.add(row["inode"])
    return inodes


def _pids_for_inodes(inodes: Set[str]) -> Set[int]:
    pids: Set[int] = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        fd_dir = os.path.join("/proc", pid, "fd")
        try:
            for fd in os.listdir(fd_dir):
                target = os.readlink(os.path.join(fd_dir, fd))
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.add(int(pid))
                    break
        except (FileNotFoundError, PermissionError):
            continue
    return pids


def _kill_pids(pids: Iterable[int], sig: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
            print(f"Sent {sig.name} to PID {pid}")
        except ProcessLookupError:
            print(f"PID {pid} already exited")
        except PermissionError:
            print(f"Insufficient permissions to signal PID {pid}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kill processes listening on a TCP port without external tools."
    )
    parser.add_argument("port", type=int, help="TCP port to free (e.g., 8545)")
    parser.add_argument(
        "--signal", "-s", default="SIGTERM", help="Signal to send (default: SIGTERM)"
    )
    args = parser.parse_args()

    try:
        sig = signal.Signals[args.signal]
    except KeyError:
        parser.error(f"Unknown signal: {args.signal}")
        return 1

    inodes = _listening_inodes_for_port(args.port)
    if not inodes:
        print(f"No listeners found on port {args.port}")
        return 0

    pids = _pids_for_inodes(inodes)
    if not pids:
        print(f"Found sockets on port {args.port} but could not resolve owning PIDs")
        return 1

    _kill_pids(pids, sig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
