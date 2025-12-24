# Genesis samples and helpers

This directory ships sample genesis files for each Animica network profile and
small helpers to make sure the right one is installed before you start a node
or Compose stack.

## Quick commands

Each command below overwrites `core/genesis/genesis.json` with the matching
sample, so you always start from a clean profile:

```bash
bash genesis/devnet.sh   # copies genesis.sample.devnet.json
bash genesis/testnet.sh  # copies genesis.sample.testnet.json
bash genesis/mainnet.sh  # copies genesis.sample.mainnet.json
```

If you need to write to a custom path (for example inside a container mount),
set `DEST_GENESIS_PATH`:

```bash
DEST_GENESIS_PATH=/data/genesis.json bash genesis/devnet.sh
```

All helpers resolve paths relative to the repo root, so you can run them from
anywhere.

## Deterministic genesis generation

Use `tools/genesis/create_genesis.py` to deterministically build a new genesis bundle
(genesis.json + genesis.hash) from explicit inputs:

```bash
python tools/genesis/create_genesis.py \
  --chain-id 1 \
  --genesis-time 2026-01-01T00:00:00Z \
  --alloc-file /path/to/alloc.json \
  --consensus-file /path/to/consensus.json \
  --output-dir /path/to/output
```

Optional: pass `--db-uri sqlite:///...` to bootstrap a DB from the generated genesis.
