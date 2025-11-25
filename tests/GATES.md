# CI Gates & Performance Guardrails

This document defines what “**CI green**” means for this repository, how we enforce **determinism**, and the **performance guardrails** that keep critical hot paths from regressing.

---

## What counts as “CI green”

A PR or main-branch commit is **CI green** when all of the following pass:

1. **Fast Suite (required on every PR)**
   - Runs: unit tests, property tests, and a light subset of integration tests (no Docker).
   - Driver: `python tests/ci/run_fast_suite.py`
   - Produces: `tests/reports/junit-fast.xml`, (optional) coverage.
2. **Full Suite (required on main & scheduled; optional on PR unless label `full-suite`)**
   - Spins up the devnet (Docker), runs integration tests, E2E, and fuzz *smoke*.
   - Driver: `python tests/ci/run_full_suite.py`
   - Produces: `tests/reports/junit-full.xml`, coverage (XML+HTML), E2E artifacts, fuzz logs.
3. **Bench Guardrails (required on main & scheduled; PRs get a short check)**
   - Driver: `python tests/bench/runner.py` (invoked by CI job)
   - Compares measured metrics to `tests/bench/baselines.json`.
4. **Artifacts collected**
   - Driver: `python tests/ci/collect_artifacts.py --out-dir outputs`
   - Gathers junit/coverage/bench (+ Playwright traces if present) into `outputs/`.

> CI is configured to fail the job on **any** failing test. There is **no retry** policy for unit/property/integration. E2E steps may retry once at the job layer where supported.

---

## Determinism & environment expectations

We aim for stable, reproducible results in CI:

- `PYTHONHASHSEED=0`
- `NUMBA_DISABLE_JIT=1` (ensures stable CPU paths if numba is installed)
- Hypothesis profile: `HYPOTHESIS_PROFILE=ci` (short deadlines, limited examples, no flakiness)
- If `pytest-xdist` is present, we run with `-n auto`. Tests must be concurrency-safe.
- Time and randomness: tests that depend on time must use provided fakes/frozen clocks or fixtures.

### Coverage
- Coverage is recorded when `pytest-cov` is available.
- **Target (informational on PRs, required on main):** line coverage ≥ **55% overall** across core packages.
- If the plugin is missing, the gate is skipped (CI images should include it).

---

## Suites in detail

### Fast Suite
- Command:  
  ```bash
  python tests/ci/run_fast_suite.py

	•	Scope:
	•	tests/unit, tests/property
	•	Selected integration tests that do not require Docker
	•	Excludes: tests/integration/* that depend on devnet, all of tests/e2e, tests/bench, tests/load, and fuzzers.

Full Suite
	•	Command:

python tests/ci/run_full_suite.py


	•	Phases:
	1.	Devnet up via Docker Compose (unless --no-docker)
	2.	Readiness wait (tests/devnet/wait_for_services.sh)
	3.	Integration + Property (pytest)
	4.	E2E:
	•	tests/e2e/run_wallet_extension_e2e.py
	•	tests/e2e/run_studio_web_deploy_template.py
	•	tests/e2e/run_explorer_live_dashboard.py
	5.	Fuzz smoke (if atheris installed): ~10s per target by default
Targets in tests/fuzz/… with dictionaries and corpora.
	•	Tearing down: the runner will docker compose down -v on exit.

⸻

Performance guardrails

Performance is enforced by comparing fresh measurements to baselines with allowed tolerance.
	•	Baselines file: tests/bench/baselines.json
	•	Runner: python tests/bench/runner.py
	•	Output: JSON & MD report under tests/reports/bench/ (then collected to outputs/bench/)

Comparison semantics

For each metric:
	•	Fail if current < baseline × (1 - degradation_pct_max)
	•	Warn if current < baseline × (1 - variance_pct_warn) (job stays green, but logs a warning)

Defaults (unless overridden per metric in baselines.json):
	•	degradation_pct_max: 0.15 (15%)
	•	variance_pct_warn: 0.05 (5%)

Canonical metrics

Key (in baselines.json)	Meaning	Typical Unit
consensus_score.ops_per_sec	PoIES scoring throughput	ops/sec
retarget_update.ops_per_sec	Θ retarget math	ops/sec
da_rs_encode.encode_MBps	Reed–Solomon encode throughput	MB/sec
da_rs_encode.decode_MBps	Reed–Solomon decode throughput	MB/sec
da_nmt_build.leaves_per_sec	NMT build rate	leaves/sec
vm_counter_runtime.steps_per_sec	VM interpreter micro-steps	steps/sec
mempool_select.ops_per_sec	Selection under budgets	ops/sec
miner_hash_loop.hashes_per_sec	Inner hash loop @ dev Θ	hashes/sec
p2p_encode_decode.frames_per_sec	Wire encode/decode	frames/sec
randomness_vdf_verify.proofs_per_sec	Wesolowski verify	proofs/sec
aicf_matcher.jobs_per_sec	Provider assignment throughput	jobs/sec

Note: Hardware variance is unavoidable. Use variance_pct_warn to accommodate minor drift. Substantial regressions must either be fixed or baselines updated in a dedicated PR after investigation.

⸻

Fuzzing expectations
	•	Smoke pass (CI): ~10s per target (configurable in run_full_suite.py via --fuzz-seconds).
	•	Determinism: Keep seeds in corpus directories and use provided dictionaries:
	•	tests/fuzz/dictionaries/cbor.dict
	•	tests/fuzz/dictionaries/json.dict
	•	Long-running fuzz is out of scope for the gate; it runs on scheduled jobs.

⸻

E2E expectations
	•	Headless Playwright, with traces/videos saved on failure when configured.
	•	The following must pass in Full Suite:
	•	Wallet extension connect & send
	•	Studio-web template deploy/verify
	•	Explorer live dashboard reflects Γ/fairness/mix updates

Artifacts (if present) are collected from playwright-report/ or test-results/.

⸻

Artifacts & where to find them

After running collection:

outputs/
  index.txt
  xml/                # junit & coverage XML
  coverage/
    html/…            # HTML reports
    xml/…             # coverage XML
  bench/
    results/*.json
    results/*.md
    baselines.json
    report.md
  playwright-report/  # if produced
  test-results/       # if produced


⸻

Local reproduction

# Fast (no Docker)
python tests/ci/run_fast_suite.py

# Full (with Docker devnet)
python tests/ci/run_full_suite.py

# Collect artifacts to outputs/
python tests/ci/collect_artifacts.py --out-dir outputs

If devnet is already running:

python tests/ci/run_full_suite.py --no-docker


⸻

Baseline updates (process)
	1.	Investigate the regression. If legitimate (e.g., algorithmic change),
	2.	Update tests/bench/baselines.json in a separate PR titled “bench: refresh baselines”.
	3.	Include a short rationale in the PR description and in tests/bench/report.md.

⸻

Flake policy
	•	Zero tolerance for unit, property, and integration tests.
	•	E2E is allowed one retry at the job layer where configured (not via pytest reruns).
	•	Any repeated flake must be tracked and fixed; temporary skips require an issue reference.

⸻

Notes
	•	Coverage, fuzz, and benches are best-effort on contributor machines; the CI images include required tooling.
	•	On PRs, maintainers can apply a full-suite label to run the full job before merge.

