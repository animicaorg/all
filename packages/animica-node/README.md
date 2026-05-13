# animica-node

The **Animica full-node operator CLI**, distributed via npm. It is a thin,
production-ready control plane around the existing Animica node runtime that
already ships with this repo (`python/animica/cli/`). The CLI never
re-implements node behavior — it spawns the same binary you would run by
hand and owns the daemon lifecycle on top.

## Install

```sh
npm install -g animica-node
# or
npx animica-node start
```

## Quickstart

```sh
animica-node init            # writes ~/.animica/node/node.json
animica-node doctor          # backend + RPC + miner safety checks
animica-node start           # detached daemon; logs in ~/.animica/node/
animica-node status          # pid, RPC, sync, config snapshot
animica-node rpc call animica_chainId
animica-node stop
```

## Backend resolution

In order:

1. `$ANIMICA_NODE_BIN` (operator override)
2. an installed `animica` shell command
3. `<repoRoot>/.venv/bin/python -m animica.cli.main`
4. `python3 -m animica.cli.main` (last resort)

## Commands

| Command | Description |
| --- | --- |
| `init` | Generate / refresh `node.json` |
| `start [-f]` | Start the node (detached by default, `-f` for foreground) |
| `stop` | Stop the running node |
| `restart` | Stop + start |
| `status` | Pidfile, RPC, sync state, config snapshot |
| `logs [-f]` | Tail node logs |
| `doctor` | RPC, backend, miner-safety checks |
| `reset --yes` | Wipe the configured data dir |
| `rpc call <method> [params…]` | JSON-RPC pass-through |
| `sync status` | One-liner sync state |
| `peers` | List connected peers |
| `config show` / `config set k=v` | Read / mutate `node.json` |
| `wallet path` | Print the wallet dir used by the node |
| `miner status` | Miner-aware hints for resourceMode |
| `balance [addr]` | Suggested agent flow for balance lookup |
| `discovery` | JSON used by `animica-agent` to align RPC |

## Config

`~/.animica/node/node.json` (override via `ANIMICA_NODE_CONFIG`):

```json
{
  "network": "local-devnet",
  "chainId": 1,
  "rpcPort": 8545,
  "p2pPort": 30303,
  "metricsPort": 9106,
  "dataDir": "~/.animica/node/data",
  "logLevel": "info",
  "minerSafeMode": false,
  "resourceMode": "balanced"
}
```

`resourceMode=miner-priority` propagates to the node runtime as
`ANIMICA_NODE_RESOURCE_MODE=miner-priority` so operators running a hot miner
on the same machine can opt the node into a calmer scheduling profile.

## Integration with the Coding Agent

`animica-agent` automatically discovers this node's config and uses
`http://127.0.0.1:<rpcPort>/rpc` for `doctor`, `status`, `balance`, `rpc call`,
and useful-work submissions. You never have to configure the URL twice.

## License

Apache-2.0.
