"""Simple event indexer for quantum jobs/results.

This is a small, file-driven indexer usable in dev: it reads newline-delimited JSON events
and calls the in-memory index helpers in `rpc.methods.quantum`.

In production, swap this with an event-listener that subscribes to the chain event stream
and forwards JobSubmitted/ResultSubmitted events.
"""
from __future__ import annotations

import json
import argparse
from typing import Any

import rpc.methods.quantum as qmod


def index_event(event: dict[str, Any]) -> None:
    """Index a single event dict.

    Expected event shapes:
      {"type": "JobSubmitted", "job": {...}}
      {"type": "ResultSubmitted", "job_id": "...", "result": {...}}
    """
    t = event.get("type")
    if t == "JobSubmitted":
        job = event.get("job")
        if job:
            qmod._index_job(job)
            print(f"Indexed job {job.get('job_id')}")
    elif t == "ResultSubmitted":
        job_id = event.get("job_id")
        result = event.get("result")
        if job_id and result:
            qmod._index_result(job_id, result)
            print(f"Indexed result for job {job_id} by worker {result.get('worker_id')}" )
    else:
        print(f"Unknown event type: {t}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--events-file", required=True, help="Newline-delimited JSON events file")
    args = p.parse_args()

    with open(args.events_file, "r", encoding="utf8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            index_event(ev)


if __name__ == "__main__":
    main()
