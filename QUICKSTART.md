# Animica L1 Blockchain - Quickstart Guide

## Network Configuration

Animica supports multiple network profiles:
- **mainnet** (chain ID 1) - Production network with premine allocation
- **testnet** (chain ID 2) - Public test network
- **devnet** (chain ID 1337) - Local development network
- **local-devnet** - Alternative local development setup

### Default Network: Mainnet

**By default, all CLI commands and RPC configurations use mainnet** unless explicitly overridden.

To use a different network:

```bash
# Option 1: Set network environment variable (persistent for shell session)
export ANIMICA_NETWORK=devnet
animica node status

# Option 2: Use --network flag (per-command)
animica --network devnet node status

# Option 3: Set persistent network preference
animica network set devnet
animica node status  # Now uses devnet
```

### Database Isolation Per Network

**Each network uses a separate database directory to prevent state contamination:**

- **Mainnet:** `~/.animica/chain-1/`
- **Testnet:** `~/.animica/chain-2/`
- **Devnet:** `~/.animica/chain-1337/`

When you switch networks using `animica network set`, the system automatically:
1. Points to the correct RPC endpoint for that network
2. Uses the appropriate genesis file (mainnet uses `core/genesis/genesis.json`)
3. Reads/writes to the network-specific database directory

This ensures that switching between networks doesn't contaminate state or lose data. Your mainnet state remains intact when testing on devnet.

### RPC URL Configuration

The CLI automatically uses network-specific RPC URLs when `ANIMICA_RPC_URL` is not set:

- **Mainnet**: `http://127.0.0.1:8545/rpc` (default)
- **Testnet**: `http://127.0.0.1:18546/rpc`
- **Devnet**: `http://127.0.0.1:28545/rpc`
- **Local-devnet**: `http://127.0.0.1:38545/rpc`

**No manual configuration needed!** Commands like `animica node status`, `animica rpc call`, and `animica wallet show` will work without setting `ANIMICA_RPC_URL`.

> **Bootstrap-only public RPC:** `http://127.0.0.1:8545/rpc` is reserved for bootstrapping (seed discovery, headers, and manifests). Run `animica node up` and use your local RPC port for normal operations.

To override the default:
```bash
export ANIMICA_RPC_URL=http://custom-host:8888/rpc
# or per-command:
animica --rpc-url http://custom-host:8888/rpc node status
```

**Note**: Empty strings (`ANIMICA_RPC_URL=""`) are treated as unset and will use the network default.

### Port Configuration

Each network uses non-conflicting default ports to allow running multiple networks simultaneously:

**Mainnet:**
- RPC: 8545, P2P: 30333, Metrics: 9000

**Testnet:**
- RPC: 18546, P2P: 31334, Metrics: 19000

**Devnet:**
- RPC: 28545, P2P: 31335, Metrics: 29000

**Local-devnet:**
- RPC: 38545, P2P: 31336, Metrics: 39000

Ports can be customized via environment variables: `HOST_RPC_PORT`, `HOST_P2P_PORT`, `HOST_METRICS_PORT`

### Checking Premine Balances (Mainnet)

To check premine wallet balances on mainnet:

```bash
# Show wallet info including balance
animica wallet show <address|label>

# Example: Check premine wallet by label
animica wallet show premine

# Or use RPC directly
animica rpc call state.getBalance '{"params": ["anim1..."]}'

# Wallet file location (default)
# ~/.animica/wallets.json
```

## Fresh Machine Setup (Ubuntu/Debian)

### Install System Dependencies
```bash
# Update package lists
sudo apt-get update

# Install Python 3.11+
sudo apt-get install -y python3.11 python3.11-venv python3-pip

# Install Node.js 20+ and npm
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install Docker
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker  # Or log out and back in

# Install build tools (for Rust, if needed)
sudo apt-get install -y build-essential pkg-config libssl-dev curl
```

### Clone and Setup Repository
```bash
# Clone the repository
git clone https://github.com/animicaorg/all.git
cd all

# Run setup script (installs Python venv, pnpm, and dependencies)
./setup.sh

# Activate Python virtual environment
source .venv/bin/activate

# Verify installation
python --version  # Should be 3.11+
pytest --version  # Should show pytest
```

## Running Tests

