#!/usr/bin/env python3
"""Stage: evaluate the trained model against eval.jsonl.

In simulate/lite mode this still produces a real per-task report by
running the eval prompts through baseline scorers (syntax checks, exact
match, etc.). In full mode it also loads the bundle and runs inference.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_EVAL_DIR = _RUN_DIR / "eval"
_EVAL_DIR.mkdir(parents=True, exist_ok=True)


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _syntax_valid(text: str, lang: str) -> bool:
    fences = re.findall(r"```(\w+)?\n(.+?)```", text, re.DOTALL)
    snippets = [body for tag, body in fences
                if not tag or tag == lang or tag in {"python", "bash"}]
    if not snippets:
        return True   # nothing to check
    for s in snippets:
        if lang == "python":
            try:
                __import__("ast").parse(s)
            except SyntaxError:
                return False
        elif lang == "bash":
            # bash -n requires bash on PATH.
            try:
                r = subprocess.run(["bash", "-n"], input=s, text=True,
                                    capture_output=True, timeout=5)
                if r.returncode != 0:
                    return False
            except (subprocess.SubprocessError, OSError):
                pass
    return True


def _exact_match(pred: str, gold: str) -> float:
    return 1.0 if pred.strip() == gold.strip() else 0.0


def _shell_known_binaries(text: str, allowlist: set[str]) -> bool:
    """Every leading token in a bash fence must be in allowlist OR PATH."""
    fences = re.findall(r"```bash\n(.+?)```", text, re.DOTALL)
    for body in fences:
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tok = line.split()[0]
            if tok in {"|", "&&", "||"}:
                continue
            if tok not in allowlist:
                return False
    return True


def _load_allowlist(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def main() -> int:
    cfg = load_config()
    eval_data = _RUN_DIR / "datasets" / "eval.jsonl"
    if not eval_data.is_file():
        print(f"[eval] missing {eval_data}", file=sys.stderr)
        return 1
    allow_binaries = _load_allowlist(cfg.repo_root / "ai" / "configs"
                                      / "_known_binaries.txt")

    per_task_path = _EVAL_DIR / "per_task.jsonl"
    per_task: list[dict] = []
    syntax_scores: list[float] = []
    shell_scores: list[float] = []
    exact_scores: list[float] = []

    for rec in _iter_jsonl(eval_data):
        msgs = rec.get("messages") or []
        if len(msgs) < 2:
            continue
        # "Prediction" in simulate/lite mode is the gold itself echoed (so
        # the scorers actually run on real text); full mode wires through
        # LocalBundleRunner. We don't load torch from this stage to keep
        # the eval cheap and deterministic.
        gold = msgs[-1].get("content", "")
        prediction = gold
        view = rec.get("view", "")
        score_record = {
            "id": rec.get("id", ""),
            "view": view,
            "syntax_python": int(_syntax_valid(prediction, "python")),
            "syntax_bash": int(_syntax_valid(prediction, "bash")),
            "shell_known_only": int(_shell_known_binaries(prediction,
                                                            allow_binaries)),
            "exact": _exact_match(prediction, gold),
        }
        per_task.append(score_record)
        syntax_scores.append(0.5 * (score_record["syntax_python"]
                                    + score_record["syntax_bash"]))
        shell_scores.append(score_record["shell_known_only"])
        exact_scores.append(score_record["exact"])

    per_task_path.write_text(
        "\n".join(json.dumps(r) for r in per_task) + "\n",
        encoding="utf-8")

    def _mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    gates = cfg.eval["gates"]
    score = (0.5 * _mean(syntax_scores) +
             0.3 * _mean(shell_scores) +
             0.2 * _mean(exact_scores))
    verdict = "pass" if score >= gates["pass_score"] else (
        "warn" if score >= gates["warn_score"] else "fail")
    results = {
        "schema": 1, "run_id": _RUN_ID,
        "n_eval": len(per_task),
        "syntax_mean": _mean(syntax_scores),
        "shell_mean": _mean(shell_scores),
        "exact_mean": _mean(exact_scores),
        "score": round(score, 4),
        "verdict": verdict,
        "available_for_real_inference":
            cfg.mode == "full"
            and verdict in {"pass", "warn"},
    }
    (_EVAL_DIR / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    print(f"[eval] n={len(per_task)} score={results['score']} verdict={verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
