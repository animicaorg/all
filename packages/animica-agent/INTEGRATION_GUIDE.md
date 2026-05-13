# Animica Coding Agent — integration guide

This is the integration guide for embedding or operating the Animica Coding
Agent on top of existing Animica infrastructure (node, wallet, miner, AICF).

## Mental model

The agent runs locally. Every paid action goes through a `BillingEngine`
that:

- estimates cost (per-action + per-1k-token tiers, optional miner subsidy)
- enforces session/daily/monthly budgets and allowance caps
- emits a signed receipt and journals it to disk

Settlement is pluggable:

- `OfflineSettlement` (default): HMAC-signs the receipt for local accounting
- `NodeSettlement`: routes through a `Signer` (interactive prompt or wallet
  extension) so the user can opt into on-chain settlement when ready

## Developer workflow

```sh
animica-agent init --interactive
animica-agent doctor
animica-agent code "add a contract that stores my account label"
animica-agent diff
animica-agent apply
animica-agent rollback     # if you change your mind
```

## Miner workflow

```sh
animica-agent miner connect <payoutAddress>
animica-agent miner status
# resourceMode auto-detects from ANIMICA_MINER_* env vars; override per-run:
animica-agent --resource-mode miner-priority status
animica-agent jobs list
animica-agent jobs submit <id> --artifact ./eval.json --metric 0.78
animica-agent rewards
```

The miner adapter is read-only against the existing `mining/config.py`
contract:

- env vars are consumed but never set
- `.animica/miner.json` / `.animica/pool.json` are optional supplemental sources
- the metrics endpoint at `http://127.0.0.1:9106/metrics` is probed with a
  1.5 s timeout so missing miners never delay interactive CLI commands

## Local node workflow

```sh
animica-node init
animica-node start          # detached daemon
animica-node doctor
animica-node logs -f
animica-node rpc call animica_chainId
animica-node stop
```

`animica-agent` automatically discovers the node config from
`~/.animica/node/node.json` (override via `ANIMICA_NODE_CONFIG`) so RPC URL
and chain id stay in sync without duplicate configuration.

## Useful-work jobs

The agent ships a `LocalCoordinator` and an `HttpCoordinator`. By default
jobs live under `.animica/agent-state/jobs/`. To point at AICF or your own
coordinator:

```sh
animica-agent --provider aicf init --force
# or
export ANIMICA_AGENT_PROVIDER_BASE_URL=https://aicf.example/api
animica-agent jobs list
```

A `LocalCoordinator` accepts well-formed submissions, scores them with a
deterministic baseline quality function, and emits proportional rewards
capped by the job's `rewardCapRaw`. Production verification (redundant
execution, validator committees, slashing) is intentionally pushed
upstream — the agent ships only the artifact and receipt.

## BigInt safety

All public surfaces (CLI, UI API, SDK exports) route through
`safe-json.ts`. The serializer emits hex on the wire (Animica RPC
convention) and wraps bigints as `{ "__bn": "<decimal>" }` for persistence.
`safeParse` recovers them losslessly. No callers should `JSON.stringify` a
payload that may contain bigint — always go through `safeStringify`.

## Regression safety

We do not edit any existing module. Files added are confined to:

```
packages/animica-agent/{core,cli,sdk,ui}
packages/animica-node
.github/workflows/animica-agent-and-node.yml
```

Two tiny additive edits are made to manifests:

- `package.json` workspaces: add `packages/animica-agent/*`, `packages/animica-node`
- `pnpm-workspace.yaml`: same

Existing wallet, miner, pool, explorer, studio, cex, extension, AICF, and
Python CLI behavior is unchanged.
