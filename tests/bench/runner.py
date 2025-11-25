# -*- coding: utf-8 -*-
"""
Benchmark Runner
================

Runs project bench scripts, writes JSON baselines, and compares against a
previous baseline with regression thresholds.

Design goals:
- **Autodiscovery** of Python bench scripts (glob patterns).
- **Portable**: pure Python (optional CPU pin on Linux if requested).
- **Loose JSON parsing**: extracts the last JSON object from stdout.
- **Heuristics**: prefers `ops_per_s` (higher is better) else `median_s`/`mean_s` (lower is better).
- **Actionable summary**: prints deltas and exits non-zero on regressions.

Recommended bench script contract
---------------------------------
Have each script print a short JSON summary to stdout, e.g.:

{
  "case": "da.nmt_build.N=65536",
  "result": {
    "ops_per_s": 250000.0,
    "median_s": 0.0029,
    "p90_s": 0.0035
  },
  "params": {"N": 65536},
  "git": "v0.1.0-3-gdeadbeef",
  "cpu_governor": "performance"
}

Only one of `ops_per_s` or `median_s`/`mean_s` is needed to compare.

Usage
-----
python tests/bench/runner.py \
  --save out/current.json \
  --compare baselines/previous.json \
  --threshold-pct 15 \
  --patterns "da/bench/*.py" "mining/bench/*.py" "randomness/bench/*.py"

Optionally pin to a CPU core on Linux:
  --cpu 2
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from glob import glob
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict


# ----------------------------- helpers ---------------------------------------


def _git_describe() -> str:
    try:
        out = subprocess.check_output(["git", "describe", "--always", "--dirty"], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return ""


def _read_cpu_governor() -> str:
    try:
        p = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        return p.read_text().strip()
    except Exception:
        return ""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def _taskset_supported() -> bool:
    return (not _is_windows()) and shutil.which("taskset") is not None


def _discover_patterns(patterns: Iterable[str]) -> List[str]:
    files: List[str] = []
    for pat in patterns:
        for f in glob(pat, recursive=True):
            if f.endswith(".py") and not f.endswith("__init__.py"):
                files.append(f)
    # also include default fallbacks if none discovered
    if not files:
        defaults = [
            "da/bench/*.py",
            "mining/bench/*.py",
            "randomness/bench/*.py",
            "tests/bench/*.py",
        ]
        for pat in defaults:
            files.extend([f for f in glob(pat, recursive=True) if f.endswith(".py") and not f.endswith("__init__.py")])
    # De-dup and stable order
    return sorted({str(Path(f)) for f in files})


def _extract_json_candidates(s: str) -> List[str]:
    """
    Try to extract JSON object(s) from arbitrary stdout. We favor the *last* JSON object.
    Strategy:
      - If full stdout parses: done.
      - Else find substrings that look like a JSON object via brace matching.
    """
    candidates: List[str] = []
    # First try whole
    try:
        json.loads(s)
        candidates.append(s)
        return candidates
    except Exception:
        pass

    # Find last '{' and try from there backward with a few attempts
    brace_positions = [m.start() for m in re.finditer(r"\{", s)]
    if not brace_positions:
        return candidates
    for start in reversed(brace_positions[-20:]):  # limit search
        chunk = s[start:]
        # Trim trailing noise after last closing brace
        last_close = chunk.rfind("}")
        if last_close != -1:
            chunk = chunk[: last_close + 1]
        try:
            json.loads(chunk)
            candidates.append(chunk)
            break
        except Exception:
            continue
    return candidates


class CaseSummary(TypedDict, total=False):
    case_id: str
    script: str
    params: Dict[str, Any]
    result: Dict[str, Any]   # original result dict (raw)
    metric: str              # "ops_per_s" | "median_s" | "mean_s" | "unknown"
    value: float             # numeric value used for compare
    higher_is_better: bool


def _pick_metric(result: Dict[str, Any]) -> Tuple[str, Optional[float], bool]:
    """
    Return (metric_name, value, higher_is_better).
    Priority: ops_per_s (higher better), then median_s, then mean_s (lower better).
    Supports nested {"result": {...}} too.
    """
    # Dive if top-level has a 'result' subobject
    obj = result.get("result") if isinstance(result.get("result"), dict) else result
    if not isinstance(obj, dict):
        return ("unknown", None, False)

    if "ops_per_s" in obj and isinstance(obj["ops_per_s"], (int, float)):
        return ("ops_per_s", float(obj["ops_per_s"]), True)
    if "median_s" in obj and isinstance(obj["median_s"], (int, float)):
        return ("median_s", float(obj["median_s"]), False)
    if "mean_s" in obj and isinstance(obj["mean_s"], (int, float)):
        return ("mean_s", float(obj["mean_s"]), False)
    # Try common aliases
    for k in ("throughput", "tps", "ops", "opsps"):
        if k in obj and isinstance(obj[k], (int, float)):
            return (k, float(obj[k]), True)
    for k in ("latency_s", "time_s", "duration_s"):
        if k in obj and isinstance(obj[k], (int, float)):
            return (k, float(obj[k]), False)
    return ("unknown", None, False)


def _normalize_case_id(script: str, result: Dict[str, Any]) -> str:
    base = result.get("case") or result.get("name") or Path(script).stem
    param = result.get("params")
    if isinstance(param, dict) and param:
        kv = ",".join(f"{k}={param[k]}" for k in sorted(param))
        return f"{base}({kv})"
    return str(base)


def _run_script(script: str, cpu: Optional[int], env_extra: Dict[str, str], timeout: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Run a Python bench script and parse last JSON object from stdout.
    Returns (parsed_json_or_none, raw_stdout).
    """
    env = os.environ.copy()
    env.update(env_extra)

    if _taskset_supported() and cpu is not None:
        cmd = ["taskset", "-c", str(cpu), sys.executable, script]
    else:
        cmd = [sys.executable, script]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        return (None, f"[TIMEOUT] {script} after {timeout}s")
    except Exception as e:
        return (None, f"[ERROR] Failed to launch {script}: {e}")

    out = proc.stdout or ""
    # Attempt JSON parse
    parsed: Optional[Dict[str, Any]] = None
    for candidate in _extract_json_candidates(out) or []:
        try:
            parsed = json.loads(candidate)
            break
        except Exception:
            continue

    return (parsed, out)


