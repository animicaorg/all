# Animica Stratum Mining Bridge

A simple Stratum V1 mining bridge for Animica that allows you to mine with any Stratum-compatible miner or use the built-in CPU miner.

## Quick Start (3 Commands)

### 1. Start your Animica node

```bash
animica node up
# Wait for node to sync
animica node status
```

### 2. Start the Stratum bridge

```bash
animica stratum up --rpc-url http://127.0.0.1:8545/rpc
```

This will:
- Start a Stratum server on `stratum+tcp://127.0.0.1:3333` (default)
- Connect to your local node RPC
- Poll for block templates
- Accept mining connections

### 3. Mine with the built-in miner

```bash
animica miner stratum --address anim1<your_address> --url stratum+tcp://127.0.0.1:3333 --count 1
```

This will:
- Connect to the Stratum bridge
- Start mining with your CPU
- Submit shares to the bridge
- Stop after 1 block is accepted

## Command Reference

### `animica stratum up`

Start the Stratum mining bridge server.

**Options:**

- `--bind ADDRESS` - Bind address (default: `127.0.0.1` for localhost only)
- `--port PORT` - Server port (default: `3333`)
- `--rpc-url URL` - Node RPC URL (default: from network config)
- `--daemon` - Run in background as daemon
- `--log-file PATH` - Log file path (daemon mode only)
- `--log-level LEVEL` - Logging level: debug, info, warning, error (default: info)
- `--public` - Bind to 0.0.0.0 (requires `--auth-token`)
- `--auth-token TOKEN` - Authentication token for public binding

**Examples:**

```bash
# Start on localhost (default, most secure)
animica stratum up

# Start on custom port
animica stratum up --port 13333

# Start with custom RPC URL
animica stratum up --rpc-url http://localhost:8545/rpc

# Start in daemon mode
animica stratum up --daemon --log-file ~/.animica/stratum.log

# Start on public IP (requires auth token)
animica stratum up --public --auth-token my_secret_token --bind 0.0.0.0
```

### `animica stratum down`

Stop the Stratum mining bridge server.

```bash
animica stratum down
```

### `animica stratum status`

Show Stratum server status.

**Options:**

- `--json` - Output in JSON format

```bash
animica stratum status
animica stratum status --json
```

### `animica miner stratum`

Mine via Stratum protocol connection.

**Options:**

- `--address ADDRESS` - **Required.** Payout address (Bech32 format, e.g., `anim1...`)
- `--url URL` - **Required.** Stratum server URL (e.g., `stratum+tcp://127.0.0.1:3333`)
- `--count N` - Stop after N blocks accepted by node (default: 1)
- `--threads N` - Number of CPU threads (0=auto-detect, default: 0)
- `--difficulty FLOAT` - Initial share difficulty (server may override)
- `--worker NAME` - Worker name/identifier

**Examples:**

```bash
# Mine 1 block to an address
animica miner stratum \
  --address anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz \
  --url stratum+tcp://127.0.0.1:3333 \
  --count 1

# Mine 5 blocks with 4 threads
animica miner stratum \
  --address anim1... \
  --url stratum+tcp://127.0.0.1:3333 \
  --count 5 \
  --threads 4

# Mine with custom worker name and difficulty
animica miner stratum \
  --address anim1... \
  --url stratum+tcp://127.0.0.1:3333 \
  --count 10 \
  --worker rig1 \
  --difficulty 0.05
```

## Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────┐
│  Animica Node   │◄────────┤ Stratum Bridge   │◄────────┤   Miners    │
│                 │  RPC    │                  │ Stratum │             │
│ - Consensus     │         │ - getBlockTemplate│         │ - Built-in  │
│ - State         │         │ - submitBlock    │         │ - cgminer   │
│ - Mempool       │         │ - Job conversion │         │ - bfgminer  │
│ - Mining        │         │ - Share tracking │         │ - custom    │
└─────────────────┘         └──────────────────┘         └─────────────┘
```

The Stratum bridge:
1. Polls `miner.getBlockTemplate` from the node RPC
2. Converts templates to Stratum V1 jobs
3. Broadcasts jobs to connected miners
4. Validates submitted shares
5. Submits full blocks when shares meet network difficulty

## Security

**Default: Localhost Only**

By default, the Stratum server binds to `127.0.0.1` (localhost only) for security. This prevents external connections.

**Public Binding**

To accept external connections, use `--public` with `--auth-token`:

```bash
animica stratum up --public --auth-token your_secret_token --bind 0.0.0.0
```

Miners will need to provide the auth token when connecting (implementation varies by miner).

**Recommendations:**

- Use localhost binding for local mining
- Use a strong, random auth token for public binding
- Use a firewall to restrict access to trusted IPs
- Monitor logs for suspicious activity

## Troubleshooting

### "Stratum server is not running"

Make sure you started the server with `animica stratum up`.

### "Connection refused" when mining

Check that:
1. The Stratum server is running (`animica stratum status`)
2. The URL matches the server's bind address and port
3. Firewall allows the connection (if remote)

### "No mining job received"

Check that:
1. The node RPC is accessible
2. The node is synced (`animica node status`)
3. Mining is enabled on the node

### Mining is slow

The built-in miner is a simple CPU miner for testing. For better performance:
- Increase threads with `--threads N`
- Use a GPU miner (cgminer, bfgminer) via Stratum
- Run multiple miners in parallel

### Blocks are rejected

Check:
- Node logs for rejection reason
- Block difficulty matches current network difficulty
- Mempool transactions are valid

## Compatibility

**Tested with:**
- Built-in `animica miner stratum` command ✓
- cgminer (with custom Animica support) ⚠️ *experimental*
- bfgminer (with custom Animica support) ⚠️ *experimental*

**Protocol:**
- Stratum V1 (mining.subscribe, mining.authorize, mining.notify, mining.submit)
- Custom Animica job encoding (not Bitcoin-compatible)

## Development

### Adding a New Mining Backend

To add support for GPU or other backends:

1. Implement a proper hash search in `mining/hash_search.py`
2. Update the miner client to use the backend
3. Add backend selection flags to `animica miner stratum`

### Extending the Bridge

The bridge is modular:
- `stratum_bridge.py` - RPC polling and job management
- `stratum_server.py` - Stratum protocol server
- `stratum_client.py` - Client for testing
- `stratum_protocol.py` - Protocol encoding/decoding

## Environment Variables

- `ANIMICA_RPC_URL` - Default node RPC URL
- `ANIMICA_STRATUM_BIND` - Default bind address
- `ANIMICA_STRATUM_PORT` - Default server port
- `ANIMICA_MINER_THREADS` - Default thread count for miner

## License

See LICENSE.txt in repository root.
