# E2E Test Simulators

This directory contains production-quality simulator modules for end-to-end testing of the centralized exchange.

## Directory Structure

```
src/sim/
├── mm/                      # Market Maker Simulators
│   ├── inventory.ts         # Inventory tracking and position management
│   ├── quoting.ts          # Bid/ask quote generation
│   ├── strategies.ts       # Trading strategies (tight, volatile, skewed)
│   ├── risk.ts             # Risk management and limits
│   └── maker.ts            # Core market maker orchestrator
│
├── deposits/                # Deposit Simulators
│   ├── bitgo_mock.ts       # Mock BitGo webhooks
│   ├── bitgo_sandbox.ts    # Real BitGo sandbox integration
│   ├── animica_devnet.ts   # Animica devnet deposits
│   └── animica_reorg.ts    # Chain reorg simulation
│
├── withdrawals/             # Withdrawal Simulators
│   ├── bitgo_mock.ts       # Mock BitGo withdrawals
│   ├── bitgo_sandbox.ts    # Real BitGo sandbox withdrawals
│   └── animica_devnet.ts   # Animica devnet withdrawals
│
├── chaos/                   # Chaos Testing
│   ├── docker.ts           # Docker container chaos (kill, pause, restart)
│   ├── toxiproxy.ts        # Network fault injection (latency, packet loss)
│   └── faults.ts           # Fault orchestration and scenarios
│
└── reconcile/               # Reconciliation
    ├── ledger_snapshot.ts   # Ledger state snapshots
    ├── event_hashchain.ts   # Cryptographic event chain
    ├── invariants.ts        # Invariant checks (double-entry, solvency, etc)
    └── proof_bundle.ts      # Audit proof generation

```

## Market Maker Simulators

### Overview
Simulates automated market makers with realistic trading behavior, inventory management, and risk controls.

### Components

**inventory.ts** - Tracks base/quote balances, calculates position limits and inventory skew
```typescript
import { InventoryManager } from './sim/mm/inventory.js';

const inventory = new InventoryManager({
  initialBase: 10,
  initialQuote: 100000,
  maxBasePosition: 20,
  maxQuoteExposure: 200000,
  targetRatio: 0.5,
});

const snapshot = inventory.getSnapshot(50000); // midPrice
console.log(`Skew: ${snapshot.skew}`); // -1 to 1
```

**quoting.ts** - Generates bid/ask prices with spread and inventory adjustments
```typescript
import { generateQuoteLadder } from './sim/mm/quoting.js';

const ladder = generateQuoteLadder({
  midPrice: 50000,
  levels: 5,
  baseSpreadBps: 10, // 0.1%
  levelSpreadIncrement: 5,
  inventorySkew: 0.2,
  skewSensitivity: 0.5,
  baseSize: 1.0,
  sizeDecrement: 0.8,
  tickSize: 0.01,
});
```

**strategies.ts** - Three built-in strategies
- `TightSpreadStrategy`: Aggressive quoting for stable markets
- `VolatileStrategy`: Wider spreads during volatility
- `InventorySkewedStrategy`: Adjusts quotes to rebalance inventory

```typescript
import { createStrategy } from './sim/mm/strategies.js';

const strategy = createStrategy('volatile', params, {
  baseSpreadBps: 20,
  levels: 5,
});

const decision = strategy.generateQuotes(orderbook);
```

**risk.ts** - Position limits and risk checks
```typescript
import { RiskManager } from './sim/mm/risk.js';

const riskCheck = riskManager.checkRisk({
  midPrice: 50000,
  bestBid: 49995,
  bestAsk: 50005,
});

if (!riskCheck.passed) {
  console.log(`Risk breach: ${riskCheck.reason}, action: ${riskCheck.action}`);
}
```

**maker.ts** - Core market maker with seeded RNG for determinism
```typescript
import { MarketMaker } from './sim/mm/maker.js';

const mm = new MarketMaker(config, httpClient, wsClient);
await mm.start();

// Get stats
const stats = mm.getStats();
console.log(`Trades: ${stats.trades}, Orders: ${stats.ordersPlaced}`);

await mm.stop();
```

## Deposit Simulators

### BitGo Mock
Simulates BitGo webhook callbacks without blockchain transactions.

```typescript
import { simulateDeposit } from './sim/deposits/bitgo_mock.js';

await simulateDeposit(config, {
  address: 'bc1q...',
  amount: '100000000', // satoshis
  maxConfirmations: 6,
  confirmationDelay: 1000,
});
```

### BitGo Sandbox
Integrates with real BitGo sandbox (when credentials available).

```typescript
import { BitGoSandbox, createSandboxDeposit } from './sim/deposits/bitgo_sandbox.js';

const sandbox = new BitGoSandbox(config);
const { address, transfer } = await createSandboxDeposit(sandbox, {
  amount: '100000000',
  minConfirmations: 2,
});
```

