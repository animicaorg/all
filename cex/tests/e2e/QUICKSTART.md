# E2E Test Harness - Quick Start Guide

## Overview

This is a comprehensive end-to-end test harness for the centralized exchange with:
- ✅ Deterministic market maker
- ✅ Deposit/withdrawal simulators  
- ✅ Chaos testing
- ✅ Cryptographic reconciliation proofs
- ✅ CI/CD integration

## Quick Start (3 steps)

### 1. Install Dependencies

```bash
cd cex/tests/e2e
pnpm install
```

### 2. Start E2E Stack

```bash
# From repo root
./cex/ops/docker/scripts/e2e_up.sh

# Wait ~60 seconds for all services to be healthy
```

### 3. Run Tests

```bash
# Smoke test (30-60s)
pnpm e2e:smoke

# Market maker (2-5min)
pnpm e2e:mm

# Full reconciliation proof (10-15min)
pnpm e2e:reconcile

# All scenarios (30-60min)
pnpm e2e:all
```

## One-Command Execution

```bash
# From repo root - handles everything
./cex/ops/docker/scripts/e2e_run.sh

# Specific scenario
./cex/ops/docker/scripts/e2e_run.sh --scenario smoke

# Keep stack running after
./cex/ops/docker/scripts/e2e_run.sh --keep
```

## Available Scenarios

| Scenario | Command | Duration | Purpose |
|----------|---------|----------|---------|
| Smoke Test | `pnpm e2e:smoke` | 30-60s | Basic health checks |
| Market Maker | `pnpm e2e:mm` | 2-5m | Automated trading |
| Stress Test | `pnpm e2e:stress` | 5-10m | Load testing |
| BitGo Deposits | `pnpm e2e -- --scenario deposits_bitgo` | 3-5m | Deposit flow |
| Animica Deposits | `pnpm e2e -- --scenario deposits_animica` | 3-5m | Blockchain deposits |
| BitGo Withdrawals | `pnpm e2e -- --scenario withdrawals_bitgo` | 3-5m | Withdrawal flow |
| Animica Withdrawals | `pnpm e2e -- --scenario withdrawals_animica` | 3-5m | Blockchain withdrawals |
| Chaos: Kill/Restart | `pnpm e2e -- --scenario chaos_kill_restart` | 5-10m | Service resilience |
| Chaos: Partition | `pnpm e2e -- --scenario chaos_partition` | 5-10m | Network faults |
| Blockchain Reorg | `pnpm e2e -- --scenario reorg_animica` | 3-5m | Reorg safety |
| Reconciliation | `pnpm e2e:reconcile` | 10-15m | Full proof |

## Advanced Usage

### Custom Parameters

```bash
# Custom duration and rate
tsx src/runner.ts --scenario stress --duration 300 --rate 100

# Multiple markets
tsx src/runner.ts --scenario market_maker --markets ANM-USD,ANM-BTC,ETH-USD

# Enable chaos
tsx src/runner.ts --scenario all --chaos true

# Use BitGo sandbox (requires credentials)
tsx src/runner.ts --scenario deposits_bitgo --use-bitgo-sandbox true

# Custom report paths
tsx src/runner.ts --scenario all --report-json ./my-report.json --report-md ./my-summary.md

# Keep stack running
tsx src/runner.ts --scenario smoke --keep true
```

### Chaos Testing

```bash
# Interactive chaos menu
./cex/ops/docker/scripts/e2e_chaos.sh

# Direct chaos scenarios
ENABLE_CHAOS=true ./cex/ops/docker/scripts/e2e_up.sh
pnpm e2e -- --scenario chaos_kill_restart --chaos true
```

## Configuration

### Environment Variables

```bash
# Service endpoints (defaults shown)
export API_GATEWAY_URL=http://localhost:13000
export ADMIN_API_URL=http://localhost:13001
export WS_URL=ws://localhost:13000
export ANIMICA_RPC=http://localhost:18545

# BitGo (optional)
export BITGO_ENV=sandbox  # or 'mock' (default)
export BITGO_ACCESS_TOKEN=your_token_here
export BITGO_WEBHOOK_SECRET=your_secret_here

# Chaos testing (optional)
export ENABLE_CHAOS=true
export TOXIPROXY_HOST=localhost
export TOXIPROXY_PORT=18474
```

### Mock vs. Sandbox Mode

**Mock Mode** (default):
- No external credentials needed
- Emulates BitGo webhooks
- Fast and deterministic
- Perfect for CI/CD

**Sandbox Mode**:
- Set `BITGO_ENV=sandbox`
- Requires `BITGO_ACCESS_TOKEN` and `BITGO_WEBHOOK_SECRET`
- Real BitGo API calls
- Use for integration validation

## Understanding Reports

### JSON Report
Machine-readable results saved to `artifacts/report-<timestamp>.json`

```json
{
  "runId": "uuid",
  "passed": true,
  "scenarios": [...],
  "metrics": {
    "ordersSubmitted": 1000,
    "trades": 250,
    "p99LatencyMs": 150
  },
  "invariants": {
    "ledgerDoubleEntryOk": true,
    "solvencyOk": true,
    ...
  },
  "proofBundlePath": "artifacts/proof-<ts>.json"
}
```

### Markdown Report
Human-readable summary saved to `artifacts/report-<timestamp>.md`

Contains:
- ✅/❌ Status for each scenario
- Performance metrics table
- Invariant check results
- Recommendations
- Link to proof bundle

