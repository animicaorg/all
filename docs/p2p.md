# Animica P2P (tx/block gossip + P2P-first sync)

Animica nodes run a P2P transport alongside the RPC server. The P2P layer is responsible for:

- Transaction relay: `INV -> GETDATA -> TX`
- Block relay: `INV -> GETDATA/GET_BLOCKS -> BLOCKS`
- P2P-first sync: `GET_HEADERS -> HEADERS`, then request missing block bodies from peers

This is designed so a node can join consensus **without relying on any “trusted RPC”** upstream. A remote RPC can still be used as a bootstrap/fallback, but it is optional.

## Quickstart: 3 local nodes (A, B, C)

Each node runs its own RPC server + P2P listener on a different port and uses its own SQLite DB.

### Node A (bootstrap)

PowerShell:

```powershell
$env:ANIMICA_CHAIN_ID="1337"
$env:ANIMICA_RPC_HOST="127.0.0.1"
$env:ANIMICA_RPC_PORT="8545"
$env:ANIMICA_RPC_DB_URI="sqlite:///./nodeA.db"

$env:ANIMICA_P2P_ENABLE="true"
$env:P2P_LISTEN="127.0.0.1:30333"
$env:P2P_SEEDS=""

python -m rpc
```

### Node B (dial A)

```powershell
$env:ANIMICA_CHAIN_ID="1337"
$env:ANIMICA_RPC_HOST="127.0.0.1"
$env:ANIMICA_RPC_PORT="8546"
$env:ANIMICA_RPC_DB_URI="sqlite:///./nodeB.db"

$env:ANIMICA_P2P_ENABLE="true"
$env:P2P_LISTEN="127.0.0.1:30334"
$env:P2P_SEEDS="/ip4/127.0.0.1/tcp/30333"

python -m rpc
```

### Node C (dial B)

```powershell
$env:ANIMICA_CHAIN_ID="1337"
$env:ANIMICA_RPC_HOST="127.0.0.1"
$env:ANIMICA_RPC_PORT="8547"
$env:ANIMICA_RPC_DB_URI="sqlite:///./nodeC.db"

$env:ANIMICA_P2P_ENABLE="true"
$env:P2P_LISTEN="127.0.0.1:30335"
$env:P2P_SEEDS="/ip4/127.0.0.1/tcp/30334"

python -m rpc
```

## Verify peers connected

List peers over JSON-RPC:

```bash
curl -s http://127.0.0.1:8545/rpc -H "content-type: application/json" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"p2p.listPeers\",\"params\":[]}"
```

Repeat for `:8546` and `:8547`.

## Verify tx relay (INV/GETDATA/TX)

1) Submit a tx to Node A via RPC (`tx.sendRawTransaction`). After it is admitted to the pending pool, Node A announces it to peers via `INV`. Peers request the body via `GETDATA`, and Node A responds with `TX`.

2) Confirm it appeared on Node B/C:

```bash
curl -s http://127.0.0.1:8546/rpc -H "content-type: application/json" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tx.getPending\",\"params\":[]}"
```

If your build doesn’t expose `tx.getPending`, use `tx.getTransactionByHash` once you have the tx hash returned by `tx.sendRawTransaction`.

## Verify block relay (INV/GETDATA/BLOCKS) and sync

- When a node imports a new head, it announces the block hash via `INV`.
- Peers request bodies (`GETDATA`/`GET_BLOCKS`) and import them locally.
- A late-joining node requests headers via `GET_HEADERS` and pulls missing blocks from peers.

For local testing you can mine on one node (example):

```bash
curl -s http://127.0.0.1:8547/rpc -H "content-type: application/json" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"miner.mine\",\"params\":[1]}"
```

Then confirm other nodes advanced:

```bash
curl -s http://127.0.0.1:8545/rpc -H "content-type: application/json" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"chain.getHead\",\"params\":[]}"
```

## Notes / knobs

- Disable P2P: set `ANIMICA_P2P_ENABLE=false`
- Force outbound target: set `ANIMICA_P2P_OUTBOUND=8`
- Peer store location: set `ANIMICA_PEER_STORE_PATH` (defaults under `~/.animica/p2p/<network>/`)

