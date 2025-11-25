# Methods for Refreshing Performance Baselines

This document explains **when** and **how** to refresh `tests/bench/baselines.json` in a way that is reproducible, reviewed, and aligned with the CI **GATES**. See also: `tests/GATES.md` (Performance Guardrails).

> TL;DR: Only refresh baselines after a justified change, on a controlled machine, with multiple runs and clear notes in a dedicated PR.

---

## 1) When it’s legitimate to refresh

Refresh baselines **only** if at least one of the following is true:

- **Algorithmic change** improves or (intentionally) trades off throughput/latency.
- **New features** legitimately shift hot-path costs (e.g., added verification steps).
- **Compiler/dep upgrades** (Python, wheels, msgspec/cbor, numpy, etc.) cause stable, explainable shifts.
- **Hardware/CI image change** establishes a new long-term reference platform.

Do **not** refresh if:
- A one-off regression appears without root-cause.
- Measurements are noisy/flaky due to resource contention.
- You just want the CI to go green.

---

## 2) Measurement environment (make it stable)

Recommended settings for local and CI-equivalent runs:

- **CPU pinning / governor**
  - Linux: `taskset -c 0-3` (pick isolated cores); `sudo cpupower frequency-set -g performance`
- **Thermals / scaling**
  - Ensure machine is cooled and not power-throttled; close heavy apps.
- **Process noise**
  - Disable background sync/indexers; keep network quiet for P2P/WS benches.
- **Env vars**
  - `PYTHONHASHSEED=0`
  - `NUMBA_DISABLE_JIT=1` (we benchmark pure-Python/reference paths deterministically)
- **Dependencies**
  - Use the same Python & pinned requirements as CI (see repo lockfiles).
- **Repeatability**
  - Run each benchmark **N≥5** times and use median (report spread in PR).

---

## 3) How to collect fresh measurements

From repo root:

```bash
# Optionally pin cores to reduce noise
taskset -c 0-3 python tests/bench/runner.py --out-dir tests/reports/bench

Outputs (if the runner finds the benches on your platform):
	•	tests/reports/bench/*.json — per-benchmark result files
	•	tests/reports/bench/report.md — human summary with environment notes

If some benches are not available on your platform, the runner will skip them. Do not refresh baselines for skipped metrics.

Run 5× and capture medians (example):

for i in 1 2 3 4 5; do
  echo "Run $i"
  taskset -c 0-3 python tests/bench/runner.py --out-dir tests/reports/bench --append
done
# Then create a median set:
python tests/bench/runner.py --out-dir tests/reports/bench --reduce median

(If --append/--reduce are unavailable on your local version, run the loop manually and pick medians by inspection. The PR should state how medians were chosen.)

⸻

4) Decide if a refresh is warranted

Compare against current baselines:

python tests/bench/runner.py --compare --out-dir tests/reports/bench

	•	If most metrics are within warn (≤5%) or improved, no refresh is needed.
	•	If metrics breach degradation (>15% by default) but you have a justified reason, you may refresh.

Any regression must be explained in the PR (what changed and why it is acceptable).

⸻

5) Refresh process (dedicated PR)
	1.	Branch & commit scope
	•	Create a PR titled: bench: refresh baselines (reason: <short>).
	•	The PR should only change:
	•	tests/bench/baselines.json
	•	tests/bench/report.md (summary of your runs)
	•	(Optionally) a README note if methodology improved.
	2.	Edit baselines.json
	•	Update only the keys you measured.
	•	Keep structure and any per-metric thresholds (e.g., degradation_pct_max, variance_pct_warn).
Minimal example (structure; values illustrative):

{
  "vm_counter_runtime.steps_per_sec": {
    "value": 1320000,
    "unit": "steps/sec",
    "degradation_pct_max": 0.15,
    "variance_pct_warn": 0.05,
    "notes": "Median of 5 runs on CI image v2025-09; Python 3.12.3"
  },
  "da_rs_encode.encode_MBps": {
    "value": 520.4,
    "unit": "MB/sec"
  }
}


	3.	Document methodology in tests/bench/report.md
	•	Machine model / CPU / cores used
	•	Python & dependency versions
	•	Exact commands (with taskset if used)
	•	Number of runs and the aggregation method (median/trimmed mean)
	•	Any known caveats or upcoming work
	4.	Link evidence
	•	Paste snippets from tests/reports/bench/*.json into the PR description, or attach artifacts.
	•	If CI image changed, link to the infra change.
	5.	Reviewer checklist
	•	Baselines only updated where justified.
	•	Units and thresholds preserved.
	•	Report includes enough detail to reproduce.
	•	No silent, blanket increases to hide regressions.

⸻

6) Post-merge validation

After merge to main, CI will:
	•	Re-run benches and compare against the new baselines.
	•	Fail if numbers still fall outside thresholds (indicates unstable environment). Investigate promptly.

⸻

7) Rollback policy

If unexpected regressions appear after refresh:
	•	Revert the baseline commit or follow-up with a fix and a new, justified refresh.
	•	Avoid multiple rapid baseline edits; aim for stability between releases.

⸻

8) Common pitfalls
	•	Thermal throttling → wildly varying MB/s or ops/sec.
	•	Hyperthreading interference → pin to physical cores if possible.
	•	Background processes (browser builds, video calls) → close them.
	•	Mixing Python/dep versions → ensure parity with CI.
	•	Single-run decisions → always aggregate across repeated runs.

⸻

9) CI integration notes
	•	tests/GATES.md defines pass/fail rules and artifact collection.
	•	The bench job publishes tests/reports/bench/ into CI artifacts; use these for PR evidence.
	•	If a new metric is added by a bench script, add it to baselines.json in the same PR that introduces the metric.

⸻

10) Quick checklist (copy into PR)
	•	Reason for refresh (algo change / deps / hardware / CI image).
	•	Environment details (CPU, Python, deps).
	•	Commands used (with pinning).
	•	Repetitions (≥5) and aggregation method.
	•	Updated baselines.json minimal scope.
	•	Updated tests/bench/report.md with summary.
	•	Evidence attached (JSON snippets / CI artifacts).
	•	Reviewer sign-off.

