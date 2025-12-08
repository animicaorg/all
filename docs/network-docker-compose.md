# Network-Specific Docker Compose Configuration

## Overview

Animica provides first-class Docker Compose configurations for mainnet, testnet, and devnet, with automatic network selection through the CLI. Each network uses isolated data directories and volumes to prevent cross-network contamination of blockchain data.

## Architecture

### Network Configurations

| Network | Chain ID | RPC Port | Compose File | Data Directory |
|---------|----------|----------|--------------|----------------|
| **mainnet** | 1 | 8545 | `ops/docker/docker-compose.mainnet.yml` | `~/.animica/chain-1/` |
| **testnet** | 2 | 8546 | `ops/docker/docker-compose.testnet.yml` | `~/.animica/chain-2/` |
| **devnet** | 1337 | 8545 | `tests/devnet/docker-compose.yml` | `~/.animica/chain-1337/` |
| **local-devnet** | 1337 | 8545 | `tests/devnet/docker-compose.yml` | `~/.animica/chain-1337/` |

### Volume Isolation

Each network uses network-specific Docker volumes to ensure complete data isolation:

- **Mainnet volumes**: `mainnet_node_data`, `mainnet_services_data`
- **Testnet volumes**: `testnet_node_data`, `testnet_services_data`
- **Devnet volumes**: `devnet_node1_data`, `devnet_node2_data`, `devnet_services_data`

This isolation ensures:
1. No accidental mixing of blockchain data between networks
2. Safe parallel operation of multiple networks (on different ports)
3. Clean network switching without data contamination

## Usage

### Quick Start

```bash
# Set the active network
animica network set mainnet

# Start the node (automatically uses mainnet compose file)
animica node up

# Check node status
animica node status

# Stop the node
animica node down
```

### Network Selection

The active network is determined by the following priority (highest to lowest):

1. **Command-line flag**: `animica --network <network> node up`
2. **Environment variable**: `export ANIMICA_NETWORK=<network>`
3. **Persisted setting**: `animica network set <network>`
4. **Default**: `mainnet`

### Network Management

#### Set Active Network

```bash
# Set to mainnet (production)
animica network set mainnet

# Set to testnet (public test network)
animica network set testnet

# Set to devnet (local development)
animica network set devnet
```

#### View Active Network

```bash
animica network get
```

#### List Available Networks

```bash
animica network list
```

### Node Operations

#### Starting a Node

The `animica node up` command automatically:
1. Detects the active network
2. Selects the appropriate Docker Compose file
3. Uses network-specific configuration (chain ID, ports, volumes)
4. Starts the node with isolated data storage

**Examples:**

```bash
# Start mainnet node (default production network)
animica network set mainnet
animica node up

# Start testnet node (for testing with public testnet)
animica network set testnet
animica node up

# Start devnet node (local development with 2 nodes + miner)
animica network set devnet
animica node up

# Start mainnet node with miner (for mining operations)
animica network set mainnet
animica node up --with-miner

# Start node in foreground (for debugging)
animica node up --no-detach

# Start node without rebuilding images
animica node up --no-build
```

#### Stopping a Node

```bash
# Stop the node (preserves data)
animica node down

# Stop the node and remove all volumes (WARNING: deletes blockchain data)
animica node down --volumes
```

**Note:** The `--volumes` flag will delete ALL blockchain data for the active network. This cannot be undone.

### Studio Services (Optional)

Studio Services provides:
- Contract deployment API
- Contract verification
- Artifact storage
- Faucet (testnet/devnet only)
- Explorer web UI

**Starting Studio Services:**

```bash
# Start Studio Services (also starts node if not running)
animica studio up

# Check Studio Services status
animica studio status

# View Studio Services logs
animica studio logs

# Stop Studio Services (node keeps running)
animica studio down
```

Studio Services automatically:
- Uses the same network as the node
- Connects to the correct RPC endpoint
- Uses network-isolated storage
- Enables faucet on testnet/devnet (if FAUCET_KEY is set)

### Network Switching

When you switch networks using `animica network set`, subsequent node operations automatically use the new network's configuration.

**Example workflow:**

```bash
# Start on testnet
animica network set testnet
animica node up
# ... work on testnet ...
animica node down

# Switch to mainnet
animica network set mainnet
animica node up
# ... work on mainnet ...
animica node down
```

**Important:** 
- Switching networks while a node is running requires stopping and restarting the node.
- Each network maintains its own isolated blockchain data.
- You cannot have multiple networks running on the same ports simultaneously.

## Configuration

### Environment Variables

Each network supports environment variable overrides:

```bash
# Override RPC URL
export ANIMICA_RPC_URL="http://custom-host:8545/rpc"

# Override chain ID
export ANIMICA_CHAIN_ID=999

# Override specific network setting
export ANIMICA_NETWORK=testnet

# Override genesis file path
export GENESIS_PATH=/path/to/genesis.json
```

