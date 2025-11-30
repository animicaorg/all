# Animica RPC Quickstart

The devnet profile exposes a JSON-RPC server on **0.0.0.0:8545** at the `/rpc` path.

1. Start the node + RPC server (devnet is the default):

```bash
ops/run.sh node
# or explicitly
ops/run.sh --profile devnet node
```

2. Issue JSON-RPC calls over HTTP POST to the base URL `http://127.0.0.1:8545/rpc`.

Examples:

```bash
# Discover the OpenRPC schema
curl -X POST http://127.0.0.1:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"rpc.discover","params":[]}'

# Fetch the active chainId (also available via eth_chainId)
curl -X POST http://127.0.0.1:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getChainId","params":[]}'

# Get the latest block header (alias: eth_getBlockByNumber with "latest")
curl -X POST http://127.0.0.1:8545/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"blk","method":"chain.getBlockByNumber","params":["latest",false,false]}'
```

The server rejects non-POST requests to `/rpc` with a helpful hint; always send JSON bodies with `Content-Type: application/json`.
