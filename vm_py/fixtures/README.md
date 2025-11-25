# vm_py/fixtures

Deterministic, hand-checked fixtures used by the Python-VM for examples, tests, and SDK demos.

## What’s here

- **counter_calls.json** — A canonical call sequence for the `examples/counter` contract:
  - `get` → `0`
  - `inc` → emits `Counter.Incremented { by: 1 }`
  - `get` → `1`
  - `inc_by(3)` → emits `Counter.Incremented { by: 3 }`
  - `get` → `4`
  Each entry specifies the call, expected return (if any), and expected events/logs.

- **abi_examples.json** — Minimal ABI encoding/decoding vectors:
  - Scalars: `int`, `bool`
  - Bytes-like values encoded as hex strings
  - Tuples and arrays
  - Edge cases (zero, max-uint-like bounds within VM limits)
  These are consumed by `vm_py/abi/{encoding,decoding}.py` tests to ensure round-trip stability.

## Conventions

- **Bytes** are hex strings with `0x` prefix, lowercase, even length (e.g. `0x00`, `0xdeadbeef`).
- **Integers** are JSON numbers within VM bounds; no strings for numbers.
- **Events** are objects `{ "name": "Pkg.Event", "args": { ... } }` where `args` values follow the same type rules.
- **Determinism**: The VM runtime uses a deterministic PRNG seeded from the call/tx hash; no wall-clock or IO is permitted.

## How to use

### Via CLI (local simulation)
```sh
# Compile & run the Counter example deterministically
python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call get
python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call inc
python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call get

In tests

from vm_py.runtime.loader import load_manifest_and_source
from vm_py.runtime.abi import call as abi_call

vm = load_manifest_and_source("vm_py/examples/counter/manifest.json")
res = abi_call(vm, "inc_by", [3])
assert res.return_value is None

Versioning

Fixture formats are intentionally simple JSON. If a breaking shape change is ever needed, bump test expectations alongside the change and annotate the top-level object with a "version": 1 field.

Reproducibility tips
	•	Keep all hex lowercased and lengths even.
	•	Avoid language-specific float/NaN values; the VM ABI has no floating point.
	•	When extending fixtures, prefer adding new cases rather than mutating existing ones to preserve historical stability.