### Full Test Suite
```bash
# Activate environment
source .venv/bin/activate

# Run all tests
./testall.sh
```

Expected results:
- Python: ~321 tests passing
- Node: Tests require `pnpm install` in workspaces
- Rust: Tests require nasm/yasm (optional)

### Python Tests Only
```bash
source .venv/bin/activate

# All Python tests
pytest -q

# Specific module
pytest consensus/tests/ -v
pytest execution/tests/ -v
pytest rpc/tests/ -v
pytest mempool/tests/ -v
pytest p2p/tests/ -v

# With coverage
pytest --cov=consensus consensus/tests/
```

### Fast Smoke Test
```bash
# Run only fast unit tests, skip slow integration tests
pytest -m "not slow and not integration" -q
```

## Testing P2P Decentralization

**Verify that Animica is fully decentralized by running multiple nodes that discover and connect to each other.**

### Quick Multi-Node Test (5 minutes)

```bash
# Start 3 nodes with P2P enabled
docker-compose -f docker-compose.multinode.yml up -d

# Wait 30 seconds for peer discovery
sleep 30

# Check Node 1 peers (should show node2 and node3)
curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Check Node 2 peers  
curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"p2p.listPeers","params":[]
}' | jq '.result | length'

# Verify all nodes have same chain height
echo "Node 1:" && curl -s http://localhost:8545/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'
echo "Node 2:" && curl -s http://localhost:8546/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'
echo "Node 3:" && curl -s http://localhost:8547/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":[]}' | jq '.result.height'

# Stop nodes
docker-compose -f docker-compose.multinode.yml down
```

**Expected Results:**
- ✅ Each node shows 1-2 connected peers
- ✅ All nodes report the same blockchain height
- ✅ No central server needed - fully peer-to-peer!

See [MULTINODE_QUICKSTART.md](MULTINODE_QUICKSTART.md) for detailed instructions.

## Devnet Setup

**Note:** Since the default network is mainnet, you must explicitly set the network to devnet for local development.

### Option 1: Docker Compose (Recommended)
```bash
# Set network to devnet before starting
export ANIMICA_NETWORK=devnet

# Start devnet (node + miner + explorer + services)
bash tests/devnet/up.sh

# Check status
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet ps

# View node logs
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f node1

# Access services:
# - Node 1 RPC: http://localhost:38545  (default for local-devnet)
# - Node 2 RPC: http://localhost:39545  (default for local-devnet)
# - Explorer: http://localhost:5173
# - Studio Services: http://localhost:8787

# Stop devnet
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down

# Clean up (remove volumes)
bash tests/devnet/cleanup.sh
```

### Option 2: Manual Node Start
```bash
# Activate environment
source .venv/bin/activate

# Set network to devnet
export ANIMICA_NETWORK=devnet

# Set genesis for devnet
bash genesis/devnet.sh

# Initialize database
python -m core.boot \
  --genesis core/genesis/genesis.json \
  --db sqlite:///data/animica.db

# Start RPC server
python -m rpc.server \
  --db sqlite:///data/animica.db \
  --genesis core/genesis/genesis.json \
  --chain-id 1337 \
  --host 0.0.0.0 \
  --port 8545 \
  --cors "[*]" \
  --log-level INFO
```

## Basic Operations

### Check Node Status
```bash
source .venv/bin/activate

# Get chain head
python -m python.animica.cli.node head --rpc-url http://localhost:8545

# Get full status
python -m python.animica.cli.node status --rpc-url http://localhost:8545

# Or use curl
curl -X POST http://localhost:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
```

### Create Wallet
```bash
source .venv/bin/activate

# Create new wallet
python -m python.animica.cli.wallet new

# List wallets
python -m python.animica.cli.key list

# Export wallet
python -m python.animica.cli.wallet export --address <addr> --output /tmp/wallet.json
```

### Send Transaction
```bash
source .venv/bin/activate

# Send value transfer
python -m python.animica.cli.tx send \
  --from <sender-address> \
  --to <recipient-address> \
  --value 1.5 \
  --rpc-url http://localhost:8545 \
  --chain-id 1337

# Check transaction status
python -m python.animica.cli.chain get-tx --hash <tx-hash> --rpc-url http://localhost:8545
```

