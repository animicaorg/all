# @animica/agent-core

Core library that backs the [`animica-agent`](../cli) CLI and
[`@animica/agent-sdk`](../sdk) SDK.

## Highlights

- `safe-json`: BigInt-safe stringify/parse, hex emit option, key-aware redact
- `config`: layered defaults / env / user / project / animica-node discovery
- `rpc`: minimal RPC client with `chainId`, `blockNumber`, `syncing`, `clientVersion` helpers
- `repo`: read-only tree walk + grep + summary
- `patches`: structured `Patch` model + apply + rollback + sensitive-file guard
- `miner`: env-driven identity, metrics probe, eligibility, resource plan
- `wallet`: identity, balance, ANM formatter/parser, pluggable `Signer`
- `billing`: pricing, budgets, allowances, signed receipts, settlement backends
- `useful-work`: jobs / submissions / rewards / leaderboard / adapters

## Usage

```ts
import { loadConfig, BillingEngine, OfflineSettlement } from "@animica/agent-core";

const { config, paths } = loadConfig();
const engine = new BillingEngine(paths.stateDir, config, undefined, new OfflineSettlement());
const est = engine.authorize({ kind: "code-task", premium: true });
const receipt = await engine.charge({ kind: "code-task", estimate: est, status: "estimated" });
```

## License

Apache-2.0.
