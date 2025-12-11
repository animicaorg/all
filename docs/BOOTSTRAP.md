# Animica Mainnet Genesis Bootstrap Guide

This document explains the one-time mainnet genesis bootstrap process and password requirements.

## Overview

Animica mainnet genesis creation is protected by a password gate to ensure that only authorized operators can initialize the canonical mainnet genesis. This is a **one-time process** required only for the first mainnet node.

## Prerequisites

- Genesis sample file: `genesis/genesis.sample.mainnet.json`
- Database URI for storing the genesis (e.g., `sqlite:///animica.db`)
- Bootstrap password: `animicawins` (required for mainnet only)

## Bootstrap Process

### First-Time Mainnet Genesis Creation

To create the mainnet genesis for the first time, run the bootstrap command:

```bash
python -m core.bootstrap \
  --genesis-sample genesis/genesis.sample.mainnet.json \
  --db sqlite:///animica.db \
  --network mainnet
```

You will be prompted to enter the bootstrap password:

```
Enter Animica mainnet bootstrap password:
```

Enter `animicawins` (the password will not be echoed to the terminal for security).

**Alternative: Environment Variable**

For automated or production deployments, you can set the bootstrap password via environment variable:

```bash
export ANIMICA_BOOTSTRAP_PASSWORD="animicawins"
python -m core.bootstrap \
  --genesis-sample genesis/genesis.sample.mainnet.json \
  --db sqlite:///animica.db \
  --network mainnet
```

This allows the bootstrap to proceed without an interactive prompt.

On success, you will see:

```
=== Animica mainnet bootstrap complete ===
DB:            sqlite:///animica.db
Genesis:       genesis/genesis.sample.mainnet.json
Chain ID:      1
Head height:   0
Head hash:     0x...
Status:        Mainnet genesis initialized successfully.

Subsequent node startups will not require the bootstrap password.
Use `python -m core.boot` for normal node startup.
```

### Subsequent Node Startups

After the genesis has been created, subsequent node startups do **not** require the password:

```bash
python -m core.boot \
  --genesis genesis/genesis.sample.mainnet.json \
  --db sqlite:///animica.db
```

The node will detect that genesis already exists and proceed without prompting for a password.

### Non-Mainnet Networks (Devnet, Testnet)

Non-mainnet networks (devnet, testnet) do **not** require the bootstrap password. You can use either the bootstrap command or the normal boot command:

```bash
# Devnet genesis (no password required)
python -m core.bootstrap \
  --genesis-sample genesis/genesis.sample.devnet.json \
  --db sqlite:///devnet.db \
  --skip-password

# Or use normal boot for devnet/testnet
python -m core.boot \
  --genesis genesis/genesis.sample.devnet.json \
  --db sqlite:///devnet.db
```

## Mainnet Premine

The mainnet genesis includes a one-time premine of **81,000,000 ANM** (81,000,000,000,000,000 base units) distributed as follows:

| Recipient          | Amount (ANM)   | Amount (base units)      |
|--------------------|----------------|--------------------------|
| Foundation         | 45,000,000     | 45,000,000,000,000,000   |
| Treasury           | 20,000,000     | 20,000,000,000,000,000   |
| AICF               | 7,000,000      | 7,000,000,000,000,000    |
| Founder            | 9,000,000      | 9,000,000,000,000,000    |
| **Total**          | **81,000,000** | **81,000,000,000,000,000** |

The user-provided address `anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9` is included in the distribution (currently allocated 0 ANM as a placeholder; adjust per design requirements).

### Premine Enforcement

- **Height 0 only**: The premine is issued **only** at genesis (height 0) for mainnet (chain_id == 1).
- **No multi-block window**: There is no multi-block premine window; only the genesis block is special.
- **From height >= 1**: Normal emission schedule applies (per `spec/params.yaml`).
- **Validation**: The genesis loader validates that mainnet genesis coinbase matches the configured premine total and distribution exactly.

## Security Notes

### Password Handling