### Deploy Contract
```bash
source .venv/bin/activate

# Example: Deploy counter contract

# 1. Compile the contract
python -m vm_py.cli.compile \
  --manifest contracts/packages/counter/manifest.json \
  --out-dir /tmp/counter

# 2. Deploy via SDK
python -m omni_sdk.cli.deploy \
  --rpc http://localhost:8545 \
  --chain-id 1337 \
  --keystore ~/.animica/keystore.json \
  --manifest contracts/packages/counter/manifest.json \
  --ir /tmp/counter/counter.ir

# 3. Call contract method
python -m omni_sdk.cli.call \
  --rpc http://localhost:8545 \
  --chain-id 1337 \
  --keystore ~/.animica/keystore.json \
  --contract <contract-address> \
  --method increment \
  --args '[]'
```

### Query Contract State
```bash
source .venv/bin/activate

# Read contract state
python -m python.animica.cli.chain get-state \
  --address <contract-address> \
  --key <state-key> \
  --rpc-url http://localhost:8545
```

## Mining

### Start CPU Miner (Docker)
```bash
# Miner is included in devnet stack
bash tests/devnet/up.sh

# Check miner logs
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet logs -f miner
```

### Manual Mining
```bash
source .venv/bin/activate

# Mine blocks to a specific address (block rewards credited to this address)
animica miner mine-blocks --address <your-address> --count 10

# Alternative: Use the mining.cli.miner module directly
python -m mining.cli.miner mine-blocks --address <your-address> --count 10 --rpc-url http://localhost:8545

# Start continuous CPU miner (uses default miner address for rewards)
python -m mining.cli.miner start \
  --rpc-url http://localhost:8545 \
  --threads 2 \
  --device cpu

# Check mining stats
python -m mining.cli.stats --rpc http://localhost:8545
```

### Stratum Mining Pool
```bash
source .venv/bin/activate

# Run a Stratum mining pool
animica miner run-pool \
  --rpc-url http://localhost:8545 \
  --db-url sqlite:///pool.db \
  --stratum-bind 0.0.0.0:3333

# Show pool configuration
animica miner show-config
```

**Note:** The setup script (`./setup.sh`) automatically installs all required dependencies for mining, including stratum pool modules and the Omni SDK.

## Development Workflow

### Typical Development Cycle
```bash
# 1. Start devnet
bash tests/devnet/up.sh

# 2. Make code changes
# ... edit files ...

# 3. Run relevant tests
source .venv/bin/activate
pytest <module>/tests/ -v

# 4. Test against devnet
python -m python.animica.cli.node status --rpc-url http://localhost:8545

# 5. Clean up
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down
```

### Hot Reload for RPC Development
```bash
# Start RPC server with auto-reload
source .venv/bin/activate

uvicorn rpc.server:app \
  --host 0.0.0.0 \
  --port 8545 \
  --reload \
  --log-level info
```

### Debug a Failing Test
```bash
source .venv/bin/activate

# Run with verbose output and stop on first failure
pytest <test-file>::<test-name> -vv -x --tb=long

# Run with Python debugger
pytest <test-file>::<test-name> --pdb
```

## Troubleshooting

### "No module named pytest"
```bash
# Ensure you've activated the virtual environment
source .venv/bin/activate

# If still missing, reinstall
pip install pytest
```

### "Port already in use"
```bash
# Each network uses different default ports to avoid conflicts
# Mainnet: 8545, Testnet: 18546, Devnet: 28545, Local-devnet: 38545

# If still conflicting, find and kill process using port
lsof -ti:8545 | xargs kill -9

# Or override port with environment variable
HOST_RPC_PORT=9545 animica node up
```

### "Genesis file not found"
```bash
# Copy appropriate genesis file
bash genesis/devnet.sh

# Or specify path explicitly
python -m core.boot --genesis /path/to/genesis.json
```

### "DB initialization failed"
```bash
# Remove existing DB and reinitialize
rm -f data/animica.db
python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///data/animica.db
```

### Docker Compose Issues
```bash
# Reset everything
docker compose -f tests/devnet/docker-compose.yml -p animica-devnet down -v
bash tests/devnet/cleanup.sh

# Rebuild images
bash tests/devnet/up.sh
```

