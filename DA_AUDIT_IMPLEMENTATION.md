# DA Provider Blob Assignment, Replication, and Audit System

This implementation provides a complete system for assigning blobs to DA storage providers, tracking replication, and auditing proof-of-storage through cryptographic challenges.

## Overview

The system consists of four main components:

1. **Assignment**: Intelligently assign blobs to providers with replication and diversity
2. **Audit**: Challenge-response protocol to verify providers store assigned data
3. **Scheduler**: Automated audit rounds with scoring and jailing
4. **CLI**: Command-line tools for managing audits and provider operations

## Architecture

```
┌─────────────────┐
│  Blob Storage   │
│   (DA Layer)    │
└────────┬────────┘
         │
         │ assign_blob()
         ▼
┌─────────────────────────────────────────┐
│      Provider Registry                  │
│  - ProviderEntry (capacity, scores)     │
│  - BlobAssignment (provider → blob)     │
└─────────┬───────────────────────────────┘
          │
          │ audit cycle
          ▼
┌─────────────────────────────────────────┐
│      Audit System                       │
│  - Challenge (byte-range, proofs)       │
│  - Response (signed data)               │
│  - Verification (hash, signature)       │
└─────────┬───────────────────────────────┘
          │
          │ scoring
          ▼
┌─────────────────────────────────────────┐
│      Audit Scheduler                    │
│  - Score updates (+100/-200)            │
│  - Jailing (score < 1000)               │
│  - Statistics tracking                  │
└─────────────────────────────────────────┘
```

## Part 1: Assignment & Replication

**File**: `da/provider/assignment.py`

### Key Functions

#### `assign_blob(registry, blob_commitment, size, replication_factor=3)`

Assigns a blob to R providers for redundant storage.

**Selection Criteria**:
- Active providers (not jailed)
- Uptime score >= 5000 (configurable)
- Available capacity >= blob size
- Regional diversity (prefer different regions)

**Algorithm**:
1. Filter eligible providers
2. Score each provider: `uptime_score + hash_bonus + capacity_bonus`
3. Sort by score and select top R providers
4. Maximize region diversity among selected
5. Create BlobAssignment records
6. Update provider capacity commitments

**Example**:
```python
from da.provider.assignment import assign_blob
from da.provider.registry import ProviderRegistry

registry = ProviderRegistry()
blob_commitment = bytes.fromhex("abc123...")
size = 50 * 1024 * 1024  # 50 MB

assignments = assign_blob(
    registry=registry,
    blob_commitment=blob_commitment,
    size=size,
    replication_factor=3,
)

print(f"Assigned to {len(assignments)} providers")
```

### Scoring Formula

```
score = uptime_score                          # 0-10000
      + hash(blob + provider_id) % 1000       # fairness
      + (available / advertised) * 500        # capacity preference
```

This ensures:
- Quality providers (high uptime) are preferred
- Fair distribution across providers (deterministic shuffle)
- Providers with more available space get bonus

## Part 2: Audit System

**File**: `da/provider/audit.py`

### Challenge Types

1. **byte-range**: Request random byte range from blob
2. **merkle-proof**: Request Merkle proof for a leaf
3. **nmt-proof**: Request Namespaced Merkle Tree proof

### Challenge Creation

#### `create_challenge(provider_id, blob_commitment, challenge_type="byte-range")`

Creates a cryptographic challenge with:
- Unique challenge ID
- Random nonce (32 bytes)
- Challenge-specific parameters
- Deadline (default: 1 hour)

**Example**:
```python
from da.provider.audit import create_challenge

challenge = create_challenge(
    provider_id=provider_id,
    blob_commitment=blob_commitment,
    challenge_type="byte-range",
    deadline_seconds=3600,
)

print(f"Challenge ID: {challenge.challenge_id.hex()}")
print(f"Offset: {challenge.params[0]}, Length: {challenge.params[1]}")
```

### Response Verification

#### `verify_response(challenge, response, provider, actual_blob_data=None)`

Verifies a provider's response:
1. Check challenge_id and provider_id match
2. Verify response submitted before deadline
3. Verify post-quantum signature (Dilithium3)
4. Check response data against actual blob

**Returns**: `(passed: bool, failure_reason: Optional[str])`

### Database

**`AuditDatabase`** stores:
- Challenges (indexed by challenge_id and provider_id)
- Responses (indexed by challenge_id)
- Results (indexed by provider_id for statistics)

