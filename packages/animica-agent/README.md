# Animica Coding Agent (npm packages)

A production-ready, npm-installable coding agent for the Animica network,
plus a companion full-node operator CLI. Built additively on top of the
existing monorepo — no existing wallet, node, studio, explorer, CEX,
extension, AICF, or CLI behavior is modified.

## Packages

| Package | Path | Purpose |
| --- | --- | --- |
| [`animica-agent`](./cli) | `packages/animica-agent/cli` | npm-installable CLI bin |
| [`@animica/agent-core`](./core) | `packages/animica-agent/core` | Engine: config, rpc, patches, miner, billing, useful-work |
| [`@animica/agent-sdk`](./sdk) | `packages/animica-agent/sdk` | Typed SDK for embedding |
| [`@animica/agent-ui`](./ui) | `packages/animica-agent/ui` | Local browser dashboard |
| [`animica-node`](../animica-node) | `packages/animica-node` | npm-installable full-node operator |

## Build everything

```sh
pnpm --filter "@animica/agent-core" build
pnpm --filter "@animica/agent-sdk"  build
pnpm --filter "@animica/agent-ui"   build
pnpm --filter "animica-agent"       build
pnpm --filter "animica-node"        build
```

## Run all tests

```sh
pnpm --filter "@animica/agent-core" test
pnpm --filter "animica-agent"       test
pnpm --filter "animica-node"        test
```

## Install locally

```sh
cd packages/animica-agent/cli && npm install -g .
cd ../../animica-node && npm install -g .
animica-agent doctor
animica-node status
```

## Five-minute user flow

For a new user who just wants the coding-agent prompt screen:

```sh
# 1. Install (once published to npm)
npm install -g animica-node animica-agent

# 2. Start a local node (delegates to the bundled Python runtime)
animica-node init
animica-node start

# 3. Provision a wallet
animica-agent init                         # writes .animica/agent.json
animica-agent wallet create main           # creates a wallet via Python CLI
animica-agent wallet address               # prints your address
animica-agent wallet fund-help             # funding instructions + balance check

# 4. Send ANM to that address (1 ANM is plenty for first-time use)

# 5. Launch the dashboard
animica-agent                              # opens UI in your default browser
# or
animica-agent chat                         # TTY-only fallback
```

Or run the single guided command:

```sh
animica-agent setup                        # prereqs → node → wallet → launch
```

`animica-agent` with no subcommand opens the dashboard. It auto-detects a
graphical environment and falls back to TTY chat headlessly (CI, SSH, etc).
Progress for `setup` is persisted to `<stateDir>/setup-state.json` and
resumed automatically.

### Common follow-up commands

```sh
animica-agent setup status                 # where we are in the wizard
animica-agent node status                  # is the node reachable?
animica-agent balance                      # check funds
animica-agent mine start                   # start useful-work mining
animica-agent useful-work readiness        # aggregate go/no-go
animica-agent useful-work go-live          # strict pre-live-payout gate
animica-agent open                         # reopen the dashboard tab
```

### Cross-platform notes

- **macOS / Linux / Windows**: `animica-agent` opens the dashboard via
  `open` / `xdg-open` / `start` respectively. On headless servers it falls
  back to TTY chat. Set `BROWSER=...` to override on Linux.
- **JSON mode**: every operator command supports `--json` for scripting.
  Output is BigInt-safe (`bigint` is encoded as `{"__bn":"…"}`).
- **No silent live spending**: `submit-live` always requires the explicit
  `--i-understand-this-spends-real-funds` flag; no other code path can
  trigger a real on-chain transfer.

## Why this exists

Animica miners and developers should share a single coding agent that:

1. Understands Animica RPC, addresses, chain id, and useful-work pipelines.
2. Charges in ANM via a wallet-linked billing engine with hard caps and
   signed receipts (offline by default, on-chain via your wallet's Signer).
3. Respects miner identity: it never disrupts mining, can run in a
   miner-priority resource mode, and offers credits/subsidy hooks.
4. Is verifiable end-to-end: the patch engine produces structured diffs and
   keeps a journal so every change can be rolled back to byte-identical state.

See each package's README for command references and configuration details.
