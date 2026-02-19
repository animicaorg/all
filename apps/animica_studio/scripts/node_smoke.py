#!/usr/bin/env python3
"""Node smoke test: status → (optionally start) → status → (if we started it, stop).

Run from apps/animica_studio:
    python scripts/node_smoke.py [rpc_local_url]

Safety: if the node is already running this script does NOT stop it.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from animica_studio.storage.config import load_config
from animica_studio.services.process_manager import ProcessManager


def _print_status(label: str, status: dict) -> None:
    print(f"\n[{label}]")
    print(f"  running      : {status.get('running')}")
    print(f"  pid          : {status.get('pid')}")
    print(f"  rpc_reachable: {status.get('rpc_reachable')}")
    lines = status.get("last_log_lines", [])
    if lines:
        print(f"  last log ({len(lines)} lines):")
        for l in lines[-5:]:
            print(f"    {l}")


def main() -> int:
    cfg = load_config()
    profile = cfg.get_active_profile()

    if len(sys.argv) > 1:
        rpc_url = sys.argv[1]
    else:
        rpc_url = profile.node.rpc_local_url

    print(f"Node smoke test — rpc_url={rpc_url}")
    print(f"  start_cmd={profile.node.start_cmd}")

    pm = ProcessManager(
        start_cmd=profile.node.start_cmd,
        rpc_url=rpc_url,
    )

    # Step 1: initial status
    status = pm.status()
    _print_status("Initial status", status)

    was_running = status["running"]
    started_by_us = False

    if not was_running:
        print("\nNode not running — attempting to start …")
        status = pm.start()
        _print_status("After start", status)
        started_by_us = status.get("running", False)
    else:
        print("\nNode already running — skipping start.")

    # Step 2: status check
    status = pm.status()
    _print_status("Current status", status)

    # Step 3: stop only if we started it
    if started_by_us:
        print("\nStopping node (we started it) …")
        result = pm.stop()
        _print_status("After stop", result)
    else:
        print("\nLeaving node running (not started by this script).")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
