# CEX E2E Test Harness + Simulation

Comprehensive end-to-end testing framework for the centralized exchange with synthetic market makers, deposit/withdrawal simulators, chaos testing, and cryptographic reconciliation proofs.

## Overview

This test harness validates the entire exchange stack end-to-end:

- **Synthetic Market Maker**: Deterministic market making with multiple strategies
- **Load Generator**: Configurable stress testing with mixed order types
- **Deposit Simulators**: BitGo (sandbox/mock) and Animica devnet integration
- **Withdrawal Simulators**: Both BitGo and Animica withdrawal flows
- **Chaos Testing**: Service restarts, network partitions, message duplication
- **Reconciliation Proof**: Cryptographic proof of ledger correctness with invariant checks

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js 20+ with pnpm
- (Optional) BitGo sandbox credentials for real sandbox testing

### One-Command Execution

```bash
# From repository root
./cex/ops/docker/scripts/e2e_run.sh

# Or with pnpm from cex/tests/e2e
pnpm e2e
```

## Usage

### Running Specific Scenarios

```bash
# Smoke test (quick validation)
pnpm e2e:smoke

# Market maker scenario
pnpm e2e:mm

# Stress testing
pnpm e2e:stress

# Full reconciliation proof
pnpm e2e:reconcile

# All scenarios
pnpm e2e:all
```

### Advanced Options

```bash
# Custom duration and rate
tsx src/runner.ts --scenario stress --duration 300 --rate 100

# Specific markets
tsx src/runner.ts --scenario market_maker --markets ANM-USD,ANM-BTC

# Enable BitGo sandbox (requires credentials)
tsx src/runner.ts --scenario deposits_bitgo --use-bitgo-sandbox true

# Enable chaos testing
tsx src/runner.ts --scenario all --chaos true

# Keep stack running after tests
tsx src/runner.ts --scenario smoke --keep true

# Custom report paths
tsx src/runner.ts --scenario all --report-json ./my-report.json --report-md ./my-report.md
```

## Architecture

### Directory Structure

```
cex/tests/e2e/
├── src/
│   ├── config.ts              # Configuration management
│   ├── runner.ts              # Main test orchestrator
│   ├── report.ts              # Report generation
│   ├── http_client.ts         # REST API client
│   ├── ws_client.ts           # WebSocket client
│   ├── scenarios/             # Test scenarios
│   │   ├── smoke.ts
│   │   ├── market_maker.ts
│   │   ├── stress.ts
│   │   ├── deposits_*.ts
│   │   ├── withdrawals_*.ts
│   │   ├── chaos_*.ts
│   │   └── reconciliation_proof.ts
│   └── sim/                   # Simulators
│       ├── mm/                # Market maker
│       ├── deposits/          # Deposit simulators
│       ├── withdrawals/       # Withdrawal simulators
│       ├── chaos/             # Chaos injection
│       └── reconcile/         # Reconciliation & proof
├── artifacts/                 # Test outputs
└── package.json
```

### Stack Components (docker-compose.e2e.yml)

- **Infrastructure**: PostgreSQL, Redis, NATS
- **Exchange Services**: API Gateway, Matching Engine, Ledger, Deposits, Withdrawals
- **Animica**: Local devnet node with fast blocks
- **Fault Injection**: Toxiproxy (optional)
- **Storage**: MinIO for proof bundles (optional)

## Scenarios

### 1. Smoke Test
Quick health checks on all services and basic operations.

**Duration**: ~30 seconds  
**Purpose**: Validate stack is operational

### 2. Market Maker
Deterministic synthetic MM with multiple strategies:
- Tight spread quoting
- Volatile market adaptation
- Inventory-skewed quoting

**Features**:
- Seeded RNG for reproducibility
- Configurable quote ladders
- Risk management limits

### 3. Stress Test
High-throughput load generator with:
- N concurrent clients
- Mixed order types (limit, market, IOC, FOK)
- Order placement/cancellation at target rate

**Validations**:
- No negative balances
- Open order count consistency
- Monotonic trade IDs and sequences

### 4. Deposit Scenarios

#### BitGo Deposits
- **Sandbox Mode**: Real BitGo sandbox API integration
- **Mock Mode**: Emulated webhook payloads and confirmations

**Tests**:
- Address generation
- Webhook ingestion
- Confirmation progression
- Balance crediting

#### Animica Deposits
- Local devnet with faucet
- Real block production
- Reorg simulation
- Confirmation safety

### 5. Withdrawal Scenarios

#### BitGo Withdrawals
- Approval workflow
- Signing and broadcast
- Confirmation tracking
- Idempotency validation

#### Animica Withdrawals
- Transaction broadcasting
- Fee handling
- Destination credit verification

### 6. Chaos Testing

#### Kill/Restart
- Random service restarts
- Recovery validation
- No duplicate credits/debits
- Orderbook stream resync

#### Network Partitions
- Latency injection (via Toxiproxy)
- Packet drops
- Connection cuts
- Message duplication

