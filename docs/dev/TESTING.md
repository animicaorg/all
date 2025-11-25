# Testing — Unit / Integration / Fuzz / Bench Workflows

This document shows how to run the test suites for the Animica stack: fast unit tests, end-to-end integration, fuzz/property tests, and performance benchmarks. All commands assume you’re in a Python virtualenv and the repo root unless noted.

> Tip: many suites auto-detect optional accelerators (e.g., `animica_zk_native`). Tests will **skip** accel-specific checks if the module isn’t installed.

---

## 1) Quick reference

| Suite | Command (common) | Purpose |
|---|---|---|
| **Unit (Python)** | `pytest -q <pkg>/tests` | Fast logic tests per module |
| **Integration (Python)** | `pytest -q tests -m "integration"` | Cross-module / end-to-end flows |
| **ZK Verifiers** | `pytest -q zk/tests` | Groth16/PLONK/STARK + fixtures |
| **Native (Rust)** | `pushd zk/native && cargo test && popd` | Rust unit tests for pairing/KZG |
| **Fuzz (Python)** | `pytest -q -m "property"` (Hypothesis) | Property tests for encoders/hash/trees |
| **Fuzz (Rust)** | `cargo fuzz run <target>` | AFL-style fuzz on BN254/KZG parsers |
| **Benches (Python)** | `python zk/bench/verify_speed.py --json-out out.json` | Verifier microbenchmarks |
| **Benches (Rust)** | `pushd zk/native && cargo bench && popd` | Native microbench (criterion) |
| **Coverage** | `pytest --cov --cov-report=term-missing` | Statement coverage across pkgs |

---

## 2) Environment & markers

