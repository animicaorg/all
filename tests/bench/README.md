# Benchmarks — reproducibility, CPU pinning, sample sizes

This directory documents how we run micro/meso-benchmarks across modules (e.g., `da/bench`, `mining/bench`, `studio-wasm/bench`, `consensus/cli`, etc.) in a way that is **repeatable**, **comparable**, and **CI-friendly**.

> TL;DR
> - Pin to a **single physical core** and **fixed frequency**.
> - Use **warmups**, **long enough samples**, and **deterministic seeds**.
> - Save results and compare with prior baselines.

---

## 1) Environment for reproducibility

### OS & power settings (Linux/Ubuntu assumed)
- Use a quiet system: close heavy apps, disable background indexing if possible.
- Set the CPU governor to `performance` for the duration of runs:
  ```bash
  sudo apt-get install -y linux-tools-common linux-tools-generic || true
  # On many distros:
  sudo cpupower frequency-set -g performance
  # Or for Intel P-States:
  echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null

	•	Optional: disable turbo/boost for tighter distributions (trade-off is lower absolute throughput):

# Intel (if available)
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo >/dev/null
# AMD (via cpupower):
sudo cpupower frequency-set -g performance



CPU pinning & NUMA
	•	Pin benchmarks to one core to reduce scheduler noise:

# Pick an isolated core; adjust "2" as needed
taskset -c 2 python -m pytest tests/bench -q


	•	For multi-socket boxes, also pin memory/CPU to a NUMA node:

numactl --physcpubind=2 --membind=0 python -m pytest tests/bench -q



Process & Python determinism
	•	Fix hash seed and project RNGs:

export PYTHONHASHSEED=0
export ANIMICA_TEST_SEED=1337


	•	Keep versions stable (pin with requirements*.txt or poetry.lock).
	•	Ensure the same build flags (e.g., AVX2 on/off) across runs if native libs are involved.

⸻

2) Running the built-in benches

Many modules ship focused benches:
	•	DA (NMT, Reed–Solomon, DAS):

taskset -c 2 python da/bench/nmt_build.py
taskset -c 2 python da/bench/rs_throughput.py


	•	Mining (inner loop, template latency):

taskset -c 2 python mining/bench/cpu_hashrate.py --theta 20
taskset -c 2 python mining/bench/template_latency.py


	•	Randomness/VDF:

taskset -c 2 python randomness/bench/vdf_verify_time.py


	•	studio-wasm (browser-like):

pnpm -C studio-wasm bench # see package README for Node/Playwright notes



If you add new benches, prefer a single-file script that prints a short JSON/CSV summary and exits with code 0.

⸻

3) Pytest-benchmark (optional, unified harness)

If you introduce Python tests that use pytest-benchmark, use these defaults for stable numbers:
	•	Warmup rounds: 2–5
	•	Min time per benchmark: ≥ 0.5 s
	•	Rounds: let the plugin adapt, but floor at 20
	•	Statistic to report: median + IQR (p50, p90, p99)
	•	Autosave & comparisons: enabled

Example invocation:

pip install pytest-benchmark
taskset -c 2 pytest tests/bench -k bench --benchmark-min-time=0.5 \
  --benchmark-warmup=on --benchmark-warmup-iterations=5 \
  --benchmark-autosave --benchmark-save=local-cpu2

Compare to a prior saved run:

taskset -c 2 pytest tests/bench -k bench --benchmark-compare=local-cpu2

Export to CSV/JSON:

pytest tests/bench -k bench --benchmark-json=bench.json


⸻

4) Sample sizes & timing guidance
	•	Micro-benchmarks (ns–µs scale):
	•	Aim for ≥ 1e6 ops total work if practical.
	•	Aggregate into batches to amortize timer overhead.
	•	Meso-benchmarks (ms–s scale):
	•	Target ≥ 30 repetitions and ≥ 10 seconds total wall time per case.
	•	Warm caches vs cold caches:
	•	Report both if relevant. For “realistic” numbers, include a short warm phase.
	•	Outliers:
	•	Use median + p90; avoid over-interpreting single-run max/min.
	•	Display units:
	•	Use ops/s for throughput, µs/op for latency; always state input sizes (e.g., “N=65,536 leaves”).

⸻

5) What to record with each run

Include these in your run notes/logs (or the JSON header):
	•	git describe / commit SHA of repo(s)
	•	CPU model (lscpu), core used, governor, turbo on/off
	•	RAM & NUMA node
	•	Python version & important library versions
	•	Command line, environment (notably PYTHONHASHSEED, taskset, numactl)
	•	Input sizes / parameters (e.g., NMT leaf count, RS (k,n), Θ, Γ caps)

Tip:

git describe --always --dirty
lscpu | egrep 'Model name|CPU$begin:math:text$s$end:math:text$|Thread|Core|Socket|MHz'


⸻

6) CI considerations
	•	CI hardware is noisy; use relative comparisons only and wide thresholds.
	•	Run a minimal subset with fixed seeds and reduced sizes to keep CI time down.
	•	Store baselines as artifacts and compare only when useful (e.g., regressions >15%).

⸻

7) Common pitfalls
	•	Comparing runs with different CPU freq/turbo states.
	•	Benchmarking tiny functions without batching (timer dominates).
	•	Mixing allocation-heavy Python code without GC control; consider gc.disable() in targeted micro-benches (re-enable after).
	•	Not isolating background load; prefer a quiet TTY/VM and a pinned core.

⸻

8) Template for new bench scripts

#!/usr/bin/env python3
# bench_foo.py
import json, time, os
from statistics import median

def run_case(param: int, reps: int = 50):
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        # ... run workload with 'param' ...
        t1 = time.perf_counter()
        samples.append((t1 - t0))
    return {
        "param": param,
        "reps": reps,
        "median_s": median(samples),
        "p90_s": sorted(samples)[int(0.9*len(samples))],
        "ops_per_s": reps / sum(samples) if sum(samples) else None,
    }

def main():
    param = int(os.getenv("BENCH_PARAM", "65536"))
    out = {
        "git": os.popen("git describe --always --dirty").read().strip(),
        "cpu_governor": os.popen("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null").read().strip(),
        "result": run_case(param),
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()

Run:

taskset -c 2 python bench_foo.py | tee bench_foo.json


⸻

Keep benches simple, pinned, and seeded. If numbers look odd, first re-check governor/turbo, pinning, and background load. Then look for GC/allocation hot spots and move more work into tight loops.

