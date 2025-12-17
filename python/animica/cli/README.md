"""
python/animica/cli — Unified Animica CLI

This directory contains the comprehensive, unified Animica command-line interface
for blockchain operations. It brings together all essential tools in a single
entry point: `animica`.

Structure
=========

main.py                  Root Typer app and callback for global options
key.py                   Key management (generate, show, list)
wallet.py               Wallet operations (new, import, list, show, export)
node.py                 Node lifecycle (run, status, logs)
tx.py                   Transaction operations (build, sign, send, simulate)
rpc.py                  Raw JSON-RPC method calls
chain.py                Chain queries (head, blocks, txs, accounts, events)
da.py                   Data Availability (submit, get, verify)
mining.py               Mining pool operations (already exists)
config.py               (in parent: python/animica/config.py) Network config
tests/                  Integration tests

Installation & Usage
====================

1. Install from repo root:

    pip install -e python/

2. Run the CLI:

    animica --help

3. Explore subcommands:

    animica node --help
    animica wallet --help
    animica key --help
    animica tx --help
    animica rpc --help
    animica chain --help
    animica da --help
    animica miner --help  (alias for mining pool)
    animica network --help
    animica peer --help

Global Options
==============

--network TEXT              Network profile (local-devnet, devnet, testnet, mainnet)
                            Default: mainnet
                            Env: ANIMICA_NETWORK

--rpc-url TEXT             Override RPC endpoint URL
                            Default: Network-dependent (see below)
                            Env: ANIMICA_RPC_URL
                            Note: Empty/whitespace values fall back to defaults

--chain-id INTEGER         Override chain ID
                            Env: ANIMICA_CHAIN_ID
                            Note: Empty string or invalid values are treated as unset
                            and fall back to network defaults

--config PATH              Path to config file (~/.config/animica/config.toml)
                            Env: ANIMICA_CONFIG

--json                     Output JSON instead of human-readable text

--verbose / -v             Increase verbosity (logging)

Configuration Resolution
=========================

Settings are resolved in this priority order (highest to lowest):
1. Command-line flags (--rpc-url, --chain-id, etc.)
2. Environment variables (ANIMICA_RPC_URL, ANIMICA_CHAIN_ID, etc.)
3. Config file (~/.config/animica/config.toml)
4. Built-in network defaults

Network defaults:
- Mainnet: http://127.0.0.1:8545/rpc (chain ID 1, ports: RPC 8545, P2P 30333, Metrics 9000)
- Testnet: http://127.0.0.1:18546/rpc (chain ID 2, ports: RPC 18546, P2P 31334, Metrics 19000)
- Devnet: http://127.0.0.1:28545/rpc (chain ID 1337, ports: RPC 28545, P2P 31335, Metrics 29000)
- Local-devnet: http://127.0.0.1:38545/rpc (chain ID 1337, ports: RPC 38545, P2P 31336, Metrics 39000)

Important: Empty strings or whitespace-only values for ANIMICA_RPC_URL 
are treated as unset and will fall back to the network defaults. This 
ensures `animica node status` and `animica rpc call` work without 
explicitly setting ANIMICA_RPC_URL.

Example Usage Patterns
======================

Key Management
--------------
  # Generate a new keypair
  animica key new --label "my-key" --output ~/.animica/keys/mykey.json

  # Show key details
  animica key show ~/.animica/keys/mykey.json

  # List all keys
  animica key list --dir ~/.animica/keys

Wallet Operations
-----------------
  # Create a new wallet (stored in ~/.animica/wallets.json by default)
  animica wallet create --label "my-wallet"

  # List all wallets
  animica wallet list

  # Show wallet details (lookup by address, label, or public key hex)
  animica wallet show anim1...              # by address
  animica wallet show my-wallet             # by label
  animica wallet show a1b2c3d4...           # by public key hex

  # Export wallet for backup (lookup by address or label)
  animica wallet export my-wallet --out backup.json
  
  # Set default wallet
  animica wallet set-default my-wallet
  
  # Override wallet store location
  animica wallet --wallet-file /path/to/wallets.json list
  export ANIMICA_WALLETS_FILE=/path/to/wallets.json

Node Management & Network Selection
------------------------------------
  Animica supports multiple networks with automatic Docker Compose configuration
  and non-conflicting default ports to allow running multiple networks simultaneously:
  - mainnet (chain ID 1): RPC 8545, P2P 30333, Metrics 9000
  - testnet (chain ID 2): RPC 18546, P2P 31334, Metrics 19000
  - devnet (chain ID 1337): RPC 28545, P2P 31335, Metrics 29000
  - local-devnet (chain ID 1337): RPC 38545, P2P 31336, Metrics 39000
  
  Each network uses isolated data directories and volumes.
  Ports can be customized via environment variables: HOST_RPC_PORT, HOST_P2P_PORT, HOST_METRICS_PORT

  # Set active network (required before node operations)
  animica network set mainnet     # Production network
  animica network set testnet     # Public test network
  animica network set devnet      # Local development

  # View active network
  animica network get

  # List available networks
  animica network list

  # Start a node (automatically uses active network's compose file)
  animica node up                 # Detached mode (background)
  animica node up --no-detach     # Foreground with logs
  animica node up --with-miner    # Include miner service
  animica node up --no-build      # Skip image rebuild

  # Start ALL networks at once (ignores active network setting)
  # Each network uses its own non-conflicting ports by default
  animica node up-all             # Start mainnet, testnet, devnet, local-devnet
  animica node up-all --with-miner    # Start all networks with miners
  animica node up-all --no-detach     # Run all in foreground (not recommended)
  
  # Override ports globally for all networks (not recommended when using up-all)
  HOST_RPC_PORT=9545 animica node up-all
  
  # Note: up-all starts networks sequentially and reports progress for each.
  # If any network fails, it continues with remaining networks and exits non-zero.
  # Networks with missing compose files are skipped with a warning.

  # Stop a node
  animica node down
  animica node down --volumes     # Also delete blockchain data (DESTRUCTIVE)

  # Check node status (retries indefinitely on RPC errors)
  animica node status
  
  # Check node status with custom retry delay
  animica node status --retry-delay 2.5  # 2.5 seconds between retries
  
  # Increase RPC timeout when the node is under heavy load (defaults to unlimited)
  animica node status --timeout 45
  
  # Set default retry delay via environment variable
  export ANIMICA_RETRY_DELAY=2.0
  animica node status
  
  # Set a longer RPC timeout globally for CLI commands
  export ANIMICA_RPC_TIMEOUT=60
  animica node status

  # View node logs
  # The exact compose file path depends on the active network
  animica network get  # Shows which network is active
  docker compose -f <compose-file> logs -f

Retry Behavior
--------------
  The following commands retry indefinitely on RPC connection/network errors:
  - animica node status: Retries RPC calls until the node is reachable
  - animica miner mine-blocks: Retries mining operations until successful
  
  Configuration:
  - Default retry delay: 1.0 second
  - Default RPC timeout: no timeout (wait indefinitely; configure via --timeout or ANIMICA_RPC_TIMEOUT; use 0/"none" to disable)
  - Configure with --retry-delay flag or ANIMICA_RETRY_DELAY environment variable
  - Configure with --timeout flag or ANIMICA_RPC_TIMEOUT environment variable
  - Each retry is logged with timestamp and error reason
  - Non-retriable errors (e.g., invalid parameters) exit immediately

Studio Services Management (Optional)
--------------------------------------
  # Studio Services provides deploy/verify API and is OPTIONAL
  # Start Studio Services (after node is running)
  animica studio up
  animica studio up --no-detach  # Run in foreground
  
  # Stop Studio Services
  animica studio down
  animica studio down --volumes  # Also delete storage data
  
  # Check Studio Services status
  animica studio status
  
  # View Studio Services logs
  animica studio logs
  animica studio logs --follow

Chain Queries
-------------
  # Current chain head
  animica chain head

  # Get block details
  animica chain block 0
  animica chain block 0x...

  # Get transaction
  animica chain tx 0x...

  # Get account balance
  animica chain account anim1...

  # Query events
  animica chain events --from 0 --to 100 --type "Transfer"

Transactions
------------
  # Build a transaction
  animica tx build --from anim1... --to anim1... --value 1.5 --gas 200000 \
    --output tx.json

  # Sign it
  animica tx sign --file tx.json --key ~/.animica/keys/mykey.json

  # One-shot: build, sign, and send
  animica tx send --from anim1... --to anim1... --value 1 \
    --gas 200000 --key-file ~/.animica/keys/mykey.json

  # Dry-run simulation
  animica tx simulate --file tx.json

JSON-RPC Calls
--------------
  # Direct RPC calls
  animica rpc call chain_getHead
  animica rpc call chain_getBlock '[0]'
  animica rpc call chain_getTx '["0x..."]'
  animica rpc call animica_vm_call '{"to":"anim1...","data":"0x"}'

Data Availability
-----------------
  # Submit a blob
  echo "hello" | animica da submit --namespace 1

  # Retrieve by commitment
  animica da get 0x... --output blob.bin

  # Verify a file matches commitment
  animica da verify 0x... --file blob.bin

Mining Operations
-----------------
  # Mine blocks for testing/development
  # Note: Block rewards go to the configured miner address (see below)
  animica miner mine-blocks --count 5
  animica miner mine-blocks --count 10 --rpc-url http://localhost:8545

  # Configure miner payout address via environment variable
  export ANIMICA_MINER_ADDRESS=anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
  animica miner mine-blocks --count 5
  
  # Specify mining device backend (cpu, cuda, rocm, opencl, metal, auto)
  # Default is 'auto' which automatically detects the best available device
  animica miner mine-blocks --count 5                    # Uses auto-detection
  animica miner mine-blocks --count 5 --device auto      # Explicit auto-detection
  animica miner mine-blocks --count 5 --device cpu       # Force CPU
  animica miner mine-blocks --count 5 --device cuda      # Force CUDA GPU
  
  # Configure device via environment variable
  export ANIMICA_MINER_DEVICE=cuda
  animica miner mine-blocks --count 5
  
  # Auto-detection priority: CUDA > ROCm > OpenCL > Metal > CPU
  # Falls back to CPU if no GPU detected or if detection fails
  
  # If ANIMICA_MINER_ADDRESS is not set, rewards go to:
  # 1. The premine address (for devnet/mainnet)
  # 2. Zero address (fallback)

  # Show pool config
  animica miner show-config

  # Run the pool
  animica miner run-pool --rpc-url http://localhost:8545 \
    --db-url postgresql://... --stratum-bind 0.0.0.0:3334

Network Management
------------------
  # Set the active network
  animica network set mainnet
  animica network set testnet
  animica network set devnet

  # Get the current active network
  animica network get

  # List all available networks
  animica network list

Peer Management
---------------
  # List all connected peers
  # Returns empty list or "No peers connected" if node has no peers
  # Supports multiple RPC method aliases for compatibility
  animica peer list
  animica peer list --verbose
  
  # Fallback to local peer store when RPC is unavailable
  # Default store location: ~/.animica/p2p/peers.json
  animica peer list --store ~/.animica/p2p/peers.json
  export ANIMICA_PEER_STORE=~/.animica/p2p/peers.json

  # Add a peer
  animica peer add /ip4/1.2.3.4/tcp/30303/p2p/QmPeerId...
  animica peer add 1.2.3.4:30303

  # Remove a peer
  animica peer remove QmPeerId...

  # Get detailed peer information
  animica peer info QmPeerId...
  
  # Connect to bootstrap/seed nodes
  # Automatically connects to network-specific seeds (mainnet/testnet/devnet)
  animica peer bootstrap
  animica peer bootstrap --network mainnet  # Explicit network
  animica peer bootstrap --probe            # Test connectivity before adding

  # Important: For mainnet nodes running via `animica node up`:
  # - P2P is enabled by default (ANIMICA_P2P_ENABLE=true)
  # - Bootstrap seeds are automatically loaded based on chain ID
  # - TCP port 30333 and QUIC UDP port 443 must be accessible for peers
  # - The node auto-connects to seeds on startup
  # - Use `animica peer list` to verify peer connections
  # - If no peers connect, ensure firewall allows outbound connections on these ports

  # Note: The peer listing uses the following RPC methods with automatic fallback:
  #   - p2p.listPeers (primary)
  #   - p2p.getPeers
  #   - p2p.peers
  #   - admin_peers (legacy)
  #   - net_peers (legacy)
  # All methods are implemented and return consistent JSON responses.
  #
  # If RPC peer listing is unavailable, the command automatically falls back
  # to reading from the local peer store. The store supports both SQLite
  # (peers.db) and JSON (peers.json) formats. This allows you to see known
  # peers even when the node's RPC doesn't expose peer listing methods.

Sync Management
---------------
  # Check blockchain synchronization status
  # Shows current head height, sync progress, and connected peers
  animica sync status
  
  # View detailed sync information (includes peer list)
  animica sync status --verbose
  
  # Get sync status in JSON format (for scripts/monitoring)
  animica sync status --json
  
  # Force blockchain resynchronization
  # Useful when sync appears stuck or after network issues
  animica sync force
  
  # Force sync with custom timeout (default: 300 seconds)
  animica sync force --timeout 600
  
  # Adjust how often to check progress (default: 5 seconds)
  animica sync force --check-interval 10
  
  # Typical workflow when node isn't syncing:
  #   1. Check sync status: animica sync status
  #   2. If no peers: animica peer bootstrap
  #   3. Force sync: animica sync force
  #   4. Monitor: animica sync status --verbose
  
  # The sync status command displays:
  #   - Current blockchain head (height, hash, chain ID)
  #   - Sync state (SYNCHRONIZED, SYNCING, or IDLE)
  #   - Progress percentage (if actively syncing)
  #   - Connected peer count
  #   - Warnings and recommendations if issues detected
  
  # The force sync command:
  #   - Checks peer connectivity before starting
  #   - Attempts to trigger sync via RPC (tries multiple methods)
  #   - Monitors sync progress in real-time
  #   - Shows blocks synced and sync rate
  #   - Provides helpful diagnostics if sync fails
  
  # Note: The sync commands use the following RPC methods with fallbacks:
  #   Sync status: node.syncStatus, sync.status, chain.syncing, sync.isSyncing
  #   Head info: chain.getHead
  #   Peer list: p2p.listPeers, p2p.getPeers, admin_peers, net_peers
  #   Trigger sync: sync.start, node.startSync, sync.trigger, p2p.sync
  #
  # The commands gracefully handle nodes that don't support all methods
  # and provide clear error messages with troubleshooting hints.

Troubleshooting Sync Issues
----------------------------
  Common sync problems and solutions:
  
  Problem: "No peers connected"
  Solution: 
    animica peer bootstrap           # Connect to seed nodes
    animica peer add <address>       # Add specific peer
  
  Problem: Sync stuck at same height
  Solution:
    animica sync force               # Force resync
    animica peer list --verbose      # Check peer status
  
  Problem: "Could not trigger sync via RPC"
  Solution:
    - Node may sync automatically when peers connect
    - Ensure node is running: animica node status
    - Check node logs for errors
    - Verify RPC endpoint is accessible
  
  Problem: Slow sync speed
  Solution:
    - Add more peers: animica peer bootstrap
    - Check network connectivity
    - Ensure node has sufficient resources
    - Monitor with: animica sync status --verbose

Node Lifecycle Commands (up/down)
----------------------------------
The `node up` and `node down` commands manage local development nodes using
Docker Compose. These commands are particularly useful for:

  - Quickly spinning up a local devnet for testing
  - Managing node lifecycle without manual docker-compose commands
  - Ensuring consistency with network configuration

Prerequisites:
  - Docker and Docker Compose must be installed
  - A network must be configured (use `animica network set <network>`)

Features:
  - Network enforcement: Commands fail with helpful error if network not set
  - Configurable profiles: dev, prod, etc. (default: dev)
  - Volume management: Optionally preserve or delete blockchain data
  - Detached/foreground modes for `up` command
  - Optional image rebuilding
  - Studio Services are decoupled and NOT started by default

Examples:

  # First, set a network (required)
  animica network set devnet

  # Start a node (default: detached, with build)
  # Note: This starts ONLY the node and miner, NOT Studio Services
  animica node up

  # Start in foreground for debugging
  animica node up --no-detach

  # Use a different profile
  animica node up --profile prod

  # Stop the node (preserves data)
  animica node down

  # Stop and remove all data (WARNING: deletes blockchain state!)
  animica node down --volumes

  # Check if node is running
  animica node status

Network Enforcement:
  Both `node up` and `node down` require an active network configuration.
  If no network is set, the commands will exit with a clear error message:

    Error: No network configured. Node lifecycle operations require a network to be set.

    Please set a network first using one of these methods:
      1. Set persistent network: animica network set <network>
      2. Set via environment: export ANIMICA_NETWORK=<network>
      3. Use --network flag: animica --network <network> node up

  This ensures that node operations are always performed in the context
  of a specific network configuration.

Studio Services (Optional)
---------------------------
Studio Services (deploy/verify API, faucet, artifacts storage) is now OPTIONAL
and must be started separately from the node. This decoupling ensures that:

  - `node up` succeeds even if Studio Services fails or is not needed
  - Studio Services can be developed/debugged independently
  - Resource usage is reduced when Studio Services is not required

To use Studio Services:

  1. Start the node first:
     animica node up

  2. Start Studio Services separately:
     animica studio up

  3. Check Studio Services status:
     animica studio status

  4. Stop Studio Services:
     animica studio down

Studio Services requires:
  - A running Animica node
  - RPC_URL configured (can be set via --rpc-url flag)
  - Optional: FAUCET_KEY for faucet functionality (devnet only)

Configuration:
  animica studio config  # Validate configuration before starting

Implementation Status
=====================

✓ Complete:
  - main.py               Full Typer root with all subgroups
  - node.py              status, head, block, tx, up (start), down (stop)
  - wallet.py            new, list, show, export-vault, import
  - key.py               new, show, list
  - rpc.py               call (raw JSON-RPC)
  - chain.py             head, block, tx, account, events
  - da.py                submit, get, verify
  - tx.py                build, simulate (sign, send need wallet integration)
  - mining.py            run-pool, show-config, generate-payout-address
  - network.py           set, get, list (network management)
  - peer.py              list, add, remove, info (peer management)
  - sync.py              status, force (sync management)
  - state.py             Persistent CLI state storage
  - pyproject.toml       Entry point added as `animica` command
  - Tests                Comprehensive tests for node, network, state, peer, sync

Partial (TODO):
  - tx.py                sign, send (require full wallet integration)
  - wallet.py            init (requires encrypted vault setup)

Integration Points
==================

The CLI leverages existing Animica modules:
  - omni_sdk.rpc.http.RpcClient       → JSON-RPC calls
  - omni_sdk.wallet.keystore          → Key encryption/decryption
  - omni_sdk.address                  → Address encoding/validation
  - omni_sdk.da.client                → Data Availability
  - pq.py.keygen, pq.py.signing       → PQ cryptography (Dilithium3)
  - animica.config                    → Configuration management
  - animica.cli.wallet, node, mining  → Existing subcommands

Dependencies
============

Core:
  - typer >= 0.12.3       CLI framework
  - httpx >= 0.27.0       HTTP client for RPC
  - cryptography >= 42.0  AES-GCM encryption
  - omni_sdk             SDK for RPC, wallet, address, DA
  - pq                   PQ cryptography

Optional (for full features):
  - fastapi, uvicorn     Mining pool
  - pytest               Testing

Testing
=======

Run tests:

    cd python/
    pytest animica/cli/tests/ -v

Check CLI structure:

    python -m animica.cli.main --help

Try a command (requires running node):

    export ANIMICA_RPC_URL=http://127.0.0.1:8545
    animica chain head
    animica rpc call chain_getHead

Future Enhancements
===================

1. Add `animica node run` with full orchestration
2. Implement full `animica tx sign` and `send` with wallet integration
3. Add `animica wallet init` with encrypted vault creation
4. Config file support (~/.config/animica/config.toml)
5. Shell completion (bash, zsh, fish)
6. Output formatting options (--format json|yaml|table)
7. Governance operations (animica gov)
8. Staking operations (animica stake)
9. Contract deployment (animica contract deploy)
10. Interactive REPL mode (animica repl)

See Also
========

- python/animica/config.py       Network configuration
- python/animica/wallet/         Wallet implementation
- sdk/python/omni_sdk/           SDK modules (rpc, wallet, address, da)
- contracts/                     Contract deployment and testing
- tests/integration/            Integration tests with devnet
"""

from __future__ import annotations

__all__ = []
