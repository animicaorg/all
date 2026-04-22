#!/usr/bin/env python3
"""Simple off-chain monitor for AICF contract job events and states."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

BASE_URL = os.getenv("AICF_API_BASE_URL", "http://127.0.0.1:8099")
INTERNAL_SECRET = os.getenv("AICF_INTERNAL_SECRET", "change-me-internal")
POLL_SECONDS = float(os.getenv("AICF_MONITOR_POLL_SECONDS", "2"))


def _request(path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        headers={
            "x-internal-secret": INTERNAL_SECRET,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    print(f"[aicf-monitor] base={BASE_URL} poll={POLL_SECONDS}s")
    while True:
        try:
            payload = _request("/internal/chain-events/watch?limit=100")
            events = payload.get("events", [])
            for event in events:
                print(
                    "[event]",
                    event.get("eventType"),
                    "job=",
                    event.get("onchainJobId") or event.get("onchainTaskId"),
                    "contract=",
                    event.get("contractAddress"),
                )
        except Exception as exc:  # noqa: BLE001
            print("[aicf-monitor] error:", exc)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
