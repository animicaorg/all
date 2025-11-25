# zk/bench — micro & macro benchmarks for verifiers

This directory contains notes and scripts you can use to **measure verifier
performance** and **metering characteristics** in a reproducible way across
Groth16/PLONK+KZG/STARK (FRI) flows.

The focus is on:
- **Micro-benches**: core cryptographic kernels (pairing checks, MSM/KZG open,
  Poseidon rounds, small transcript ops).
- **Macro-benches**: end-to-end `ProofEnvelope` verification via
  `zk.integration.omni_hooks.zk_verify`, including input parsing and
  metering-unit computation.

> If you only care about *deterministic metering* (gas/units) and not crypto
> speed, use `meter_only=True` (see below). For performance tuning, run the full
> verification.

---

## What gets measured

### Micro
- **BN254 pairings** (Groth16): number of Miller loops and final exponentiation
  timings, backend-dependent (`py_ecc` baseline; native optional if present).
- **KZG opening** (PLONK): MSM (G1), a pairing vs fixed G2, and a small amount
  of transcript hashing.
- **Poseidon** (Fr) hashing: round function over test vectors (same parameters
  used by circuits; see `zk/verifiers/poseidon.py`).
- **Transcript FS**: squeeze/absorb overhead (SHA3-based).

### Macro
- End-to-end verify for an **actual Envelope**:
  - Groth16: SnarkJS-shaped proof + VK.
  - PLONK: PlonkJS-shaped proof + VK.
  - STARK (toy): FRI proof for a tiny Merkle-membership AIR.
- Report **wall clock time** and **units** (deterministic metering) separately.

---

## Prerequisites

- Python 3.11 (pinned recommended)
- Optional: native backends for EC/MSM (if you have them compiled);
  otherwise everything falls back to pure-Python (`py_ecc` etc.).
- Fixtures (proofs/VKs) for macro benches:
  - Groth16: `zk/tests/fixtures/groth16_embedding/{proof.json,vk.json[,public.json]}`
  - PLONK:   `zk/tests/fixtures/plonk_poseidon/{proof.json,vk.json[,public.json]}`
  - STARK:   `zk/tests/fixtures/stark_merkle/proof.json[,vk.json,public.json]`

You can override fixture directories via env vars:
`ZK_GROTH16_EMBED_DIR`, `ZK_PLONK_POSEIDON_DIR`, `ZK_STARK_MERKLE_DIR`.

---

## Environment toggles (backends & logging)

- `ZK_DISABLE_NATIVE=1` — force pure-Python paths.
- `ZK_FORCE_PYECC=1` — prefer `py_ecc` pairing for BN254 where selectable.
- `ZK_TEST_LOG=1` — enable INFO logs during benches.
- `PYTHONHASHSEED=0` — recommended for stable hashing overhead.

See also **zk/docs/REPRODUCIBILITY.md** for lockfiles and repro manifests.

---

## Quick micro-benches (one-liners)

> These are simple `timeit`-style runs; use `perf`/`pyperf`/`pytest-benchmark`
> for rigorous results.

### Pairing (Groth16 baseline)

