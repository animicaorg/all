# Fuzz & Property Testing Guide

This project leans on **property-based tests** (Hypothesis via `pytest`) to "fuzz"
critical codecs, execution, DA sampling math, consensus acceptance edges, etc.
You can run them as quick sanity checks or turn the dials up for heavyweight fuzzing.

> The actual fuzz targets live under `tests/property/` and several
> `tests/integration/` suites also rely on randomized inputs.  
> This document shows how to control **time**, **seed**, and **intensity**.

---

## Prerequisites

```bash
python -m pip install -U pytest hypothesis pytest-timeout
# (optional) nicer output while shrinking:
python -m pip install -U hypothesis[cli]

We already vendor reasonable timeouts in tests using pytest-timeout or marks.

⸻

Quick start (default fuzz intensity)

Run all property suites:

pytest -q tests/property

Target a specific area:

# Tx / block codec properties
pytest -q tests/property -k 'tx_codec or block_codec'

# VM storage invariants
pytest -q tests/property -k vm_storage

# DA sampling probability bounds
pytest -q tests/property -k da_sampling

# Consensus acceptance around Θ
pytest -q tests/property -k consensus_accept


⸻

Reproducibility: Seeds

Hypothesis integrates with pytest and exposes seed controls:
	•	One-off seed (repro a prior failure):

pytest -q tests/property --hypothesis-seed=123456789


	•	Show the seed Hypothesis used on failures (printed automatically).
Add -vv for extra detail.
	•	Persist examples: Hypothesis stores interesting cases under .hypothesis/.
Keep this folder if you want to preserve and replay discovered counterexamples.

⸻

Time & Intensity Controls

You can trade runtime for coverage with these knobs:
	•	Max examples per test (more cases ⇒ longer time):

pytest tests/property --hypothesis-max-examples=2000


	•	No per-example deadlines (useful on slow machines or under CI load):

pytest tests/property --hypothesis-deadline=none


	•	Faster shrinking (less time minimizing failures):

pytest tests/property --hypothesis-derandomize


	•	Verbose shrinking / statistics:

pytest tests/property --hypothesis-verbosity=debug --hypothesis-show-statistics


	•	Global wall-time cap (coarse control via pytest-timeout):

# Fail any single test that exceeds 300s
pytest tests/property --timeout=300



Tip: Combine --hypothesis-max-examples with -k to focus time where it matters.

⸻

Suggested Presets

Fast developer loop (~30–60s)

pytest -q tests/property \
  --hypothesis-max-examples=300 \
  --hypothesis-deadline=none

Medium (~3–5 min)

pytest tests/property \
  --hypothesis-max-examples=1500 \
  --hypothesis-deadline=none \
  -q

Heavy fuzz (overnight or CI cron)

pytest tests/property \
  --hypothesis-max-examples=10000 \
  --hypothesis-deadline=none \
  --hypothesis-verbosity=debug \
  --hypothesis-show-statistics

(Consider running suites in parallel with pytest -n auto if pytest-xdist is installed.)

⸻

Interpreting Failures
	•	Hypothesis will shrink inputs to a minimal counterexample and print it with the
seed. You can re-run with --hypothesis-seed=<seed> to reproduce locally.
	•	Keep .hypothesis/ committed or cached in CI artifacts if you want stability across runs.
	•	Many properties also assert idempotence, stability of hashes, or probability bounds.
When a failure references such a property, confirm:
	•	deterministic encoders (no accidental time/nonces),
	•	canonical ordering and normalization,
	•	numeric tolerances and epsilon values used in the test.

⸻

Tips
	•	To focus on a single failing test until fixed:

pytest -q tests/property -k name_of_test -x --maxfail=1


	•	Capture logs to help debug randomized cases:

pytest tests/property -vv -s



⸻

Environment Variables (optional)
	•	HYPOTHESIS_PROFILE: if you define profiles in conftest.py, you can select one:

HYPOTHESIS_PROFILE=ci pytest tests/property


	•	PYTEST_ADDOPTS: set organization-wide defaults, e.g.:

export PYTEST_ADDOPTS="--hypothesis-deadline=none --hypothesis-max-examples=1000 -q"



⸻

Non-Python fuzzers?

If you want to augment with harness-style fuzzing (e.g. AFL/atheris) for specific
parsers (CBOR, JSON schemas, NMT proofs), add dedicated harnesses under
tests/fuzz/ and gate them behind an env var so they don’t run by default.
This repo’s core strategy remains Hypothesis-first for portability and speed.

⸻

Happy fuzzing! If you uncover a minimal counterexample, add it as a test vector
under the appropriate spec/*/test_vectors file to lock the fix in.