**Validations**:
- Idempotency in all handlers
- WS client reconnection
- Sequence gap handling

### 7. Reconciliation Proof (CRITICAL)

Generates cryptographic proof of ledger correctness.

**Snapshots**:
- Account balances
- Trades table
- Deposits/withdrawals
- Ledger journal entries

**Invariants Checked**:
1. **Solvency**: `hot_wallet + cold_wallet = Σ(user_balances) + fees + adjustments`
2. **Double-Entry**: `Σ(debits) = Σ(credits)` for all journal entries
3. **Idempotency**: No duplicate external event IDs
4. **Trade Consistency**: Each trade maps to correct ledger movements

**Hashchain**:
- Append-only event chain
- Trade events (ID, timestamp, price, size)
- Deposit/withdrawal events
- Root hash output
- Optional cryptographic signing

**Output**: JSON proof bundle saved to `artifacts/proof-<timestamp>.json`

## Reports

### JSON Report Structure

```json
{
  "run_id": "uuid",
  "seed": 12345,
  "scenarios": [...],
  "metrics": {
    "orders_submitted": 1000,
    "cancels": 500,
    "trades": 250,
    "deposits": 10,
    "withdrawals": 5,
    "p50_latency_ms": 50,
    "p99_latency_ms": 200,
    "ws_disconnects": 0,
    "faults_injected": [...]
  },
  "invariants": {
    "ledger_double_entry_ok": true,
    "solvency_ok": true,
    "no_negative_balances": true,
    "no_duplicate_credits": true,
    "trade_ledger_consistency_ok": true
  },
  "proof_bundle_path": "artifacts/proof-<ts>.json",
  "logs_path": "artifacts/logs-<run_id>.txt"
}
```

### Markdown Report

Human-readable summary with:
- Pass/fail per scenario
- Key metrics
- Invariant check results
- Link to proof root hash
- Recommendations

## Environment Variables

### Required
```bash
# Set in ops/docker/env/.env.e2e
DB_HOST=postgres
DB_PORT=5432
DB_NAME=cex_e2e
DB_USER=cex
DB_PASSWORD=secret

REDIS_HOST=redis
REDIS_PORT=6379

NATS_URL=nats://nats:4222
```

### Optional (BitGo Sandbox)
```bash
BITGO_ENV=sandbox
BITGO_ACCESS_TOKEN=<your-token>
BITGO_WEBHOOK_SECRET=<your-secret>

# If not set, tests run in mock mode
DEPOSITS_BITGO_MODE=mock  # or 'sandbox'
```

### Optional (Chaos Testing)
```bash
TOXIPROXY_HOST=toxiproxy
TOXIPROXY_PORT=8474
ENABLE_CHAOS=true
```

## CI Integration

E2E tests run automatically on:
- Pull requests to `main` (smoke + reconciliation in mock mode)
- Daily scheduled runs (full suite)

### Local CI Emulation

```bash
# Run E2E tests as CI would
./cex/ops/docker/scripts/e2e_run.sh --scenario smoke --duration 120
```

### Artifacts

CI uploads:
- JSON report
- Markdown summary
- Proof bundle
- Service logs

## Development

### Adding New Scenarios

1. Create scenario file in `src/scenarios/`
2. Implement `ScenarioRunner` interface
3. Register in `src/runner.ts`
4. Add script to `package.json`

### Testing Locally

```bash
# Install dependencies
pnpm install

# Start E2E stack
../../../ops/docker/scripts/e2e_up.sh

# Run tests
pnpm e2e:smoke

# Clean up
../../../ops/docker/scripts/e2e_down.sh
```

## Troubleshooting

### Stack won't start
```bash
# Check logs
docker compose -f ops/docker/docker-compose.e2e.yml logs

# Restart
./ops/docker/scripts/e2e_down.sh
./ops/docker/scripts/e2e_up.sh
```

### Tests fail immediately
- Ensure migrations ran: `./ops/docker/scripts/migrate.sh`
- Verify services are healthy: `curl http://localhost:3000/health`

### BitGo sandbox issues
- Verify credentials in `.env.e2e`
- Fall back to mock mode: `DEPOSITS_BITGO_MODE=mock`

### Reconciliation proof fails
- Check invariant violations in report
- Review `artifacts/proof-<ts>.json` for details
- Verify no manual DB changes during test

## Security Considerations

- **Never commit BitGo credentials** to version control
- Proof bundles may contain sensitive transaction data
- Use `.gitignore` to exclude `artifacts/` from commits
- Sanitize reports before sharing publicly

## Performance Guidelines

### Recommended Test Durations

- Smoke: 30-60 seconds
- Market Maker: 2-5 minutes
- Stress: 5-10 minutes (CI: 2 minutes)
- Deposits: 3-5 minutes
- Withdrawals: 3-5 minutes
- Chaos: 5-10 minutes
- Reconciliation: 5-10 minutes

### CI Constraints

- Total runtime: < 10 minutes
- Use mock mode for external services
- Reduced load and duration

## License

Same as parent repository.