### Proof Bundle
Cryptographic proof saved to `artifacts/proof-<timestamp>.json`

Contains:
- Event hashchain
- Root hash
- Snapshot summaries
- First 100 events for verification

## Troubleshooting

### Stack won't start

```bash
# Check logs
docker compose -f cex/ops/docker/docker-compose.e2e.yml -p cex-e2e logs

# Hard reset
docker compose -f cex/ops/docker/docker-compose.e2e.yml -p cex-e2e down -v
./cex/ops/docker/scripts/e2e_up.sh
```

### Tests fail immediately

```bash
# Verify services are healthy
curl http://localhost:13000/health
curl http://localhost:13001/health

# Check migrations ran
docker compose -f cex/ops/docker/docker-compose.e2e.yml -p cex-e2e exec postgres psql -U cex -d cex_e2e -c "\dt"
```

### Port conflicts

The E2E stack uses ports 13000-19999. If you have conflicts:

```bash
# Stop conflicting services
docker ps

# Or edit docker-compose.e2e.yml to change ports
```

### Slow performance

```bash
# Increase Docker resources
# Docker Desktop -> Preferences -> Resources
# Recommended: 4 CPU, 8GB RAM

# Or run fewer concurrent scenarios
pnpm e2e:smoke  # Single quick scenario
```

## CI/CD

### GitHub Actions

The E2E tests run automatically:

- **On PRs**: Smoke test + reconciliation proof (~10 min)
- **Daily**: Full suite of all scenarios (~60 min)
- **Manual**: Workflow dispatch with custom parameters

### Viewing CI Results

1. Go to Actions tab in GitHub
2. Select "E2E Tests" workflow
3. Click on a run
4. Download artifacts to view reports

### Running Locally Like CI

```bash
# Emulate CI environment
export BITGO_ENV=mock
export ENABLE_CHAOS=false

./cex/ops/docker/scripts/e2e_run.sh --scenario smoke --duration 120
```

## File Structure

```
cex/tests/e2e/
├── README.md                      # Full documentation
├── E2E_IMPLEMENTATION_COMPLETE.md # Implementation summary
├── package.json                   # Dependencies
├── tsconfig.json                  # TypeScript config
├── artifacts/                     # Test outputs (gitignored)
└── src/
    ├── config.ts                  # Configuration
    ├── runner.ts                  # Test orchestrator
    ├── report.ts                  # Report generation
    ├── http_client.ts             # REST client
    ├── ws_client.ts               # WebSocket client
    ├── scenarios/                 # 11 test scenarios
    └── sim/                       # Simulators
        ├── mm/                    # Market maker
        ├── deposits/              # Deposit simulators
        ├── withdrawals/           # Withdrawal simulators
        ├── chaos/                 # Chaos testing
        └── reconcile/             # Reconciliation
```

## Stack Components

When you run `e2e_up.sh`, it starts:

- **PostgreSQL** (port 15432) - Test database
- **Redis** (port 16379) - Cache
- **NATS** (port 14222) - Message bus
- **API Gateway** (port 13000) - Exchange API
- **Admin API** (port 13001) - Admin operations
- **Matching Engine** - Order matching
- **Ledger Service** - Balance management
- **Animica Asset Service** - Blockchain integration
- **Withdrawals Service** - Withdrawal processing
- **BitGo Webhook Ingestor** - Deposit webhooks
- **Animica Devnet** (port 18545) - Local blockchain

Optional:
- **Toxiproxy** (port 18474) - Network fault injection
- **MinIO** (ports 19000-19001) - Proof storage

## Best Practices

### For Development

1. **Run smoke test first** to verify stack is healthy
2. **Use --keep flag** to debug issues
3. **Check artifacts/** for detailed logs
4. **Start with mock mode** before sandbox

### For CI/CD

1. **Always use mock mode** (no credentials)
2. **Keep durations short** (2-5 minutes)
3. **Upload artifacts** for debugging
4. **Run full suite** only on schedule/manual

### For Production Validation

1. **Use sandbox mode** with real credentials
2. **Run full suite** including chaos
3. **Review proof bundles** carefully
4. **Keep proof artifacts** for 90 days

## Getting Help

- **README.md**: Full documentation
- **E2E_IMPLEMENTATION_COMPLETE.md**: Implementation details
- **Inline comments**: All code is documented
- **GitHub Issues**: Report bugs
- **Logs**: Check `artifacts/logs-*.txt`

## Next Steps

1. **Run smoke test** to verify setup
2. **Run reconciliation proof** to see full capabilities
3. **Review reports** in `artifacts/`
4. **Customize scenarios** as needed
5. **Integrate with CI/CD** pipeline

## Quick Reference

```bash
# Setup
cd cex/tests/e2e && pnpm install

# Start
./cex/ops/docker/scripts/e2e_up.sh

# Test
pnpm e2e:smoke
pnpm e2e:reconcile

# Stop
./cex/ops/docker/scripts/e2e_down.sh

# Clean
docker compose -f cex/ops/docker/docker-compose.e2e.yml -p cex-e2e down -v
```

## Success! 🎉

You now have a production-grade E2E test harness with:
- ✅ Comprehensive test coverage
- ✅ Cryptographic correctness proofs
- ✅ Automated CI/CD integration
- ✅ Zero external dependencies (mock mode)
- ✅ Full chaos engineering capabilities

Run `pnpm e2e:smoke` to get started!