### Animica Devnet
Simulates deposits on Animica blockchain.

```typescript
import { AnimicaDevnetClient, simulateAnimicaDeposit } from './sim/deposits/animica_devnet.js';

const client = new AnimicaDevnetClient(config);
const { txHash, receipt } = await simulateAnimicaDeposit(client, {
  depositAddress: '0x...',
  amount: '1000000',
  faucetAddress: '0x...',
  minConfirmations: 3,
});
```

### Reorg Simulation
Tests deposit safety during blockchain reorganizations.

```typescript
import { ReorgSimulator, testDepositReorgSafety } from './sim/deposits/animica_reorg.js';

const simulator = new ReorgSimulator(config);
const result = await testDepositReorgSafety(simulator, {
  depositAddress: '0x...',
  depositAmount: '1000000',
  faucetAddress: '0x...',
  expectedBehavior: 'should_survive',
});
```

## Withdrawal Simulators

Similar structure to deposits, with mock, sandbox, and devnet implementations.

```typescript
import { simulateBitGoWithdrawal } from './sim/withdrawals/bitgo_mock.js';

const result = await simulateBitGoWithdrawal(params, {
  webhookUrl: 'http://localhost:3000/webhooks/withdrawal',
  signingDelay: 1000,
  broadcastDelay: 2000,
  confirmationDelay: 3000,
  maxConfirmations: 6,
});
```

## Chaos Testing

### Docker Chaos
Control Docker containers to simulate crashes and resource issues.

```typescript
import { DockerChaos, ContainerChaosScenarios } from './sim/chaos/docker.js';

const chaos = new DockerChaos();
const scenarios = new ContainerChaosScenarios(chaos);

// Kill and restart a service
await scenarios.killAndRestart('cex-api', 5000);

// Pause container (freeze processes)
await scenarios.pauseAndUnpause('cex-matching', 10000);

// Rolling restart
await scenarios.rollingRestart(['cex-api', 'cex-matching'], 5000);
```

### Toxiproxy Network Faults
Inject network faults without changing application code.

```typescript
import { ToxiproxyClient, NetworkFaults } from './sim/chaos/toxiproxy.js';

const client = new ToxiproxyClient({ apiUrl: 'http://localhost:8474' });
const faults = new NetworkFaults(client);

// Add latency
await faults.addLatency('api-proxy', 500, 100); // 500ms ± 100ms

// Add packet loss
await faults.addPacketLoss('api-proxy', 10); // 10%

// Cut connection
await faults.cutConnection('db-proxy');

// Restore
await faults.restoreConnection('db-proxy');
```

### Fault Orchestration
Run predefined chaos scenarios.

```typescript
import { ChaosTester } from './sim/chaos/faults.js';

const tester = new ChaosTester({ docker, toxiproxy });
const scenarios = tester.createScenarios();

for (const scenario of scenarios) {
  await tester.runScenario(scenario);
  // Test exchange behavior during fault
}

await tester.cleanupAll();
```

## Reconciliation

### Ledger Snapshot
Capture point-in-time ledger state.

```typescript
import { takeLedgerSnapshot, compareSnapshots } from './sim/reconcile/ledger_snapshot.js';

const snapshot = await takeLedgerSnapshot(adminClient);
console.log(`Snapshot hash: ${snapshot.snapshotHash}`);
console.log(`Entries: ${snapshot.entryCount}`);
console.log(`Users: ${snapshot.balancesByUser.size}`);

// Compare snapshots
const diff = compareSnapshots(snapshot1, snapshot2);
if (!diff.identical) {
  console.log(`Balance differences: ${diff.balanceDifferences.length}`);
}
```

### Event Hashchain
Build cryptographic chain from events.

```typescript
import { buildHashchain, verifyHashchain } from './sim/reconcile/event_hashchain.js';

const hashchain = buildHashchain(events);
console.log(`Hashchain head: ${hashchain.headHash}`);
console.log(`Length: ${hashchain.length}`);

const verification = verifyHashchain(hashchain);
if (!verification.valid) {
  console.error(`Verification failed: ${verification.errors.join(', ')}`);
}
```

### Invariant Checks
Verify exchange correctness.

```typescript
import { checkAllInvariants } from './sim/reconcile/invariants.js';

const report = await checkAllInvariants(snapshot, adminClient);
console.log(`Invariants: ${report.summary.passed}/${report.summary.total} passed`);

if (!report.allPassed) {
  report.results.filter(r => !r.passed).forEach(r => {
    console.error(`Failed: ${r.name} - ${r.message}`);
  });
}
```

**Checked Invariants:**
1. **Double-entry integrity**: Every debit has a corresponding credit
2. **Solvency**: User balances ≤ deposits - withdrawals
3. **No negative balances**: All balances ≥ 0
4. **No duplicate credits**: Each deposit credited once
5. **Trade-ledger consistency**: Trades match ledger entries
6. **Balance sum consistency**: Sum of balances matches totals