```bash
python - <<'PY'
import time, statistics
from zk.verifiers.pairing_bn254 import pairing_check_demo # small helper if exposed
from zk.tests import configure_test_logging
configure_test_logging()

def run(n=50):
    t=[]
    for _ in range(n):
        t0=time.perf_counter()
        pairing_check_demo()   # performs 2 pairings (e(g1, g2) vs e(g1', g2'))
        t.append(time.perf_counter()-t0)
    print("pairing_check_demo:", "avg=", sum(t)/n, "p50=", statistics.median(t), "p95=", sorted(t)[int(n*0.95)-1])
run()
PY

KZG opening (PLONK)

python - <<'PY'
import time, statistics
from zk.verifiers.kzg_bn254 import demo_open_verify    # small demo helper if exposed
from zk.tests import configure_test_logging
configure_test_logging()
def run(n=50):
    t=[]
    for _ in range(n):
        t0=time.perf_counter()
        demo_open_verify()
        t.append(time.perf_counter()-t0)
    print("kzg_open_verify: avg=", sum(t)/n, "p50=", statistics.median(t), "p95=", sorted(t)[int(n*0.95)-1])
run()
PY

Poseidon hash (parameters used by circuits)

python - <<'PY'
import time, statistics
from zk.verifiers.poseidon import poseidon_hash, test_vector
from zk.tests import configure_test_logging
configure_test_logging()
inp = test_vector()["inputs"]
def run(n=1000):
    t=[]
    for _ in range(n):
        t0=time.perf_counter()
        poseidon_hash(inp)
        t.append(time.perf_counter()-t0)
    print("poseidon: avg=", sum(t)/n, "p50=", statistics.median(t), "p95=", sorted(t)[int(n*0.95)-1])
run()
PY


⸻

Macro-bench: end-to-end zk_verify

These runs include JSON load, canonicalization, transcript work, and the crypto
core. They also report metering units (deterministic gas proxy).

Groth16 (SnarkJS fixtures)

python - <<'PY'
import json, time
from pathlib import Path
from zk.integration.omni_hooks import zk_verify
from zk.tests import fixture_path, configure_test_logging
configure_test_logging()

base = Path(fixture_path("groth16_embedding"))
proof = json.load(open(base/"proof.json"))
vk = json.load(open(base/"vk.json"))
pub = proof.get("publicSignals") or json.load(open(base/"public.json"))

env = {"kind":"groth16_bn254","vk_format":"snarkjs","vk":vk,"proof":proof,"public_inputs":pub}
t0=time.perf_counter()
res = zk_verify(env)
dt=time.perf_counter()-t0
print("ok:", res["ok"], "units:", res["units"], "time_s:", dt)
PY

PLONK+KZG (PlonkJS fixtures)

python - <<'PY'
import json, time
from pathlib import Path
from zk.integration.omni_hooks import zk_verify
from zk.tests import fixture_path, configure_test_logging
configure_test_logging()

base = Path(fixture_path("plonk_poseidon"))
proof = json.load(open(base/"proof.json"))
vk = json.load(open(base/"vk.json"))
pub = proof.get("publicSignals") or json.load(open(base/"public.json"))

env = {"kind":"plonk_kzg_bn254","vk_format":"plonkjs","vk":vk,"proof":proof,"public_inputs":pub}
t0=time.perf_counter()
res = zk_verify(env)
dt=time.perf_counter()-t0
print("ok:", res["ok"], "units:", res["units"], "time_s:", dt)
PY

STARK (toy FRI Merkle)

python - <<'PY'
import json, time
from pathlib import Path
from zk.integration.omni_hooks import zk_verify
from zk.tests import fixture_path, configure_test_logging
configure_test_logging()

base = Path(fixture_path("stark_merkle"))
proof = json.load(open(base/"proof.json"))
pub = proof.get("public_inputs") or json.load(open(base/"public.json"))
vk = json.load(open(base/"vk.json")) if (base/"vk.json").exists() else {
  "air":"merkle_membership_v1","field":"bn254_fr","hash":"keccak","domain_log2":16,"num_queries":16
}

env = {"kind":"stark_fri_merkle","vk_format":"fri","vk":vk,"proof":proof,"public_inputs":pub}
t0=time.perf_counter()
res = zk_verify(env)
dt=time.perf_counter()-t0
print("ok:", res["ok"], "units:", res["units"], "time_s:", dt)
PY


⸻

Meter-only runs (crypto-free unit accounting)

If you just need deterministic units (gas proxy) independent of backend:

python - <<'PY'
import json
from pathlib import Path
from zk.integration.omni_hooks import zk_verify
from zk.tests import fixture_path

base = Path(fixture_path("groth16_embedding"))
proof = json.load(open(base/"proof.json"))
vk = json.load(open(base/"vk.json"))
pub = proof.get("publicSignals") or json.load(open(base/"public.json"))

env = {"kind":"groth16_bn254","vk_format":"snarkjs","vk":vk,"proof":proof,"public_inputs":pub}
print(zk_verify(env, meter_only=True))
PY


⸻

Tips for stable performance numbers
	•	CPU scaling: disable turbo / pin governor (performance) where possible.
	•	Affinity: run on isolated cores; avoid simultaneous multi-threading for
crypto baselines to reduce variance.
	•	Warm-up: discard the first few iterations (imports & JIT warm-ups).
	•	Lockfiles: pin Python/Node/Rust deps (see zk/docs/REPRODUCIBILITY.md).
	•	Backends: record which backend is used (pure-Python vs native bindings).
	•	CI runners: expect higher variance; prefer local bare-metal or dedicated
perf runners for publication-grade results.

⸻

Suggested reporting format

When comparing configurations, report:

Kind	Backend	Input size (KB)	Units	Time (ms)	Ops/s	Notes
Groth16 BN254	py_ecc	12	210	42.8	23.3	Miller×2 + finalexp
PLONK KZG BN254	msm+pairing	18	260	55.1	18.1	single opening
STARK FRI toy	python-only	30	190	31.6	31.6	16 queries

Units come from zk/integration/policy.py::compute_units; they’re stable
given the same envelope bytes, independent of machine speed.

⸻

Next steps
	•	Add pytest-benchmark integration and export results to JSON/CSV.
	•	Optionally wire a CFFI/ctypes path to native BN254/KZG backends and guard with
ZK_DISABLE_NATIVE.
	•	Include batch-verify demos for PLONK (single-opening multi-proof) and Groth16
(multi-pairing batching) when circuits and VKs support it.

