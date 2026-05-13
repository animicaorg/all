# @animica/agent-sdk

Embed the Animica Coding Agent in your own application — IDE plugin, web
dashboard, exchange UI, or downstream service. The SDK is a typed re-export
of [`@animica/agent-core`](../core), so consumers do not need to depend on
the CLI runtime.

```ts
import { loadConfig, probeNode, fetchBalance, BillingEngine, OfflineSettlement } from "@animica/agent-sdk";

const { config, paths } = loadConfig();
const node = await probeNode(config.rpcUrl);
const balance = await fetchBalance(config.rpcUrl, "anm1…");
const engine = new BillingEngine(paths.stateDir, config, undefined, new OfflineSettlement());
```

## License

Apache-2.0.