def _summarize_case(script: str, parsed: Dict[str, Any]) -> CaseSummary:
    metric_name, value, hib = _pick_metric(parsed)
    case_id = _normalize_case_id(script, parsed)
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    return CaseSummary(
        case_id=case_id,
        script=str(script),
        params=params,
        result=parsed,        # raw result object
        metric=metric_name,
        value=(float(value) if value is not None else float("nan")),
        higher_is_better=hib,
    )


def _load_baseline(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_baseline(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.json")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)


def _compare_cases(
    prev: Dict[str, Any], cur: Dict[str, Any], threshold_pct: float
) -> Tuple[List[str], List[str], List[str], int]:
    """
    Compare two baselines. Returns (improved_msgs, regressed_msgs, unchanged_msgs, num_regressions_over_threshold).
    """
    improved: List[str] = []
    regressed: List[str] = []
    unchanged: List[str] = []
    over = 0

    prev_cases = prev.get("cases", {})
    cur_cases = cur.get("cases", {})

    for cid, curc in cur_cases.items():
        if cid not in prev_cases:
            unchanged.append(f"[NEW] {cid} = {curc.get('value')} ({curc.get('metric')})")
            continue
        pvc = prev_cases[cid]
        metric = curc.get("metric") or pvc.get("metric") or "unknown"
        v_cur = curc.get("value")
        v_prev = pvc.get("value")
        hib = bool(curc.get("higher_is_better") if curc.get("higher_is_better") is not None else pvc.get("higher_is_better", False))

        if not isinstance(v_cur, (int, float)) or not isinstance(v_prev, (int, float)):
            unchanged.append(f"[SKIP] {cid} (unknown numeric metric)")
            continue

        if (v_cur != v_cur) or (v_prev != v_prev):  # NaN check
            unchanged.append(f"[SKIP] {cid} (NaN)")
            continue

        if hib:
            # higher is better => regression if v_cur < v_prev
            delta = (v_cur - v_prev) / v_prev * 100.0 if v_prev else float("inf")
            if delta < -1e-12:
                regressed.append(f"[REGRESS] {cid}: {v_prev:.6g} → {v_cur:.6g} {metric} ({delta:.2f}%)")
                if abs(delta) > threshold_pct:
                    over += 1
            elif delta > 1e-12:
                improved.append(f"[IMPROVE] {cid}: {v_prev:.6g} → {v_cur:.6g} {metric} (+{delta:.2f}%)")
            else:
                unchanged.append(f"[=] {cid}: {v_cur:.6g} {metric}")
        else:
            # lower is better => regression if v_cur > v_prev
            delta = (v_prev - v_cur) / v_prev * 100.0 if v_prev else 0.0
            if delta < -1e-12:
                regressed.append(f"[REGRESS] {cid}: {v_prev:.6g} → {v_cur:.6g} {metric} (-{delta:.2f}%)")
                if abs(delta) > threshold_pct:
                    over += 1
            elif delta > 1e-12:
                improved.append(f"[IMPROVE] {cid}: {v_prev:.6g} → {v_cur:.6g} {metric} ({delta:.2f}% better)")
            else:
                unchanged.append(f"[=] {cid}: {v_cur:.6g} {metric}")

    # Anything missing now
    for cid in prev_cases:
        if cid not in cur_cases:
            regressed.append(f"[MISSING] {cid} (present in baseline, missing now)")
            over += 1

    return improved, regressed, unchanged, over


# ----------------------------- CLI -------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Run project benches, save baseline JSON, and compare with thresholds.")
    p.add_argument("--patterns", nargs="*", default=["da/bench/*.py", "mining/bench/*.py", "randomness/bench/*.py"],
                   help="Glob patterns for bench scripts (default: common module benches).")
    p.add_argument("--cpu", type=int, default=None, help="Pin to CPU core (Linux taskset).")
    p.add_argument("--timeout", type=int, default=300, help="Per-benchmark timeout in seconds (default: 300).")
    p.add_argument("--save", type=Path, default=Path("benchmarks/current.json"), help="Where to write current baseline JSON.")
    p.add_argument("--compare", type=Path, default=None, help="Path to a previous baseline JSON to compare against.")
    p.add_argument("--threshold-pct", type=float, default=15.0, help="Regression threshold percentage (default: 15).")
    p.add_argument("--print-stdout", action="store_true", help="Print raw stdout from bench scripts.")
    p.add_argument("--md", type=Path, default=None, help="Also write a Markdown summary table here.")
    p.add_argument("--env", action="append", default=[], help="Extra env (KEY=VALUE). Can repeat.")
    args = p.parse_args(argv)

    # Resolve env extras
    env_extra: Dict[str, str] = {"PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0")}
    for kv in args.env:
        if "=" in kv:
            k, v = kv.split("=", 1)
            env_extra[k] = v

    scripts = _discover_patterns(args.patterns)
    if not scripts:
        print("[bench-runner] No bench scripts discovered. Patterns:", args.patterns)
        return 1

    print(f"[bench-runner] Discovered {len(scripts)} bench scripts:")
    for s in scripts:
        print("  -", s)

    meta = {
        "ts": _now_iso(),
        "git": _git_describe(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
        "cpu_governor": _read_cpu_governor(),
        "cpu_pin": args.cpu,
        "taskset": _taskset_supported(),
    }

    cases: Dict[str, Any] = {}
    failures = 0

    for script in scripts:
        print(f"\n[bench] Running: {script}")
        parsed, stdout = _run_script(script, cpu=args.cpu, env_extra=env_extra, timeout=args.timeout)
        if args.print-stdout:
            print("----- stdout begin -----")
            print(stdout.rstrip())
            print("----- stdout end -----")
        if parsed is None:
            print(f"[bench] WARN: No JSON extracted from {script}. Skipping.")
            failures += 1
            continue
        case = _summarize_case(script, parsed)
        cid = case["case_id"]
        metric = case["metric"]
        value = case.get("value")
        hib = case["higher_is_better"]
        print(f"[bench] Case: {cid} | metric={metric} value={value} higher_is_better={hib}")
        cases[cid] = case

    current = {"meta": meta, "cases": cases}
    _save_baseline(args.save, current)
    print(f"\n[bench-runner] Wrote current baseline: {args.save}")

    exit_code = 0
    if args.compare and args.compare.exists():
        print(f"\n[bench-runner] Comparing to baseline: {args.compare}")
        prev = _load_baseline(args.compare)
        improved, regressed, unchanged, over = _compare_cases(prev, current, args.threshold_pct)

        print("\n== Improvements ==")
        for m in improved or ["(none)"]:
            print(m)
        print("\n== Regressions ==")
        for m in regressed or ["(none)"]:
            print(m)
        print("\n== Unchanged/New ==")
        for m in unchanged or ["(none)"]:
            print(m)

        if over > 0:
            print(f"\n[bench-runner] FAIL: {over} regression(s) exceeded threshold {args.threshold_pct}%")
            exit_code = 2
        else:
            print(f"\n[bench-runner] PASS: No regressions over {args.threshold_pct}%")

        if args.md:
            _write_markdown(args.md, prev, current, improved, regressed, unchanged, args.threshold_pct)

    elif args.compare:
        print(f"[bench-runner] Baseline to compare not found: {args.compare} (skipping comparison)")
    else:
        print("[bench-runner] No --compare provided; skipping comparison.")

    # Consider discovery or parse failures as soft errors unless explicit compare fails
    if failures and exit_code == 0:
        print(f"[bench-runner] NOTE: {failures} script(s) produced no JSON; skipped.")
    return exit_code


def _write_markdown(
    path: Path,
    prev: Dict[str, Any],
    cur: Dict[str, Any],
    improved: List[str],
    regressed: List[str],
    unchanged: List[str],
    threshold: float,
) -> None:
    """Write a concise Markdown table with per-case deltas."""
    prev_cases = prev.get("cases", {})
    cur_cases = cur.get("cases", {})

    lines: List[str] = []
    lines.append(f"# Benchmark Comparison (threshold {threshold}%)\n")
    lines.append(f"- Prev git: `{prev.get('meta', {}).get('git', '')}`  \n- Cur git: `{cur.get('meta', {}).get('git', '')}`\n")
    lines.append("| Case | Metric | Prev | Cur | Δ% | Status |")
    lines.append("|------|--------|------|-----|----|--------|")

    # Union of cases
    all_ids = sorted(set(prev_cases) | set(cur_cases))
    for cid in all_ids:
        pvc = prev_cases.get(cid)
        cuc = cur_cases.get(cid)
        if pvc and cuc:
            metric = cuc.get("metric") or pvc.get("metric") or "?"
            v_prev = pvc.get("value")
            v_cur = cuc.get("value")
            hib = bool(cuc.get("higher_is_better") if cuc.get("higher_is_better") is not None else pvc.get("higher_is_better", False))
            if isinstance(v_prev, (int, float)) and isinstance(v_cur, (int, float)):
                if hib:
                    d = (v_cur - v_prev) / v_prev * 100.0 if v_prev else float("inf")
                else:
                    d = (v_prev - v_cur) / v_prev * 100.0 if v_prev else 0.0
                status = "improve" if ((hib and d > 0) or ((not hib) and d > 0)) else ("regress" if d < 0 else "same")
                lines.append(f"| {cid} | {metric} | {v_prev:.6g} | {v_cur:.6g} | {d:+.2f}% | {status} |")
            else:
                lines.append(f"| {cid} | {metric} | {v_prev} | {v_cur} | n/a | n/a |")
        elif pvc and not cuc:
            lines.append(f"| {cid} | {pvc.get('metric','?')} | {pvc.get('value')} | — | — | missing |")
        elif cuc and not pvc:
            lines.append(f"| {cid} | {cuc.get('metric','?')} | — | {cuc.get('value')} | — | new |")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[bench-runner] Wrote Markdown summary: {path}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
