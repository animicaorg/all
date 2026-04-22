# Animica Scripts

Utility scripts for Animica node operators and developers.

## Node Operations

### update_genesis_hash.py

Update the genesis hash in an existing node database to match a new genesis file. Useful for handling genesis resets without losing all chain data.

**Use cases:**
- Mainnet genesis reset (2026-01)
- Updating verifier nodes after a network upgrade
- Switching between genesis configurations

**Examples:**

```bash
# Update mainnet genesis (recommended)
python scripts/update_genesis_hash.py --network mainnet

# Dry-run to preview changes
python scripts/update_genesis_hash.py --network mainnet --dry-run

# Update with explicit paths
python scripts/update_genesis_hash.py \
  --db-uri sqlite:///$HOME/.animica/chain-1/animica.db \
  --genesis-path core/genesis/mainnet.json

# Force update even if hash matches
python scripts/update_genesis_hash.py --network mainnet --force
```

**See also:**
- [VERIFIER_NODE_RESTART.md](../docs/VERIFIER_NODE_RESTART.md) - Comprehensive guide for verifier node operators
- [CHAIN_RESET.md](../docs/CHAIN_RESET.md) - Mainnet genesis reset documentation

## Development & Testing

### deploy_aicf_contracts.py

Compile and deploy the core AICF VM-PY contracts, then inject deployed contract
addresses into `apps/aicf-api/.env`.

**Examples:**

```bash
# Using wallet label from wallets.json
python3 scripts/deploy_aicf_contracts.py \
  --rpc http://127.0.0.1:8545/rpc \
  --chain-id 1337 \
  --wallet-label "demo-miner"

# Using mnemonic
python3 scripts/deploy_aicf_contracts.py \
  --rpc http://127.0.0.1:8545/rpc \
  --chain-id 1337 \
  --mnemonic "$DEPLOYER_MNEMONIC"

# Using raw seed hex
python3 scripts/deploy_aicf_contracts.py \
  --rpc https://rpc.animica.org/rpc \
  --chain-id 1 \
  --seed-hex "$DEPLOYER_SEED_HEX" \
  --print-json

# Using wallet label with explicit wallet file
python3 scripts/deploy_aicf_contracts.py \
  --rpc https://rpc.animica.org/rpc \
  --chain-id 1 \
  --wallet-file "$HOME/.animica/wallets.json" \
  --wallet-label "prod-deployer" \
  --print-json
```

### bootstrap_explorer_cache.py

Bootstrap the explorer cache for faster initial load.

### mining_smoke.py

Quick smoke test for mining functionality.

### build_miner_packages.sh

Build the production mining starter bundles used by the Animica mining portal.

### toy_miner.py

Simple demonstration miner for testing.

### test_all.sh

Run all tests across the repository.

### verify_snapshot_system.py

Verify the snapshot system integrity.

## Debugging

### debug_tx_sig.sh

Debug transaction signature issues.

### kill_port.py

Kill processes listening on specific ports.

## Infrastructure

### fetch_amd_snp_roots.sh
### fetch_intel_pcs_roots.sh

Fetch trusted execution environment certificate roots.

### update-rust-toolchain.sh

Update Rust toolchain to the required version.

### run_testnet.sh

Quick testnet node startup script.

### preview_web.sh

Preview web UI components locally.

## Running Tests

Each script may have associated tests in `scripts/tests/`. Run them with:

```bash
# All script tests
pytest scripts/tests/

# Specific test file
pytest scripts/tests/test_update_genesis_hash.py

# With verbose output
pytest scripts/tests/ -v
```

## Contributing

When adding new scripts:
1. Add execute permissions: `chmod +x scripts/new_script.py`
2. Include a docstring with usage examples
3. Add tests in `scripts/tests/test_new_script.py`
4. Update this README with a brief description
5. Consider adding detailed documentation in `docs/` for complex tools