- The bootstrap password is **never logged or persisted**.
- It can be set via the `ANIMICA_BOOTSTRAP_PASSWORD` environment variable or defaults to a hardcoded value in `core/bootstrap.py`.
- For production deployments, consider using environment variables or additional security measures (HSM, multi-sig, ceremony-style bootstrap).
- The password prompt uses `getpass` for non-echo terminal input.
- Incorrect password entry exits cleanly without creating genesis.

### Mainnet-Specific Enforcement

- Premine enforcement is **network-specific**: only mainnet (chain_id == 1) requires the premine.
- Other networks (devnet, testnet) follow their own genesis allocation rules.
- Reward logic is deterministic and depends only on `(chain_id, height, params)`.

## Troubleshooting

### Error: "Genesis already exists"

If you see this error:

```
Genesis already exists. Bootstrap is only required for first-time genesis creation.
```

This means the database already contains a genesis block. You can proceed with normal node startup using `python -m core.boot`.

### Error: "Bootstrap password incorrect"

If you see this error:

```
Bootstrap password incorrect. Aborting.
```

You entered the wrong password. The correct password is `animicawins`. Note that the password is case-sensitive and must be entered exactly as shown.

### Error: "Genesis file not found"

If you see this error:

```
[boot] genesis file not found: <path>

For mainnet genesis creation, run the bootstrap command:
    python -m core.bootstrap --network mainnet --genesis-sample <path> --db <uri>
```

The specified genesis file does not exist. Check the path and ensure the file is present.

## Command-Line Reference

### `python -m core.bootstrap`

Bootstrap mainnet genesis with password gate.

**Options:**

- `--genesis-sample <path>`: Path to genesis sample file (e.g., `genesis/genesis.sample.mainnet.json`) [required]
- `--db <uri>`: Database URI (e.g., `sqlite:///animica.db` or `rocksdb:///data`) [required]
- `--network <name>`: Network name (mainnet, testnet, devnet) [default: mainnet]
- `--skip-password`: Skip password prompt (for testing or non-mainnet networks)
- `--log <level>`: Log level (debug, info, warn, error) [default: info]

### `python -m core.boot`

Normal node startup (no password required if genesis exists).

**Options:**

- `--genesis <path>`: Path to genesis file [default: `core/genesis/genesis.json`]
- `--db <uri>`: Database URI [default: `sqlite:///animica.db`]
- `--log <level>`: Log level (debug, info, warn, error) [default: info]

## Implementation Details

### Modules

- **`consensus/rewards.py`**: Mainnet premine constants and reward calculation logic
  - `MAINNET_PREMINE_TOTAL`: 81,000,000 ANM in base units
  - `MAINNET_PREMINE_DISTRIBUTION`: List of (address, amount) tuples
  - `compute_block_reward(chain_id, height, params)`: Calculate block rewards
  - `validate_mainnet_genesis_coinbase(chain_id, height, coinbase_outputs)`: Validate genesis coinbase

- **`core/bootstrap.py`**: Password-gated genesis creation
  - `BOOTSTRAP_PASSWORD`: The required password (never logged)
  - `bootstrap_mainnet_genesis(genesis_path, db_uri, ...)`: Main bootstrap function
  - `validate_bootstrap_password(entered)`: Password validation
  - `genesis_exists(db_uri)`: Check if genesis already exists

- **`core/genesis/loader.py`**: Genesis loading and validation
  - Updated to validate mainnet genesis coinbase matches premine

- **`core/boot.py`**: Normal node startup
  - Updated to suggest bootstrap command if genesis is missing

### Tests

- **`consensus/tests/test_rewards.py`**: Tests for premine constants and reward logic (21 tests, all passing)
- **`tests/unit/test_bootstrap.py`**: Tests for bootstrap password gate (9 tests, all passing)

## References

- Genesis samples: `genesis/genesis.sample.{mainnet,testnet,devnet}.json`
- Chain parameters: `spec/params.yaml`
- Emission schedule: See `spec/params.yaml` under `networks.[network].monetary.issuance`
- PoIES consensus: `spec/poies_math.md`
