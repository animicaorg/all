#!/usr/bin/env python3
"""Stage: self-critique validators over per-task eval results."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_EVAL_DIR = _RUN_DIR / "eval"


def _validators(cfg) -> list[dict]:
    return list(cfg.safety["validators"].values()) \
        if isinstance(cfg.safety["validators"], dict) else []


def main() -> int:
    cfg = load_config()
    per_task = _EVAL_DIR / "per_task.jsonl"
    if not per_task.is_file():
        print(f"[critique] missing {per_task}", file=sys.stderr)
        return 1
    eval_data = _RUN_DIR / "datasets" / "eval.jsonl"
    secrets = [p["regex"] for p in cfg.safety["secret_scan"].get("patterns", [])]
    high_risk = cfg.safety["validators"]["command_risk_validator"][
        "high_risk_patterns"]
    high_risk_re = [re.compile(p) for p in high_risk]
    secret_re = [re.compile(p) for p in secrets]

    n = 0
    n_high = 0
    n_medium = 0
    flags: list[dict] = []
    eval_records = {}
    if eval_data.is_file():
        with eval_data.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("id"):
                    eval_records[r["id"]] = r

    with per_task.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            rid = rec.get("id", "")
            evrec = eval_records.get(rid, {})
            msgs = evrec.get("messages") or []
            assistant = msgs[-1].get("content", "") if msgs else ""
            if any(p.search(assistant) for p in high_risk_re):
                n_high += 1
                flags.append({"id": rid, "kind": "command_risk",
                               "severity": "HIGH"})
            if any(p.search(assistant) for p in secret_re):
                n_high += 1
                flags.append({"id": rid, "kind": "secret_leakage",
                               "severity": "HIGH"})
            # Hallucinated commands signal already encoded in
            # per_task.shell_known_only.
            if rec.get("shell_known_only") == 0:
                n_medium += 1
                flags.append({"id": rid, "kind": "hallucinated_command",
                               "severity": "MEDIUM"})

    out = {
        "schema": 1, "run_id": _RUN_ID,
        "n_examples": n,
        "n_high_risk": n_high,
        "n_medium_risk": n_medium,
        "flags": flags[:200],
        "passed": n_high == 0,
    }
    (_EVAL_DIR / "critique.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print(f"[critique] n={n} high={n_high} medium={n_medium} "
          f"passed={out['passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