## Part 3: Audit Scheduler

**File**: `da/provider/audit_scheduler.py`

### Configuration

```python
@dataclass
class AuditSchedulerConfig:
    sample_size: int = 10               # Audits per round
    jail_threshold: int = 1000          # Jail if score < this
    jail_duration_seconds: int = 86400  # 24 hours
    min_audit_interval_seconds: int = 300  # 5 minutes
    challenge_type: str = "byte-range"
    deadline_seconds: int = 3600        # 1 hour
```

### Audit Round

#### `scheduler.run_audit_round(blob_data_provider=None)`

Executes a complete audit cycle:

1. **Selection**: Pick random sample of provider-blob pairs
   - Skip recently audited (< min_interval)
   - Skip jailed providers
   - Use deterministic random seed (current hour)

2. **Challenge**: Create and store challenges

3. **Response**: Collect responses from database

4. **Verification**: Verify responses and create results

5. **Scoring**: Update provider scores
   - Pass: +100 points
   - Fail: -200 points
   - Clamp to [0, 10000]

6. **Jailing**: Jail providers with score < threshold
   - Duration: 24 hours (configurable)
   - Set active=False
   - Set jailed_until timestamp

**Example**:
```python
from da.provider.audit_scheduler import AuditScheduler, AuditSchedulerConfig

config = AuditSchedulerConfig(sample_size=10, jail_threshold=1000)
scheduler = AuditScheduler(registry, audit_db, config)

results = scheduler.run_audit_round()
print(f"Audited {len(results)} providers")
print(f"Passed: {sum(1 for r in results if r.passed)}")
```

### Statistics

#### `scheduler.get_audit_stats(provider_id)`

Returns audit statistics:
```python
{
    "total": 100,
    "passed": 95,
    "failed": 5,
    "pass_rate": 0.95,
    "avg_score_delta": 80.0,
}
```

## Part 4: CLI Commands

**File**: `da/cli/audit.py`

### Commands

#### Challenge

```bash
animica da audit challenge <provider_id> <blob_commitment> \
  --challenge-type byte-range \
  --deadline 3600
```

#### Respond (for providers)

```bash
animica da audit respond <challenge_id> \
  --provider-key ~/.animica/provider_key.json
```

#### Run Audit Round

```bash
animica da audit run \
  --sample-size 10 \
  --registry-db ~/.animica/provider_registry.db \
  --audit-db ~/.animica/audit_results.db
```

#### Show Results

```bash
animica da audit results <provider_id> --limit 20
```

#### Jail Management

```bash
# Jail a provider
animica da audit jail <provider_id> --duration 86400 --reason "Low uptime"

# Unjail a provider
animica da audit unjail <provider_id>

# List jailed providers
animica da audit jailed
```

## Part 5: Provider Sync Enhancement

**File**: `da/cli/provider.py` (updated)

Enhanced sync command with hash verification:

```bash
animica da provider sync \
  --path /storage/blobs \
  --da-url http://127.0.0.1:8648 \
  --verify
```

Features:
- Downloads missing blobs from DA service
- Verifies hashes match assignments
- Re-downloads on hash mismatch
- Content-addressed storage
- Progress reporting

## Security Features

### Post-Quantum Signatures

Provider responses use **Dilithium3** signatures:
- Domain separation: `ANIMICA_DA_AUDIT_RESPONSE`
- Message: `challenge_id + response_type + hash(payload)`
- Verification before accepting response

### Nonce Freshness

Each challenge includes a random 32-byte nonce to prevent:
- Replay attacks
- Pre-computed responses
- Response caching

### Deadline Enforcement

Responses submitted after deadline are rejected:
- Default: 1 hour
- Configurable per challenge
- Prevents delayed responses

## Testing

### Unit Tests

1. **`test_assignment.py`** (9 tests)
   - Basic assignment
   - Diversity and redundancy
   - Capacity constraints
   - Uptime filtering
   - Jailed provider filtering

2. **`test_audit.py`** (15 tests)
   - Challenge creation
   - Response verification
   - Database storage
   - Signature checking
   - Score updates

3. **`test_audit_scheduler.py`** (12 tests)
   - Audit round execution
   - Provider selection
   - Jailing logic
   - Statistics

### Integration Demo

Run complete workflow:
```bash
python -c "from da.provider.assignment import assign_blob; ..."
```

See output in test runs above.

## Database Schema

### Provider Registry