### Proof Bundle
Generate comprehensive audit proof.

```typescript
import { generateProofBundle, saveProofBundle, verifyProofBundle } from './sim/reconcile/proof_bundle.js';

const bundle = await generateProofBundle({
  snapshot,
  hashchain,
  invariants,
  metadata: {
    generatedBy: 'e2e-test',
    environment: 'staging',
    notes: 'End of trading day reconciliation',
  },
});

const paths = await saveProofBundle(bundle, './artifacts/proofs');
console.log(`Proof saved to ${paths.bundlePath}`);

// Verify bundle integrity
const verification = verifyProofBundle(bundle);
if (!verification.valid) {
  console.error(`Bundle verification failed: ${verification.errors}`);
}
```

## Usage Examples

### Complete Market Making Test
```typescript
import { MarketMaker } from './sim/mm/maker.js';
import { ExchangeAPIClient, WSClient } from './http_client.js';

const httpClient = new ExchangeAPIClient({ baseURL: 'http://localhost:3000' });
const wsClient = new WSClient({ url: 'ws://localhost:3000' });

await wsClient.connect();

const mm = new MarketMaker(
  {
    market: 'BTC-USD',
    strategy: 'volatile',
    inventory: {
      initialBase: 10,
      initialQuote: 500000,
      maxBasePosition: 20,
      maxQuoteExposure: 1000000,
      targetRatio: 0.5,
    },
    riskLimits: {
      maxBasePosition: 20,
      maxQuoteExposure: 1000000,
      maxSpreadBps: 500,
      maxPriceDeviationPercent: 5,
      maxInventorySkew: 0.8,
    },
    tickSize: 0.01,
    minOrderSize: 0.001,
    quoteRefreshInterval: 5000,
    randomSeed: 12345, // Deterministic
  },
  httpClient,
  wsClient
);

await mm.start();

// Let it run for 60 seconds
await new Promise(resolve => setTimeout(resolve, 60000));

await mm.stop();

const stats = mm.getStats();
console.log(`Completed: ${stats.trades} trades, ${stats.quoteCycles} quote cycles`);
```

### Complete Reconciliation Test
```typescript
import { takeLedgerSnapshot } from './sim/reconcile/ledger_snapshot.js';
import { buildHashchain } from './sim/reconcile/event_hashchain.js';
import { checkAllInvariants } from './sim/reconcile/invariants.js';
import { generateProofBundle, saveProofBundle } from './sim/reconcile/proof_bundle.js';

// Take snapshot
const snapshot = await takeLedgerSnapshot(adminClient);

// Build hashchain from events
const events = await adminClient.get('/api/admin/events');
const hashchain = buildHashchain(events.data);

// Check invariants
const invariants = await checkAllInvariants(snapshot, adminClient);

// Generate proof
const bundle = await generateProofBundle({
  snapshot,
  hashchain,
  invariants,
});

// Save to disk
await saveProofBundle(bundle, './artifacts/proofs');

// Verify
const verification = verifyProofBundle(bundle);
console.assert(verification.valid, 'Proof bundle verification failed');
```

## Configuration

All simulators support configuration via environment variables or config objects:

```typescript
// Docker chaos
const dockerChaos = new DockerChaos({
  socketPath: process.env.DOCKER_SOCKET || '/var/run/docker.sock',
});

// Toxiproxy
const toxiproxy = new ToxiproxyClient({
  apiUrl: process.env.TOXIPROXY_API || 'http://localhost:8474',
});

// BitGo
const bitgoConfig = {
  apiUrl: process.env.BITGO_API_URL || 'https://test.bitgo.com',
  accessToken: process.env.BITGO_ACCESS_TOKEN,
  walletId: process.env.BITGO_WALLET_ID,
  coin: 'tbtc',
};
```

## Best Practices

1. **Determinism**: Use seeded RNG for reproducible tests
2. **Cleanup**: Always call cleanup methods to avoid resource leaks
3. **Timeouts**: Set appropriate timeouts for async operations
4. **Logging**: Enable verbose logging for debugging
5. **Isolation**: Run chaos tests in isolated environments
6. **Proof Archival**: Save proof bundles for audit trail

## Testing

Run individual simulator tests:
```bash
npm run test:sim:mm        # Market maker tests
npm run test:sim:deposits  # Deposit tests
npm run test:sim:chaos     # Chaos tests
npm run test:sim:reconcile # Reconciliation tests
```

## Performance

- Market maker: Can simulate 100+ quote updates/sec
- Deposits: 50+ simultaneous deposit flows
- Chaos: Sub-second fault injection
- Reconciliation: 10k+ entries/sec snapshot

## License

See LICENSE.txt at repository root.
