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
