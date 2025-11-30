"""
Lightweight Quantum job runner used by the AICF Provider (template).

This module deliberately avoids heavyweight dependencies so the template
boots fast everywhere. It implements a *toy* sampler that produces
bitstring measurement outcomes from a very small "IR":

Accepted `QuantumJobIn.circuit` shapes
--------------------------------------
1) OpenQASM(2.x) string (very partial; only qreg parsing is honored):
    - The sampler will detect the number of qubits from:  qreg q[N];
    - Distribution defaults to unbiased (50/50 for each qubit).

2) JSON object with (some of) the following keys:
    {
      "n_qubits": 5,                       # required
      "bias": [0.1, 0.9, 0.5, 0.5, 0.2],   # optional, per-qubit P(1)
      "depth": 42,                          # optional, metadata only
      "name": "demo"
    }

Traps
-----
If `include_traps=True` on the job, we synthesize a basic "trap check"
that re-evaluates a deterministic subset of shots and reports whether
the empirical frequencies are within a loose tolerance window. This is
not cryptographic; it exists for *demo and integration* value, so your
provider can surface a `trap_checks` field that downstream systems can
display while you implement real trap circuits.

Determinism vs. randomness
--------------------------
- By default, the sampler seeds Python's `random.Random` using a stable
  hash derived from the job's circuit and (optional) client_job_id.
- If you set the environment variable `AICF_PROVIDER_SEED`, that value
  (an integer) is used as the seed instead—useful for reproducible tests.

Outputs
-------
Returns a `QuantumResult` with:
- `bitstrings`: a compact list of raw shot outcomes as bitstrings.
- `histogram`: aggregated counts {bitstring -> count}.
- `trap_checks`: a small dictionary with 'passed', 'ratio', and 'tolerance'.
- `backend`: the backend name string ("simple-sampler/v1").

This module is intended as a drop-in starting point. Replace the
`SimpleSamplerBackend` with a real quantum backend adapter (local
simulator, remote QPU API, etc.) when you’re ready.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple, Union

from .models import QuantumJobIn, QuantumResult

BACKEND_NAME = "simple-sampler/v1"
# Soft upper bound on total produced bits to avoid accidental memory blow-ups.
MAX_TOTAL_BITS = 20_000_000  # e.g. 20M (n_qubits * shots)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _stable_seed_for_job(job: QuantumJobIn) -> int:
    """
    Produce a stable seed so repeated submissions with the same inputs
    yield the same distribution (useful for demos & tests).

    Precedence:
      1) env AICF_PROVIDER_SEED (if set, integer)
      2) sha3_256 of (normalized_circuit_json | client_job_id or "")
    """
    env = os.getenv("AICF_PROVIDER_SEED")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            # fall back to hash-based seed if env was invalid
            pass

    # Normalize the circuit for hashing
    if isinstance(job.circuit, dict):
        circuit_bytes = json.dumps(
            job.circuit, sort_keys=True, separators=(",", ":")
        ).encode()
    else:
        circuit_bytes = str(job.circuit).encode()

    h = hashlib.sha3_256()
    h.update(b"animica:aicf:simple-quantum-v1|")
    h.update(circuit_bytes)
    h.update(b"|client_job_id=")
    h.update((job.client_job_id or "").encode())
    # reduce to 32-bit range for Python's Random seed ergonomics
    return int.from_bytes(h.digest()[:4], "big")


def _parse_qasm_qubits(qasm: str) -> Optional[int]:
    """
    Extremely small OpenQASM parser: extract N from 'qreg q[N];'.
    Returns None if not found.
    """
    m = re.search(r"qreg\s+\w+\s*\[\s*(\d+)\s*\]\s*;", qasm)
    return int(m.group(1)) if m else None


def _normalize_circuit(
    job: QuantumJobIn,
) -> Tuple[int, Optional[List[float]], Dict[str, Union[str, int, float]]]:
    """
    Normalize various circuit representations into:
        (n_qubits, bias_list_or_None, meta)

    bias_list[i] gives P(1) for qubit i. If None, sampler assumes 0.5.
    `meta` is free-form metadata to echo back if desired (unused by logic).
    """
    if isinstance(job.circuit, dict):
        if "n_qubits" not in job.circuit:
            raise ValueError("circuit object must include 'n_qubits'")
        n_qubits = int(job.circuit["n_qubits"])
        if n_qubits <= 0 or n_qubits > 4096:
            raise ValueError(f"n_qubits out of range: {n_qubits}")

        bias = job.circuit.get("bias")
        if bias is not None:
            if not isinstance(bias, list) or len(bias) != n_qubits:
                raise ValueError("circuit.bias must be a list of length n_qubits")
            for p in bias:
                if not (0.0 <= float(p) <= 1.0):
                    raise ValueError("each bias entry must be within [0,1]")
            bias = [float(p) for p in bias]
        meta = {}
        for k in ("name", "depth"):
            if k in job.circuit:
                meta[k] = job.circuit[k]
        return n_qubits, bias, meta

    # String circuit — assume OpenQASM-ish, try to read qreg size
    if not isinstance(job.circuit, str):
        raise ValueError("circuit must be JSON object or QASM string")

    n_qubits = _parse_qasm_qubits(job.circuit) or 4  # gentle default
    return n_qubits, None, {"parsed_from": "qasm", "assumed_default_bias": True}


def _ensure_budget(n_qubits: int, shots: int) -> None:
    total_bits = n_qubits * shots
    if total_bits > MAX_TOTAL_BITS:
        raise ValueError(
            f"shot budget too large: n_qubits*shots = {total_bits:,} > {MAX_TOTAL_BITS:,} "
            f"(n_qubits={n_qubits}, shots={shots})"
        )


def _sample_bitstrings(
    rng: random.Random,
    n_qubits: int,
    shots: int,
    bias: Optional[List[float]],
) -> List[str]:
    """
    Sample `shots` outcomes. Each outcome is an n_qubits-length bitstring.
    If `bias` is None, sample each qubit with p=0.5 independently.
    """
    p = bias or [0.5] * n_qubits
    out: List[str] = []
    # micro-optimization: localize for-loop attributes
    rand = rng.random
    for _ in range(shots):
        bits = ["1" if rand() < p_i else "0" for p_i in p]
        out.append("".join(bits))
    return out


def _histogram(bitstrings: Iterable[str]) -> Dict[str, int]:
    hist: Dict[str, int] = {}
    for b in bitstrings:
        hist[b] = hist.get(b, 0) + 1
    return hist


def _trap_check(
    rng: random.Random,
    n_qubits: int,
    bitstrings: List[str],
    *,
    min_ratio: float = 0.05,
    tolerance: float = 0.15,
) -> Dict[str, Union[bool, float, Dict[str, float]]]:
    """
    A tiny "sanity" trap: pick a pseudo-random qubit index `i` and assert
    its observed proportion of '1' is within [0.5±tolerance].

    This is *not* a real trap construction; it simply demonstrates how a
    provider can attach a check that downstream systems display.

    Returns:
      {
        "passed": True/False,
        "ratio": observed_ratio_for_qubit_i,
        "tolerance": tolerance,
        "qubit": i
      }
    """
    if not bitstrings:
        return {"passed": False, "ratio": 0.0, "tolerance": tolerance, "qubit": 0}

    # use at least min_ratio of the shots, sampled without replacement
    shots = len(bitstrings)
    sample_size = max(1, int(math.ceil(shots * min_ratio)))

    i = rng.randrange(0, n_qubits)
    indices = list(range(shots))
    rng.shuffle(indices)
    indices = indices[:sample_size]

    ones = 0
    for idx in indices:
        ones += 1 if bitstrings[idx][i] == "1" else 0

    ratio = ones / float(sample_size)
    passed = (0.5 - tolerance) <= ratio <= (0.5 + tolerance)
    return {"passed": passed, "ratio": ratio, "tolerance": tolerance, "qubit": i}


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


@dataclass
class SamplerConfig:
    """Configuration toggles for the simple sampler backend."""

    name: str = BACKEND_NAME
    trap_min_ratio: float = 0.06  # fraction of shots used by trap check
    trap_tolerance: float = 0.18  # how far from 50/50 we still accept


class SimpleSamplerBackend:
    """
    Extremely small backend that synthesizes outcomes by independent
    per-qubit Bernoulli draws with optional bias.

    Replace this class with a real backend adapter (e.g. qiskit, braket,
    remote QPU vendor) when integrating with production hardware.
    """

    def __init__(self, cfg: Optional[SamplerConfig] = None) -> None:
        self.cfg = cfg or SamplerConfig()

    def run(self, job: QuantumJobIn) -> QuantumResult:
        # Normalize inputs
        n_qubits, bias, meta = _normalize_circuit(job)
        _ensure_budget(n_qubits, job.shots)

        # Create RNG with stable seed
        seed = _stable_seed_for_job(job)
        rng = random.Random(seed)

        t0 = time.perf_counter()
        bitstrings = _sample_bitstrings(rng, n_qubits, job.shots, bias)
        # Optional trap check
        trap_checks = None
        if job.include_traps:
            trap_checks = _trap_check(
                rng,
                n_qubits,
                bitstrings,
                min_ratio=self.cfg.trap_min_ratio,
                tolerance=self.cfg.trap_tolerance,
            )
        duration = time.perf_counter() - t0

        result = QuantumResult(
            job_id="",
            # Caller (service layer) should fill the actual job_id before returning.
            # We leave it blank here to keep the backend independent of queuing.
            kind="quantum",
            bitstrings=bitstrings,
            histogram=_histogram(bitstrings),
            trap_checks=trap_checks,
            backend=self.cfg.name,
            duration_s=duration,
            raw={"meta": meta, "seed": seed},
        )
        return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_backend_singleton: Optional[SimpleSamplerBackend] = None


def get_backend() -> SimpleSamplerBackend:
    """Singleton accessor (cheap & stateless)."""
    global _backend_singleton
    if _backend_singleton is None:
        _backend_singleton = SimpleSamplerBackend()
    return _backend_singleton


def run_quantum_job(
    job: QuantumJobIn, *, job_id: Optional[str] = None
) -> QuantumResult:
    """
    High-level helper used by route handlers:
      - Executes the job on the sampler backend
      - Fills `job_id` into the result (if provided)
    """
    res = get_backend().run(job)
    if job_id is not None:
        res.job_id = job_id
    return res


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Small demo when running this module directly:
    demo_job = QuantumJobIn(
        kind="quantum",
        circuit={"n_qubits": 3, "bias": [0.2, 0.8, 0.5], "name": "demo", "depth": 8},
        shots=2000,
        include_traps=True,
        backend="simple",  # user-provided field; not used by the backend template
        client_job_id="demo-123",
    )
    out = run_quantum_job(demo_job, job_id="job_demo_001")
    print("backend:", out.backend)
    print("job_id:", out.job_id)
    print("shots:", len(out.bitstrings))
    print("unique bitstrings:", len(out.histogram))
    print("trap_checks:", out.trap_checks)
