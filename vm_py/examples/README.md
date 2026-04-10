# Animica Python-VM Examples

This directory contains runnable local examples for `vm_py`:

- `counter/contract.py`
- `counter/manifest.json`
- `escrow/contract.py`
- `escrow/manifest.json`

All commands below are intended to be run from the repository root.

## Counter Quickstart

Compile from manifest:

```bash
python -m vm_py.cli.compile \
  --manifest vm_py/examples/counter/manifest.json \
  --out /tmp/counter.ir
```

Run a read call:

```bash
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call get
```

Run state-changing calls:

```bash
python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call set \
  --args '[5]'

python -m vm_py.cli.run \
  --manifest vm_py/examples/counter/manifest.json \
  --call inc
```

Notes:

- `vm_py.cli.run` accepts manifests using `source`, `entry`, `sources`, `code`, and `path` source declarations.
- Relative source paths are resolved relative to the manifest directory.
- Each CLI invocation is a fresh local simulation process.
