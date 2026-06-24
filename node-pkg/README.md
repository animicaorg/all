# animica-node

**Slim, GPU-free Animica node.** Run a full node and JSON-RPC endpoint — native,
**Bitcoin-Core-compatible**, and **Ethereum-compatible** — with none of the
torch/transformers/CUDA footprint of the full `animica` client.

Built for **exchanges and infrastructure operators** who just need to sync the
chain and serve RPC for deposits, withdrawals, and balance queries.

## What's different from `animica`

| | `pip install animica` | `pip install animica-node` |
|---|---|---|
| Node + JSON-RPC | ✓ | ✓ |
| Bitcoin / Ethereum RPC compat | ✓ | ✓ |
| Wallet, contracts, CLI | ✓ | — (use RPC) |
| Mining, useful-work, Studio | ✓ | — |
| GPU/AI stack (torch, transformers, CUDA) | ✓ (bundled) | **none** |

`animica-node` ships the **same node runtime** as `animica` (same chain,
consensus, p2p, mempool, DA, VM, post-quantum crypto), just with the AI/GPU
dependencies removed. Install one or the other on a box, not both (they share the
same top-level runtime modules).

## Install & run

```bash
pip install animica-node
animica-node            # starts a mainnet node + RPC on 127.0.0.1:8545
```

Or the module form (identical): `python -m rpc`.

## Configure (environment variables)

```bash
ANIMICA_NETWORK=mainnet        # network to join
ANIMICA_DATA_DIR=/data         # data directory
ANIMICA_RPC_HOST=0.0.0.0       # bind host (0.0.0.0 to expose)
ANIMICA_RPC_PORT=8545          # bind port
ANIMICA_P2P_ENABLE=true        # join the p2p network
ANIMICA_LOG_LEVEL=INFO
```

## JSON-RPC endpoints

```
POST /rpc   native:   state.* chain.* tx.* mempool.* p2p.* node.*
POST /      Bitcoin:  getblockchaininfo, getrawtransaction, sendrawtransaction, …
POST /      Ethereum: eth_chainId, eth_blockNumber, eth_getBalance, …  (chain id 149)
```

```bash
# native — balance for an exchange deposit address
curl -s localhost:8545/rpc -d '{"jsonrpc":"2.0","id":1,"method":"state.getBalance","params":["anim1..."]}'
# Ethereum-compatible
curl -s localhost:8545/   -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
# Bitcoin-compatible
curl -s localhost:8545/   -d '{"jsonrpc":"2.0","id":1,"method":"getblockchaininfo","params":[]}'
```

## Docs

- Node & RPC: https://animica.org/docs
- Bitcoin RPC: https://animica.org/docs/bitcoin-rpc
- Ethereum/EVM RPC: https://animica.org/docs/evm-rpc

Apache-2.0 · https://github.com/animicaorg/all
