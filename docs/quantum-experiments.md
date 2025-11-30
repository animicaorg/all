# Quantum experiments (simulator only)

This experimental module demonstrates how Animica could integrate
quantum-inspired signals into a future useful-work or PoW pipeline.
The implementation is intentionally lightweight and runs entirely on a
local simulator — there is no reliance on external quantum hardware.

## Goals

- Provide a minimal circuit/variational hook that can consume block
  data, such as a block hash and nonce, and emit a deterministic score.
- Keep quantum libraries optional so importing Animica core does not
  require Qiskit or PennyLane. A built-in simulator covers CI and local
  usage, while optional extras enable richer backends.

## Layout

- `animica.quantum.simulator`: tiny statevector simulator plus optional
  Qiskit path.
- `animica.quantum.experiment`: wraps simulator execution and exposes a
  deterministic helper seeded from PoW-like inputs.
- `animica.quantum.cli`: demonstration CLI runnable via
  `python -m animica.quantum.cli demo`.
- `python/tests/quantum`: tests exercising determinism, normalization,
  and the PoW helper.

## Optional dependency

The `quantum` extra in `python/pyproject.toml` installs Qiskit and
PennyLane for a more realistic simulator:

```bash
pip install .[quantum]
```

The code automatically falls back to the built-in simulator when those
libraries are missing.

## How it could plug into useful-work

1. Miner fetches or constructs a block header and nonce.
2. The data is passed to `simulate_from_pow_input`, producing a
   deterministic score derived from the quantum circuit's measurement
   probability.
3. The score can be mixed with other useful-work metrics or logged for
   offline analysis without impacting consensus-critical logic.

Because the module is simulator-only and optional, integrating it into
experiments poses no risk to the production chain. It simply offers a
reproducible signal that future useful-work research can evolve.
