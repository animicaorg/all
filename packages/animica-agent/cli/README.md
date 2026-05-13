# animica-agent

The **Animica Coding Agent** — a developer + miner-aware coding assistant for the
Animica network. Repo-aware patches, scaffolding, RPC + wallet ops, miner
identity, useful-work jobs, ANM-denominated billing, and a local browser UI.

## Install

```sh
npm install -g animica-agent
```

Requires Node 18.17+.

## Quickstart

```sh
animica-agent init           # scaffold .animica/agent.json
animica-agent doctor         # health checks
animica-agent status         # network + miner + wallet snapshot
animica-agent code "add a contract that stores a number"
animica-agent diff
animica-agent apply
```

## Commands

| Command | Description |
| --- | --- |
| `init` | Create or refresh project config |
| `doctor` | RPC / miner / wallet diagnostics |
| `status` | Current project, network, wallet, miner snapshot |
| `chat` | Interactive coding session |
| `code "<task>"` | One-shot task, produces a plan + pending patch |
| `diff` | Show the pending patch |
| `apply` | Apply the pending patch (preview first) |
| `rollback` | Restore files from the most recent applied patch |
| `patches` | List applied patches |
| `rpc call <method> [params…]` | JSON-RPC pass-through |
| `wallet connect [addr]` | Configure or inspect wallet identity |
| `balance [addr]` | Read ANM balance via configured RPC |
| `miner connect [addr]` | Connect miner identity (env / config / user) |
| `miner status` | Miner identity, resource plan, eligibility |
| `pricing` | Show current pricing table |
| `budget [show\|set\|reset-session]` | Manage session/daily/monthly caps |
| `estimate <kind>` | Cost estimate for a given action |
| `receipts [list\|export]` | Receipt history |
| `allowance [list\|grant\|revoke]` | Manage spending allowances |
| `jobs list\|accept\|submit` | Useful-work job board |
| `rewards` | Miner rewards |
| `leaderboard` | Miner leaderboard |
| `adapters` | Promoted adapters |
| `contract\|dapp\|token scaffold <name>` | Generate new project |
| `ui [--port 4720]` | Start local browser dashboard |
| `release` | Build + test + npm pack --dry-run |

## Config

Layered (highest precedence wins): function args > env > `<repo>/.animica/agent.json` >
`~/.animica/agent.json` > built-in defaults. If `animica-node` is installed on the same
machine its `~/.animica/node/node.json` is auto-discovered for `rpcUrl` + `chainId`.

Useful env vars:

- `ANIMICA_AGENT_RPC_URL` — override RPC URL
- `ANIMICA_AGENT_PROVIDER` — `offline` (default), `anthropic`, `openai`, `aicf`
- `ANIMICA_AGENT_PROVIDER_BASE_URL` — for AICF/custom endpoints
- `ANIMICA_AGENT_APPROVAL_MODE` — `auto | ask | manual | never`
- `ANIMICA_AGENT_RESOURCE_MODE` — `balanced | miner-priority | agent-priority`
- `ANIMICA_AGENT_CPU_LIMIT` — 1..100 percent
- `ANIMICA_AGENT_CREDITS_MODE` — `off | wallet | miner | aicf | hybrid`

Miner env (read, never written): `ANIMICA_POOL_ADDRESS`, `ANIMICA_MINER_*`,
`ANIMICA_POOL_*`.

## Billing & receipts

All paid actions go through the `BillingEngine`. Estimates honor the pricing
table and miner subsidies; settlement is offline-HMAC by default and can be
upgraded to on-chain settlement via the wallet `Signer` adapter. Every action
emits a signed receipt journalled to `.animica/agent-state/receipts.jsonl`.

```sh
animica-agent pricing
animica-agent budget set --daily 25 --monthly 300 --session 1
animica-agent allowance grant --cap 10 --days 14 --per-task 0.5
animica-agent receipts list
animica-agent receipts export > receipts.jsonl
```

## Miner integration

The agent reads (never writes) miner state from the same env vars the existing
Animica mining runtime uses (`mining/config.py`). The detection covers:

- `ANIMICA_POOL_ADDRESS`, `ANIMICA_POOL_URL`, `ANIMICA_POOL_MODE`
- `ANIMICA_MINER_DEVICE`, `ANIMICA_MINER_STRATUM_*`, `ANIMICA_MINER_METRICS_*`
- Optional project files under `.animica/miner.json` or `.animica/pool.json`

`resourceMode=miner-priority` lowers `cpuLimit` to ≤ 25 % and routes heavy work
to background; `agent-priority` does the opposite. `safeToRunDuringMining` flips
to `false` when an agent task would clash with active mining, so the CLI warns
before launching expensive operations.

## Useful-work jobs

```sh
animica-agent jobs list
animica-agent jobs accept <job-id>
animica-agent jobs submit <job-id> --artifact ./eval.json --metric 0.78
animica-agent rewards
animica-agent leaderboard
animica-agent adapters --model animica-agent
```

By default we use a `LocalCoordinator` rooted at `.animica/agent-state/jobs/`;
set `aicfMode=enabled` and `providerBaseUrl` to point at an HTTP coordinator
(AICF or self-hosted).

## Safety

- No transaction is signed or broadcast without explicit user action.
- The patch engine snapshots files before any write and journals each apply so
  `rollback` is byte-identical.
- The patch engine refuses to edit `.env`, `*.key`, `*.pem` without warning.
- Secrets are redacted from logs by name (`password`, `seed`, `mnemonic`, …).

## License

Apache-2.0.
