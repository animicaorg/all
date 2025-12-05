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

Global Options
==============

--network TEXT              Network profile (local-devnet, devnet, testnet, mainnet)
                            Default: devnet
                            Env: ANIMICA_NETWORK

--rpc-url TEXT             Override RPC endpoint URL
                            Default: http://127.0.0.1:8545/rpc
                            Env: ANIMICA_RPC_URL

--chain-id INTEGER         Override chain ID
                            Env: ANIMICA_CHAIN_ID

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
4. Built-in defaults (devnet on http://127.0.0.1:8545/rpc)

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
  # Create a new wallet
  animica wallet new --label "my-wallet"

  # List all wallets
  animica wallet list

  # Show wallet details
  animica wallet show anim1...

  # Export for backup
  animica wallet export-vault > wallet-backup.json

Node Management
---------------
  # Check node status
  animica node status

  # Show logs
  animica node logs --tail 100

  # Run a node
  animica node run --config config.toml

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

Mining Pool
-----------
  # Show pool config
  animica miner show-config

  # Run the pool
  animica miner run-pool --rpc-url http://localhost:8545 \
    --db-url postgresql://... --stratum-bind 0.0.0.0:3334

Implementation Status
=====================

✓ Complete:
  - main.py               Full Typer root with all subgroups
  - node.py              status, logs (run planned)
  - wallet.py            new, list, show, export-vault, import
  - key.py               new, show, list
  - rpc.py               call (raw JSON-RPC)
  - chain.py             head, block, tx, account, events
  - da.py                submit, get, verify
  - tx.py                build, simulate (sign, send need wallet integration)
  - mining.py            run-pool, show-config, generate-payout-address
  - pyproject.toml       Entry point added as `animica` command
  - Tests                Basic CLI structure tests

Partial (TODO):
  - tx.py                sign, send (require full wallet integration)
  - node.py              run (requires node orchestration)
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
