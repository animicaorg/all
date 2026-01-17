# Animica Mining Pool with PPLNS

A fully-featured mining pool implementation for the Animica blockchain with Pay Per Last N Shares (PPLNS) payout mode.

## Features

### Core Functionality
- **PPLNS Payouts**: Work-based window calculation with deterministic integer math
- **Share Accounting**: Tracks all submitted shares with miner/worker identity
- **Block Tracking**: Monitors block confirmations and detects orphans via RPC polling
- **Automatic Payouts**: Scheduled payout execution with batching and retry logic
- **Variable Difficulty (VarDiff)**: Dynamic per-connection difficulty adjustment (planned)
- **Abuse Prevention**: Ban system, rate limiting, and duplicate share detection

### Database
- **SQLite Backend**: Simple, reliable storage with migration support
- **Comprehensive Schema**: miners, workers, shares, blocks, balances, payouts
- **Idempotent Payouts**: Safe restart and retry without double-payment

### Statistics
- **Pool Stats**: Total miners, hashrate (EMA), shares, blocks, luck percentage
- **Miner Stats**: Per-miner hashrate, shares, earnings, balance
- **Worker Stats**: Per-worker tracking and performance

### API
- **HTTP JSON API**: Read-only endpoints for pool and miner data
- **Endpoints**:
  - `/api/pool/status` - Pool statistics
  - `/api/pool/blocks` - Recent blocks
  - `/api/pool/miners` - Miner list
  - `/api/miner/{address}/stats` - Miner statistics
  - `/api/miner/{address}/balance` - Miner balance
  - `/api/miner/{address}/payouts` - Payout history

### CLI
Complete command-line interface for pool operators:
```bash
# Start pool
animica pool up --address anim1... --bind 0.0.0.0 --pool-fee 1.0

# Stop pool
animica pool down

# Check status
animica pool status

# Manage payouts
animica pool payouts run [--dry-run]
animica pool payouts pause
animica pool payouts resume
animica pool payouts history --limit 50

# Database
animica pool db migrate
```

## Quick Start

### 1. Install Dependencies
```bash
cd /path/to/animica
pip install -e ".[dev]"
```

### 2. Start Pool
```bash
animica pool up \
  --address anim1yourpooladdress \
  --bind 127.0.0.1 \
  --port 3333 \
  --pool-fee 1.0 \
  --min-payout 1.0 \
  --daemon
```

### 3. Connect Miners
Miners connect via Stratum protocol:
```
stratum+tcp://127.0.0.1:3333
Username: <payout_address>[.<worker_name>]
Password: (optional)
```

### 4. Monitor Status
```bash
# View pool status
animica pool status

# Check API
curl http://127.0.0.1:8550/api/pool/status
```

### 5. Execute Payouts
```bash
# Dry run (preview)
animica pool payouts run --dry-run

# Execute
animica pool payouts run
```

## Configuration

### Environment Variables
```bash
# Pool settings
export ANIMICA_POOL_ADDRESS=anim1...
export ANIMICA_POOL_HOST=127.0.0.1
export ANIMICA_POOL_PORT=3333
export ANIMICA_POOL_FEE_PERCENT=1.0

# Database
export ANIMICA_POOL_DB=~/.animica/pool.db

# PPLNS
export ANIMICA_POOL_PPLNS_WINDOW=2  # Multiplier of network difficulty
export ANIMICA_POOL_MATURITY_BLOCKS=20

# Payouts
export ANIMICA_POOL_MIN_PAYOUT=1000000  # Base units
export ANIMICA_POOL_PAYOUT_INTERVAL=600  # Seconds

# VarDiff
export ANIMICA_POOL_VARDIFF=true
export ANIMICA_POOL_VARDIFF_TARGET=10.0  # Shares per minute
export ANIMICA_POOL_VARDIFF_MIN=0.01
export ANIMICA_POOL_VARDIFF_MAX=1.0

# API
export ANIMICA_POOL_API_ENABLED=true
export ANIMICA_POOL_API_HOST=127.0.0.1
export ANIMICA_POOL_API_PORT=8550

# Node RPC
export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
```

