#!/usr/bin/env python3
"""Stage: compare this run's scores against baselines."""

from __future__ import annotations

import json
import os
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


def _load_results(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    cfg = load_config()
    this_run = _load_results(_EVAL_DIR / "results.json")
    if not this_run:
        print("[benchmark] no results.json from evaluate_model.py",
              file=sys.stderr)
        return 1

    # Baseline: previous exported bundle, if present.
    prev_link = _PKG_DIR / "models" / "export" / "latest"
    prev_results: dict = {}
    if prev_link.is_dir() or prev_link.is_symlink():
        prev_results = _load_results(prev_link / "eval_summary.json")

    # Baseline: simulated baseline (always 0; provides a floor).
    sim_score = 0.0

    rows = [
        {"name": "this_run", "score": this_run.get("score", 0.0)},
        {"name": "simulated_baseline", "score": sim_score},
    ]
    if prev_results:
        rows.append({"name": "previous_bundle",
                      "score": prev_results.get("score", 0.0)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    out = {
        "schema": 1, "run_id": _RUN_ID,
        "rows": rows,
        "winner": rows[0]["name"],
        "delta_over_previous":
            round(this_run.get("score", 0.0) -
                  prev_results.get("score", 0.0), 4),
    }
    (_EVAL_DIR / "benchmark.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print(f"[benchmark] winner={out['winner']} "
          f"this_run_score={this_run.get('score'):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
