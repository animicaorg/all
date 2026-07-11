"""Append-only serving ledger.

One JSON line per AICF inference job served, so a separate treasury-payout
job can reward the miner who served it. Best-effort and fully guarded: it
must never raise into the inference path.
"""
from __future__ import annotations

import json
import os
import time

LEDGER = os.environ.get("ANIMICA_SERVING_LEDGER", "/var/lib/animica-ai/serving.jsonl")


def record(worker: str, job_id: str = "", cost: int = 0) -> None:
    """Append a served-job record: {w: worker, j: job_id, c: cost, t: unixts}."""
    try:
        worker = (worker or "").strip()
        if not worker:
            return
        d = os.path.dirname(LEDGER)
        if d:
            os.makedirs(d, exist_ok=True)
        line = json.dumps(
            {"w": worker, "j": str(job_id or ""), "c": int(cost or 0), "t": int(time.time())},
            separators=(",", ":"),
        )
        with open(LEDGER, "a") as f:
            f.write(line + "\n")
    except Exception:
        # Serving accounting must never break serving.
        pass
