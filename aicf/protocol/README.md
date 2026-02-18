# AICF Protocol - GPU Contributor Redistribution System

**Transform AICF from a treasury address into a complete on-chain protocol.**

## Quick Start

### For ENA Service Operators

Enable protocol recording in 3 steps:

```bash
# 1. Set environment variables
export ENA_AICF_PROTOCOL_ENABLED=true
export ENA_AICF_PROTOCOL_DB=./ena_data/aicf_protocol.db

# 2. Follow integration guide
# See: ena/PROTOCOL_INTEGRATION.md

# 3. Restart ENA service
uvicorn ena.services.ena_node.main:app
```

### For GPU Workers

```bash
# Register as worker
animica ena aicf register-worker YOUR_ADDRESS \
    --name "My GPU Farm" \
    --stake 100

# View protocol status
animica ena aicf protocol-status

# Claim rewards after epoch
animica ena aicf claim-rewards WORKER_ID EPOCH_ID
```

### For Developers

```python
from aicf.protocol.state import ProtocolState
from aicf.protocol.economics import EpochAccountant
from aicf.protocol.rpc import ProtocolRPCMethods

# Initialize
state = ProtocolState("aicf.db")
accountant = EpochAccountant(state)
rpc = ProtocolRPCMethods(state, accountant)

# Use RPC methods
methods = rpc.make_methods()
status = methods["aicf.protocol.getStatus"]()
```

## What Is This?

The AICF Protocol is an on-chain system that:

1. **Tracks GPU Contributions**: Workers register and submit verified training/eval work
2. **Fair Redistribution**: Epoch-based rewards proportional to verified work credits
3. **ENA Integration**: All ENA payments automatically fund GPU infrastructure
4. **Verifiable**: Challenge-based verification with extension points for zkML/TEE
5. **Decentralized**: No single point of control, governed by protocol parameters

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              AICF Protocol Layer                         │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐│
│  │ Workers  │  │   Jobs   │  │Submissions│  │ Epochs  ││
│  │ Registry │  │   Specs  │  │  + Proofs │  │Accounting││
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘│
│                                                          │
│  15 JSON-RPC Methods → Transport-Agnostic               │
└─────────────────────────────────────────────────────────┘
         ▲                    ▲                   ▲
         │                    │                   │
    ┌────┴────┐         ┌─────┴──────┐      ┌────┴────┐
    │ Workers │         │Coordinator │      │   ENA   │
    │ (GPUs)  │         │ (optional) │      │ Service │
    └─────────┘         └────────────┘      └─────────┘
```

## Core Concepts

### Epochs
Time periods for batching rewards (default: 1000 blocks)

```python
epoch = {
    "inflowTotal": "10000000000",      # Total funds received
    "inflowForWorkers": "7000000000",  # 70% for workers
    "totalCredits": "500",             # Total work credits
    "workerCount": 10                  # Contributing workers
}
```

### Credits
Non-transferable units awarded for verified work

```python
# Worker completes job
credits = job.rewardWeight * job.difficulty