### Test Collection Errors
```bash
# Some test modules require optional dependencies
# These are automatically skipped by conftest.py

# To see which tests are being skipped:
pytest --collect-only -q | grep SKIPPED
```

## Environment Variables

### RPC Server
- `ANIMICA_RPC_HOST` - Host to bind (default: 0.0.0.0)
- `ANIMICA_RPC_PORT` - Port to bind (default: 8545)
- `ANIMICA_RPC_DB_URI` - Database URI (default: sqlite:///animica.db)
- `ANIMICA_CHAIN_ID` - Chain ID (default: 1337). Empty or invalid values are treated as unset and fall back to network defaults.
- `ANIMICA_LOG_LEVEL` - Log level (default: INFO)
- `ANIMICA_RPC_CORS_ORIGINS` - CORS origins (default: [*])

### CLI Tools
- `ANIMICA_RPC_URL` - Default RPC endpoint
- `ANIMICA_NETWORK` - Network profile (local-devnet, devnet, testnet, mainnet)
- `ANIMICA_CONFIG` - Path to config file

### Mining
- `MINER_DEVICE` - Device to use (cpu, cuda, opencl)
- `MINER_THREADS` - Number of threads for CPU mining
- `MINER_LOG_LEVEL` - Log level

### Testing
- `ANIMICA_TESTALL_NO_LINT` - Skip linting in testall.sh (set to 1)
- `ANIMICA_TEST_SIG_ALG` - Force specific signature algorithm in tests

## Next Steps

After completing this quickstart:

1. **Read the Architecture Docs**: `docs/ARCHITECTURE.md` (when available)
2. **Review Test Suites**: Understand test patterns in `<module>/tests/`
3. **Study Core Modules**: 
   - `consensus/` - PoIES consensus
   - `execution/` - State machine
   - `p2p/` - Networking
   - `rpc/` - JSON-RPC API
4. **Explore Contract Examples**: `contracts/packages/`
5. **Review Genesis Configs**: `genesis/*.json`

## Getting Help

- **Documentation**: Check `<module>/README.md` files
- **Tests**: Look at test files for usage examples
- **Issues**: See `HARDENING_SUMMARY.md` for known issues
- **Debugging**: Enable debug logging with `--log-level DEBUG`

## Troubleshooting

### Mining Issues

**Problem**: `animica miner run-pool` fails with "Stratum pool modules required"
- **Solution**: Run `./setup.sh` to install all dependencies including stratum extras

**Problem**: `animica miner mine-blocks` fails with "RpcClient not available"
- **Solution**: Ensure SDK is installed: `pip install -e sdk/python` (or re-run `./setup.sh`)

**Problem**: Mining fails with "'RpcClient' object has no attribute '_handle_response'"
- **Solution**: Reinstall SDK from the repository: `pip install -e sdk/python --force-reinstall`
- This ensures you have the latest SDK with bug fixes

**Problem**: Miner can't connect to RPC
- **Solution**: Check that the node is running and RPC port is correct (default: 8545)
- Use `--rpc-url` flag to specify the correct endpoint

### Setup Issues

**Problem**: `setup.sh` fails during pnpm install
- **Solution**: Install Node.js 20+ and ensure npm/pnpm is available

**Problem**: Python package install fails
- **Solution**: Ensure Python 3.10+ is installed and venv module is available
- Try: `python3 -m pip install --upgrade pip setuptools wheel`

**Problem**: Tests fail with import errors
- **Solution**: Activate venv first: `source .venv/bin/activate`
- Ensure all packages installed: Re-run `./setup.sh`

## CI/CD Integration

### GitHub Actions Example
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: ./setup.sh
      - run: source .venv/bin/activate && pytest
```

### Pre-commit Hooks
```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

## Performance Tips

1. **Use SQLite WAL mode** for better concurrency
2. **Adjust worker threads** based on CPU cores
3. **Monitor memory usage** with execution state size
4. **Profile slow tests** with `pytest --profile`
5. **Use pytest-xdist** for parallel test execution: `pytest -n auto`

## Security Notes

- **Never commit private keys** to the repository
- **Use environment variables** for sensitive configuration
- **Review .gitignore** before committing
- **Rotate devnet keys** regularly
- **Use proper PQ algorithms** in production (Dilithium3, SPHINCS+)
