# animica

Single-command installer for the full Animica toolchain. Installing `animica`
pulls in the node operator, the coding agent, the SDK, the local UI, and the
shared core library, and exposes a unified `animica` CLI that routes to them.

## Install

```sh
npm install -g animica
```

This installs the meta-package and these dependencies:

- [`animica-node`](https://www.npmjs.com/package/animica-node) — full-node operator (`animica-node` binary)
- [`animica-agent`](https://www.npmjs.com/package/animica-agent) — coding agent CLI (`animica-agent` binary)
- [`@animica/agent-core`](https://www.npmjs.com/package/@animica/agent-core) — shared core library
- [`@animica/agent-sdk`](https://www.npmjs.com/package/@animica/agent-sdk) — typed SDK for embedding the agent
- [`@animica/agent-ui`](https://www.npmjs.com/package/@animica/agent-ui) — local browser dashboard

## Usage

The new top-level `animica` command routes to the right underlying CLI:

```sh
animica --help                       # show all commands
animica install-runtime --channel stable
animica node start                   # alias of `animica-node start`
animica agent setup                  # alias of `animica-agent setup`
animica doctor                       # node health checks
animica status                       # node + RPC + sync state
animica wallet address               # agent wallet
animica code "add a /healthz route"  # one-shot coding task
```

Common shortcuts:

| `animica …`             | forwards to             |
|-------------------------|-------------------------|
| `node <subcmd>`         | `animica-node <subcmd>` |
| `agent <subcmd>`        | `animica-agent <subcmd>`|
| `install-runtime …`     | `animica-node install-runtime …` |
| `start` / `stop` / `status` / `doctor` / `sync` / `peers` / `rpc` | `animica-node …` |
| `setup` / `code` / `scaffold` / `wallet` / `miner` / `ui`         | `animica-agent …` |

The underlying binaries are still available directly:

```sh
animica-node --help
animica-agent --help
```

## Quickstart

```sh
npm install -g animica
animica install-runtime --channel stable
animica node start
animica agent setup
```

## License

Apache-2.0
