# AICF Provider Worker

Reference GPU provider worker for AICF on Animica.

This package ships a cross-platform worker with:

- `init-config` for first-run config generation
- `benchmark` for hardware detection and score emission
- `start` for heartbeat/worker runtime loop
- `health` for connectivity checks
- launcher scripts for Linux and Windows bundles

## Quickstart (Python)

```bash
python worker.py init-config --config provider.config.json
python worker.py benchmark --config provider.config.json
python worker.py start --config provider.config.json
```

## Bundle Build

```bash
./scripts/build_bundles.sh 0.2.0
```

This creates versioned artifacts in:

- `dist/provider/windows`
- `dist/provider/linux`
- `dist/provider/python`
- `dist/provider/manifest.json`

and mirrors them to `website/public/provider/`.