- Prefer reproducible runs:
  ```bash
  export PYTHONHASHSEED=0
  export TZ=UTC

	•	Useful pytest markers (declared in the tree):
	•	@pytest.mark.unit — fast tests (default).
	•	@pytest.mark.integration — spins subsystems (DB, RPC, P2P, etc.).
	•	@pytest.mark.native — requires animica_zk_native.
	•	@pytest.mark.slow — heavier vectors/large loops.
	•	@pytest.mark.property — Hypothesis property tests.
	•	@pytest.mark.bench — micro-bench sanity wrappers.

Select by marker:

pytest -q -m "unit and not slow"
pytest -q -m "integration"
pytest -q -m "native"


⸻

3) Unit tests (Python)

Run per-package for fast iteration:

pytest -q core/tests
pytest -q consensus/tests
pytest -q proofs/tests
pytest -q mempool/tests
pytest -q p2p/tests
pytest -q da/tests
pytest -q execution/tests
pytest -q zk/tests -k "not slow"

With coverage:

pytest \
  --cov=core --cov=consensus --cov=proofs --cov=mempool \
  --cov=p2p --cov=da --cov=execution --cov=zk \
  --cov-report=term-missing -q


⸻

4) Integration tests

End-to-end flows that wire multiple modules and use realistic fixtures.

Examples:

# RPC round-trips (JSON-RPC server + sqlite DB)
pytest -q rpc/tests -k "roundtrip or tx_flow"

# P2P: two nodes sync headers/blocks, gossip
pytest -q p2p/tests -k "end_to_end_two_nodes or header_sync or block_sync"

# DA: post/get/proof with light-client verification
pytest -q da/tests -k "integration_post_get_verify"

To run only “integration” marked tests across the repo:

pytest -q tests -m "integration"


⸻

5) ZK verifier tests

Pure-Python backends with optional native fast-paths.

# Groth16 (snarkjs-compatible fixtures)
pytest -q zk/tests/test_groth16_embedding_verify.py

# PLONK (Poseidon params & KZG)
pytest -q zk/tests/test_plonk_poseidon_verify.py

# Tiny STARK (toy AIR + FRI) + Merkle
pytest -q zk/tests/test_stark_merkle_verify.py

# VK cache integrity
pytest -q zk/tests/test_vk_cache.py

# Envelope adapters stable round-trip
pytest -q zk/tests/test_envelope_roundtrip.py

If you’ve installed the native module:

python -c "import animica_zk_native; print('native-ok')"
pytest -q zk/tests -m "native or unit"


⸻

6) Native (Rust) tests

pushd zk/native
cargo test --all-features
popd

Run only BN254 pairing/KZG units:

cargo test -p animica_zk_native pairing kzg


⸻

7) Property & fuzz testing

7.1 Python (Hypothesis)

Install Hypothesis:

python -m pip install hypothesis

Run property suites:

pytest -q -m "property"
# Example focus: Merkle/NMT encoding stability
pytest -q da/tests -k "property"

Reproduce a failing seed:

pytest -q <path> --hypothesis-seed=123456789

7.2 Python (Atheris, optional)

python -m pip install atheris
# Run corpus fuzzers under tools/fuzzers/* if present

7.3 Rust (cargo-fuzz)

pushd zk/native
cargo install cargo-fuzz
cargo fuzz run kzg_opening    # example target
cargo fuzz run bn254_pairing  # example target
popd


⸻

8) Benchmarks & perf guards

8.1 Python micro-bench

python zk/bench/verify_speed.py --json-out /tmp/zk_bench.json \
  --repeat 5 --warmup 1
cat /tmp/zk_bench.json | jq .

Compare against a saved baseline:

python zk/bench/verify_speed.py --json-out /tmp/new.json
python - <<'PY'
import json,sys
old=json.load(open("zk/bench/data/baseline.json"))
new=json.load(open("/tmp/new.json"))
for k in new:
    if new[k]["median_ms"] > 1.20*old[k]["median_ms"]:
        print("REGRESSION",k,new[k]["median_ms"],">",old[k]["median_ms"])
        sys.exit(1)
print("OK")
PY

8.2 Rust criterion benches

pushd zk/native
cargo bench
popd


⸻

9) Website & studio tests (optional)

From website/:

pnpm i
pnpm test:unit
pnpm test:e2e   # Playwright

CI Playwright runs spin a mock RPC and assert that status/head widgets render:

# See: website/tests/e2e/*.spec.ts


⸻

10) Determinism & flake-resistance
	•	Pin versions (lockfiles).
	•	Set stable RNG seeds (PYTHONHASHSEED=0, Hypothesis seeds).
	•	Avoid wall-clock/time dependence in tests (use fixed timestamps).
	•	Prefer pure-CPU code paths for CI; skip GPU tests or guard with markers.

⸻

11) Logging, artifacts, and debugging

Increase verbosity:

pytest -q -s -vv --log-cli-level=INFO

Store failing vectors:

pytest --basetemp /tmp/animica-test-tmp -q


⸻

12) CI mapping (GitHub Actions)
	•	Unit/Coverage: runs on all pushes/PRs.
	•	E2E/Integration: matrix (Linux/macOS) with sqlite.
	•	ZK Benches: optional, nightly, compares against baseline JSON.
	•	Native crate: cargo test + cargo bench on Linux/macOS runners.

Artifacts commonly uploaded:
	•	Coverage XML/HTML
	•	Bench JSON (zk_bench.json)
	•	Logs from failing tests

⸻

13) Common issues
	•	Missing native module: accel tests are skipped—install via maturin develop ....
	•	Apple Silicon ABI mismatch: ensure both Python and Rust target arm64.
	•	CI flake on timing: mark long-running tests as @pytest.mark.slow and exclude from default.

⸻

14) Useful snippets

Only run tests changed since last commit:

pytest -q $(git diff --name-only HEAD~1 | grep -E "_tests?/|tests?/.*\.py" | xargs)

Run a single test node:

pytest -q p2p/tests/test_end_to_end_two_nodes.py::test_sync_headers

Happy testing! ✅