### Per-Network Defaults

#### Mainnet

```yaml
chain_id: 1
rpc_url: http://127.0.0.1:8545/rpc
rpc_port: 8545
genesis_path: core/genesis/genesis.mainnet.json
data_dir: ~/.animica/chain-1
db_name: mainnet.db
p2p_port: 30333
```

**Features:**
- Production-ready configuration
- Strict CORS policy (localhost only by default)
- Faucet disabled
- Optimized for stability

#### Testnet

```yaml
chain_id: 2
rpc_url: http://127.0.0.1:8546/rpc
rpc_port: 8546
genesis_path: core/genesis/genesis.testnet.json
data_dir: ~/.animica/chain-2
db_name: testnet.db
p2p_port: 30334
```

**Features:**
- Public test network
- Open CORS policy
- Faucet enabled (requires FAUCET_KEY)
- Suitable for integration testing

#### Devnet

```yaml
chain_id: 1337
rpc_url: http://127.0.0.1:8545/rpc
rpc_port: 8545
genesis_path: core/genesis/genesis.json
data_dir: ~/.animica/chain-1337
db_name: devnet.db
```

**Features:**
- Local development environment
- 2 nodes + miner by default
- Open CORS policy
- Fast block times
- Premine accounts available
- Faucet enabled (if FAUCET_KEY set)

## Docker Compose Profiles

### Devnet Profiles

Devnet uses Docker Compose profiles for flexible service management:

- **dev** (default): Node(s) + Miner
- **studio**: Studio Services + Explorer Web UI

```bash
# Start just the node and miner
animica node up

# Start node, miner, and studio services
animica studio up
```

### Mainnet/Testnet Profiles

Mainnet and testnet have simpler configurations:

- **default**: Node only
- **miner**: Add miner service
- **studio**: Studio Services + Explorer Web UI

```bash
# Start mainnet node only
animica node up

# Start mainnet node with miner
animica node up --with-miner

# Start studio services
animica studio up
```

## Data Management

### Viewing Data

```bash
# List Docker volumes for current network
docker volume ls | grep animica

# Inspect a volume
docker volume inspect mainnet_node_data

# Check database location
# Mainnet: ~/.animica/chain-1/mainnet.db
# Testnet: ~/.animica/chain-2/testnet.db
# Devnet: ~/.animica/chain-1337/devnet.db
```

### Backing Up Data

```bash
# Backup mainnet node data
docker run --rm -v mainnet_node_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/mainnet-backup.tar.gz /data

# Restore mainnet node data
docker run --rm -v mainnet_node_data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/mainnet-backup.tar.gz -C /
```

### Cleaning Up

```bash
# Remove all volumes for active network (DESTRUCTIVE)
animica node down --volumes

# Remove all Animica Docker volumes (VERY DESTRUCTIVE)
docker volume rm $(docker volume ls -q | grep animica)
```

## Troubleshooting

### Network Not Set

**Error:** `No network configured. Node lifecycle operations require a network to be set.`

**Solution:**
```bash
animica network set mainnet  # or testnet, devnet
```

### Port Already in Use

**Error:** `Error starting userland proxy: listen tcp4 0.0.0.0:8545: bind: address already in use`

**Solution:**
```bash
# Check what's using the port
lsof -i :8545

# Stop the conflicting service or use a different network
# (testnet uses port 8546, so no conflict)
animica network set testnet
animica node up
```

### Compose File Not Found

**Error:** `Docker Compose file not found at /path/to/compose.yml`

**Solution:**
- Ensure you're in the repository root
- Verify the compose files exist:
  - `ops/docker/docker-compose.mainnet.yml`
  - `ops/docker/docker-compose.testnet.yml`
  - `tests/devnet/docker-compose.yml`

### Wrong Network Data

If you accidentally used the wrong network:

```bash
# Stop the node
animica node down --volumes  # Remove wrong data

# Switch to correct network
animica network set <correct-network>

# Start fresh
animica node up
```

## Advanced Usage

### Running Multiple Networks

You can run multiple networks simultaneously on different ports:

```bash
# Terminal 1: Start mainnet (port 8545)
animica network set mainnet
animica node up

# Terminal 2: Start testnet (port 8546)
animica network set testnet
animica node up
```

### Custom Genesis Files

```bash
# Set custom genesis path
export GENESIS_PATH=/path/to/custom-genesis.json
animica node up
```

### Development Workflow

```bash
# Development on devnet
animica network set devnet
animica node up --no-detach  # Run in foreground for logs

# In another terminal: Deploy contracts
animica studio up
# ... development work ...

# Clean restart
animica node down --volumes
animica node up
```

## See Also

- [CLI Commands Documentation](./cli-commands.md)
- [Network Management](../python/animica/cli/README.md)
- [Docker Compose Files](../ops/docker/)
- [Devnet Setup](../tests/devnet/README.md)
