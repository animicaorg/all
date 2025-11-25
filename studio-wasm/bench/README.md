# studio-wasm /bench

Micro-benchmarks for the in-browser Python VM bundle (Pyodide + `vm_pkg`). These scripts help you measure:
- **Startup time** (Pyodide boot + module import)
- **Compile time** for example contracts
- **Run time** (simple call execution + gas accounting)

They are designed to be **repeatable**, **scriptable**, and CI-friendly (JSON output to stdout).

> ⚠️ Benchmarks run in a Node environment using the same loader code that the app uses in the browser. Results are indicative, not absolute; browser engines, caching, and CPU/GPU power will affect real-world numbers.

---

## Prerequisites

- **Node 18+** (for WebCrypto + fetch)
- **pnpm** or **npm** (examples below use `pnpm`)
- **Pyodide assets** fetched to `studio-wasm/vendor/` or a reachable CDN:
  - The repo provides `scripts/fetch_pyodide.mjs`. Run:
    ```bash
    pnpm -C studio-wasm node scripts/fetch_pyodide.mjs
    ```
  - Alternatively set `PYODIDE_BASE_URL` (e.g. to a CDN) so the loader can resolve assets.

---

## Quickstart

Run any benchmark with `tsx`:

```bash
# from repo root (or cd studio-wasm first)
pnpm -C studio-wasm dlx tsx bench/startup_time.ts
pnpm -C studio-wasm dlx tsx bench/compile_time.ts
pnpm -C studio-wasm dlx tsx bench/run_time.ts

Each script prints a single JSON line with the measured fields. Example:

{"name":"startup_time","cold_ms":1543,"warm_ms":312,"pyodide_version":"0.24.1","python":"3.11.4"}

You can redirect to a file and post-process:

pnpm -C studio-wasm dlx tsx bench/startup_time.ts >> bench.out.jsonl


⸻

Included benchmarks

1) startup_time.ts

Measures:
	•	Cold start: load Pyodide + initialize vm_pkg bridge
	•	Warm start: subsequent getPyodide() reuse cost (cache hit)

Outputs:
	•	cold_ms, warm_ms, pyodide_version, python

2) compile_time.ts

Measures compilation of the Counter example (examples/counter/contract.py) into IR using the same code path as the app (src/api/compiler.ts).

Outputs:
	•	compile_ms, ir_size_bytes, pyodide_version

3) run_time.ts

Measures a tiny call sequence over an ephemeral state:
	1.	get() (baseline)
	2.	inc() (mutates + emits event)
	3.	get() (should return 1)

Outputs:
	•	get0_ms, inc_ms, get1_ms, total_ms, gas_used_inc, events_emitted

⸻

Repro tips
	•	Warm vs cold: The first run after process start is “cold”. Use multiple runs to see standard deviation.
	•	Cache controls:
	•	Ensure PYODIDE_BASE_URL points to a local vendor/ directory for stable cold-start times.
	•	Delete node_modules/.cache and any HTTP caches between runs if you want fully cold measurements.
	•	CPU scaling: Disable turbo/boost to reduce variance when investigating regressions.
	•	Affinity: On Linux/macOS, pin the node process to a single core for cleaner results.

⸻

CI usage

Run as a job step and collect JSON lines for later analysis. Example (GitHub Actions):

- name: Bench startup
  run: pnpm -C studio-wasm dlx tsx bench/startup_time.ts >> $GITHUB_WORKSPACE/bench.jsonl

- name: Bench compile
  run: pnpm -C studio-wasm dlx tsx bench/compile_time.ts >> $GITHUB_WORKSPACE/bench.jsonl

- name: Bench run
  run: pnpm -C studio-wasm dlx tsx bench/run_time.ts >> $GITHUB_WORKSPACE/bench.jsonl

You can then upload bench.jsonl as an artifact or parse & comment on PRs.

⸻

Troubleshooting
	•	“Failed to load Pyodide assets”
Ensure you ran node scripts/fetch_pyodide.mjs or set PYODIDE_BASE_URL to a reachable CDN (matching the version in .env.example / pyodide.lock.json).
	•	ReferenceError: crypto is not defined
You must use Node 18+ (WebCrypto). Verify with node -v.
	•	“TypeError: fetch is not a function”
Our test bootstrap uses Undici when necessary, but for benches we rely on global fetch in Node 18+. Upgrade Node or import a fetch polyfill if you’ve customized the setup.
	•	Variance is high
Close background apps, disable thermal throttling, and run multiple iterations; consider averaging and reporting P95.

⸻

Notes
	•	These benches intentionally avoid unit-test frameworks to reduce overhead and interference.
	•	The scripts use the same public APIs exported by src/index.ts that the web app uses, so regressions here are usually meaningful for real users.