```sql
CREATE TABLE providers (
    provider_id BLOB PRIMARY KEY,
    pubkey BLOB NOT NULL,
    capacity_bytes_advertised INTEGER,
    capacity_bytes_committed INTEGER,
    uptime_score INTEGER,
    jailed_until INTEGER,
    ...
);

CREATE TABLE blob_assignments (
    blob_commitment BLOB NOT NULL,
    provider_id BLOB NOT NULL,
    assigned_at INTEGER,
    replicas INTEGER,
    blob_size INTEGER,
    PRIMARY KEY (blob_commitment, provider_id)
);
```

### Audit Database

```sql
CREATE TABLE challenges (
    challenge_id BLOB PRIMARY KEY,
    provider_id BLOB NOT NULL,
    blob_commitment BLOB NOT NULL,
    challenge_type TEXT,
    deadline INTEGER,
    ...
);

CREATE TABLE responses (
    challenge_id BLOB PRIMARY KEY,
    provider_id BLOB NOT NULL,
    response_type TEXT,
    signature BLOB,
    ...
);

CREATE TABLE results (
    challenge_id BLOB PRIMARY KEY,
    provider_id BLOB NOT NULL,
    passed INTEGER NOT NULL,
    score_delta INTEGER,
    ...
);
```

## Configuration

### Default Values

```python
DEFAULT_REPLICATION_FACTOR = 3
DEFAULT_UPTIME_SCORE = 5000  # 50%
MAX_UPTIME_SCORE = 10000     # 100%

DEFAULT_CHALLENGE_DEADLINE_SECONDS = 3600  # 1 hour
SCORE_DELTA_PASS = 100
SCORE_DELTA_FAIL = -200

DEFAULT_SAMPLE_SIZE = 10
DEFAULT_JAIL_THRESHOLD = 1000
DEFAULT_JAIL_DURATION_SECONDS = 86400  # 24 hours
MIN_AUDIT_INTERVAL_SECONDS = 300  # 5 minutes
```

### Environment Variables

```bash
export ANIMICA_REGISTRY_DB=~/.animica/provider_registry.db
export ANIMICA_AUDIT_DB=~/.animica/audit_results.db
export ANIMICA_DA_URL=http://127.0.0.1:8648
```

## Production Deployment

### Provider Setup

1. Register provider:
```bash
animica da provider register \
  --capacity 1TB \
  --endpoint http://provider.example.com \
  --path /storage/blobs \
  --region us-west
```

2. Sync assigned blobs:
```bash
animica da provider sync --path /storage/blobs --verify
```

3. Start provider service:
```bash
animica da serve --path /storage/blobs --port 8648
```

### Coordinator Setup

1. Run periodic audit rounds:
```bash
# Cron job every 5 minutes
*/5 * * * * animica da audit run --sample-size 20
```

2. Monitor jailed providers:
```bash
animica da audit jailed
```

3. Review audit statistics:
```bash
animica da audit results <provider_id>
```

## Troubleshooting

### Provider Score Dropping

Check audit history:
```bash
animica da audit results <provider_id> --limit 50
```

Common issues:
- Network connectivity
- Insufficient storage
- Corrupted blob data

### Assignment Failures

```
AssignmentError: Insufficient providers
```

Solutions:
- Register more providers
- Increase provider capacity
- Lower minimum uptime threshold
- Check for jailed providers

### Audit Failures

Common failure reasons:
- `deadline exceeded` - Provider slow to respond
- `signature invalid` - Key mismatch or corruption
- `byte-range data mismatch` - Blob data corrupted
- `no response` - Provider offline

## Future Enhancements

1. **Advanced Proofs**
   - Full Merkle tree verification
   - NMT proof verification
   - Zero-knowledge proofs

2. **Economic Incentives**
   - Reward tokens for successful audits
   - Slash stakes on failures
   - Pricing models based on uptime

3. **Network Coordination**
   - P2P challenge distribution
   - Distributed audit scheduling
   - Byzantine fault tolerance

4. **Monitoring**
   - Prometheus metrics
   - Real-time dashboards
   - Alert on critical failures

## References

- Provider Registry: `da/provider/registry.py`
- CDDL Schema: `da/schemas/provider_registry.cddl`
- AICF SLA: `aicf/sla/slash_engine.py` (scoring patterns)
- PQ Signatures: `pq/py/sign.py`, `pq/py/verify.py`

## License

See [LICENSE.txt](../../LICENSE.txt)