# At epoch finalization
workerShare = (inflowForWorkers * workerCredits) // totalCredits
```

### Reward Split
Configurable distribution (basis points):

- **70%** (7000 bp) → GPU Workers
- **20%** (2000 bp) → Treasury
- **5%** (500 bp) → Dev Fund
- **5%** (500 bp) → Burn

### Verification (MVP)
Challenge window system:

1. Worker submits artifact commitments
2. 100-block challenge window opens
3. Anyone can verify and challenge
4. Credits awarded if no successful challenges

**Future:** zkML proofs, TEE attestations, VDF verification

## RPC Methods (15 Total)

### Worker Management
- `aicf.protocol.registerWorker` - Register as GPU worker
- `aicf.protocol.updateWorker` - Update worker info
- `aicf.protocol.getWorker` - Get worker details
- `aicf.protocol.listWorkers` - List workers with filters

### Job Management
- `aicf.protocol.createJob` - Create training/eval job
- `aicf.protocol.listJobs` - List available jobs
- `aicf.protocol.getJob` - Get job details

### Work Submission
- `aicf.protocol.submitWork` - Submit completed work
- `aicf.protocol.getSubmission` - Get submission details

### Challenge System
- `aicf.protocol.challengeSubmission` - Challenge invalid work
- `aicf.protocol.resolveChallenge` - Resolve challenge

### Epoch Management
- `aicf.protocol.finalizeEpoch` - Close epoch, compute rewards
- `aicf.protocol.getEpoch` - Get epoch stats

### Claims
- `aicf.protocol.claim` - Claim epoch rewards

### Status
- `aicf.protocol.getStatus` - Get protocol config

## File Structure

```
aicf/
  db/
    schema_protocol.sql              # Complete database schema
  protocol/
    __init__.py                       # Module exports
    state.py                          # State management (31KB)
    economics.py                      # Epoch accounting (10KB)
    rpc.py                            # 15 RPC methods (17KB)
    integration.py                    # ENA integration (5KB)
    SPECIFICATION.md                  # Technical spec (15KB)
    IMPLEMENTATION_SUMMARY.md         # Developer guide (12KB)
    tests/
      test_protocol.py                # 15 test cases (12KB)

ena/
  PROTOCOL_INTEGRATION.md             # Integration guide (4KB)

python/animica/cli/
  AICF_PROTOCOL_COMMANDS.py          # CLI commands (13KB)
```

## Documentation

### For Users
- **Getting Started**: See "Quick Start" above
- **CLI Reference**: `python/animica/cli/AICF_PROTOCOL_COMMANDS.py`

### For Developers
- **Technical Specification**: `aicf/protocol/SPECIFICATION.md`
- **Implementation Summary**: `aicf/protocol/IMPLEMENTATION_SUMMARY.md`
- **Integration Guide**: `ena/PROTOCOL_INTEGRATION.md`

### For ENA Operators
- **Integration Guide**: `ena/PROTOCOL_INTEGRATION.md`
- Configuration via environment variables
- Graceful degradation if module unavailable

## Testing

Run comprehensive test suite:

```bash
# Unit tests (requires pytest)
pytest aicf/protocol/tests/test_protocol.py -v

# Specific test
pytest aicf/protocol/tests/test_protocol.py::TestEconomics::test_finalize_epoch -v
```

Test coverage:
- ✅ State management (workers, jobs, submissions, epochs, credits)
- ✅ Economics (reward splits, inflow tracking, finalization)
- ✅ RPC methods (all 15 methods)
- ✅ Deterministic calculations

## Security

### Implemented
- ✅ Deterministic rounding (consensus-safe)
- ✅ Double-claim prevention (DB constraints)
- ✅ Transaction-safe state updates
- ✅ Input validation on all RPC methods

### Pending
- ⏳ Challenge window time enforcement
- ⏳ Submission nullifier tracking
- ⏳ State machine validation

### Known Limitations
- MVP verification uses challenge windows (zkML planned for v2)
- Coordinator initially centralized (decentralization planned)
- Model approval requires governance

## Roadmap

### MVP (Current)
- ✅ Core protocol implementation
- ✅ ENA integration module
- ✅ Comprehensive documentation
- ⏳ CLI integration
- ⏳ ENA service wiring

### Phase 2 (Q2 2026)
- zkML proof integration
- TEE attestation support
- Worker client application
- Automated coordinator

### Phase 3 (Q3 2026)
- PoIES consensus integration
- Model marketplace
- Governance voting
- Multi-chain support

## Contributing

See `CONTRIBUTING.md` for guidelines.

Key areas for contribution:
- Worker client implementation
- zkML proof generation
- TEE attestation verification
- Challenge window enforcement
- Additional tests

## License

See `LICENSE.txt` for details.

## Support

- **Documentation**: See `aicf/protocol/` directory
- **Issues**: GitHub Issues
- **Discord**: Animica Community Discord

---

**Status:** Core Complete (70%)  
**Version:** 1.0-MVP  
**Last Updated:** 2026-02-18