### Command-Line Options
All settings can be overridden via command-line flags:
```bash
animica pool up --help
```

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                      Mining Pool                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│  │   Stratum    │───▶│    Share     │───▶│    Share     │ │
│  │   Server     │    │  Validator   │    │   Recorder   │ │
│  └──────────────┘    └──────────────┘    └──────────────┘ │
│         │                                        │          │
│         ▼                                        ▼          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│  │     Job      │    │    Block     │    │   Database   │ │
│  │   Manager    │    │   Tracker    │    │   (SQLite)   │ │
│  └──────────────┘    └──────────────┘    └──────────────┘ │
│         │                    │                   │          │
│         ▼                    ▼                   ▼          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│  │   PPLNS      │◀───│   Payout     │    │    Stats     │ │
│  │ Calculator   │    │   Engine     │    │   Tracker    │ │
│  └──────────────┘    └──────────────┘    └──────────────┘ │
│         │                    │                   │          │
│         └────────────────────┴───────────────────┘          │
│                              │                              │
│                    ┌──────────────────┐                     │
│                    │   HTTP API       │                     │
│                    │  (FastAPI)       │                     │
│                    └──────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
              ┌──────────┐     ┌──────────┐
              │  Miners  │     │   Web    │
              │          │     │Dashboard │
              └──────────┘     └──────────┘
```

### Data Flow

1. **Miners Submit Shares**
   - Connect via Stratum protocol
   - Receive mining jobs
   - Submit shares with solutions

2. **Share Validation**
   - Check difficulty, job validity, duplicates
   - Calculate work weight (deterministic integer)
   - Detect block finds (shares meeting network target)

3. **Share Recording**
   - Get/create miner and worker records
   - Store share with all metadata
   - Update miner last_seen timestamp

4. **Block Tracking**
   - Record found blocks
   - Poll node RPC for confirmations
   - Detect orphans (reorg detection)
   - Update block states

5. **PPLNS Calculation**
   - When block matures (reaches confirmations)
   - Select shares backward from finding share
   - Accumulate until target work reached
   - Calculate per-miner payouts

6. **Payout Execution**
   - Aggregate mature balances
   - Enforce minimum payout threshold
   - Batch into transactions (max outputs per tx)
   - Submit via node RPC
   - Record payout and update balances

## PPLNS Details

### Window Calculation
The PPLNS window is work-based, not share-count-based:
- Window size = `network_difficulty * window_multiplier * 1_000_000`
- Default `window_multiplier = 2` (approximately 2 blocks worth of work)
- Shares are selected backward from the finding share until target work is accumulated

### Payout Distribution
```
block_reward = coinbase_value + tx_fees
pool_fee = block_reward * pool_fee_percent / 100
distributable = block_reward - pool_fee

For each miner in window:
    payout = (miner_work / total_work) * distributable
```

### Deterministic Rounding
- All calculations use integer math (base units)
- Work weight = `difficulty * 1_000_000`
- Payouts use integer division: `(work * amount) // total_work`
- Dust (leftover from rounding) goes to pool or next payout

## Security

### Default Settings
- Binds to `127.0.0.1` (localhost only)
- Auth tokens optional for private pools
- Rate limiting on connections and auth attempts
- Ban system for invalid share spam

### Public Pool
For public pools, additional security:
```bash
animica pool up \
  --address anim1... \
  --bind 0.0.0.0 \
  --auth-required \
  --auth-token <secret> \
  --ban-threshold 10 \
  --rate-limit-auth 60
```

### Best Practices
1. Use firewall rules to restrict access
2. Enable auth tokens for public pools
3. Monitor logs for abuse
4. Keep minimum payout reasonable to avoid dust spam
5. Use hot/cold wallet separation (planned feature)

## Testing

### Unit Tests
```bash
# Run all pool tests
pytest python/animica/pool/tests/ -v

# Run specific test
pytest python/animica/pool/tests/test_pplns.py -v

# Run with coverage
pytest python/animica/pool/tests/ --cov=animica.pool
```

### Integration Tests
(To be implemented)

## Roadmap

### Phase 1: Core ✅
- [x] Database schema and migrations
- [x] Share validation and recording
- [x] PPLNS calculation
- [x] Block tracking
- [x] Payout engine
- [x] CLI commands
- [x] Unit tests

### Phase 2: Enhancement 🚧
- [ ] VarDiff implementation
- [ ] Abuse prevention (banning, rate limiting)
- [ ] Stats tracking (EMA hashrate)
- [ ] HTTP API
- [ ] Web dashboard

### Phase 3: Advanced
- [ ] PPS payout mode
- [ ] SOLO mode
- [ ] Hot/cold wallet separation
- [ ] Multi-currency support
- [ ] Profit switching

## Contributing

Contributions welcome! Please:
1. Follow existing code style
2. Add tests for new features
3. Update documentation
4. Submit PR with clear description

## License

See LICENSE.txt

## Support

- Documentation: https://docs.animica.network
- Issues: https://github.com/animicaorg/all/issues
- Discord: https://discord.gg/animica
