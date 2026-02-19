# DA Audit System Quick Reference

## Installation

```bash
pip install -e ".[dev]"
```

## Quick Start

### 1. Register Providers

```bash
# Register a provider with 1TB capacity
animica da provider register \
  --capacity 1TB \
  --endpoint http://provider.example.com \
  --path /storage/blobs \
  --region us-west
```

### 2. Assign Blob to Providers

```python
from da.provider.assignment import assign_blob
from da.provider.registry import ProviderRegistry

registry = ProviderRegistry()
assignments = assign_blob(
    registry=registry,
    blob_commitment=blob_hash,
    size=50_000_000,  # 50 MB
    replication_factor=3,
)
```

### 3. Create Audit Challenge

```bash
animica da audit challenge \
  <provider_id> \
  <blob_commitment> \
  --challenge-type byte-range
```

### 4. Run Audit Round

```bash
animica da audit run --sample-size 10
```

### 5. Check Results

```bash
animica da audit results <provider_id> --limit 20
```

## API Examples

### Assignment

```python
from da.provider.assignment import assign_blob, get_blob_providers
from da.provider.registry import ProviderRegistry

# Initialize
registry = ProviderRegistry()

# Assign blob
assignments = assign_blob(
    registry=registry,
    blob_commitment=blob_hash,
    size=1024 * 1024,  # 1 MB
    replication_factor=3,
    min_uptime_score=5000,
)

# Get providers for blob
providers = get_blob_providers(registry, blob_hash)
```

### Audit

```python
from da.provider.audit import (
    AuditDatabase,
    create_challenge,
    verify_response,
)

# Initialize
audit_db = AuditDatabase()

# Create challenge
challenge = create_challenge(
    provider_id=provider_id,
    blob_commitment=blob_hash,
    challenge_type="byte-range",
)
audit_db.store_challenge(challenge)

# Verify response
passed, reason = verify_response(
    challenge=challenge,
    response=response,
    provider=provider,
    actual_blob_data=blob_data,
)
```

### Scheduler

```python
from da.provider.audit_scheduler import (
    AuditScheduler,
    AuditSchedulerConfig,
)

# Configure
config = AuditSchedulerConfig(
    sample_size=10,
    jail_threshold=1000,
    jail_duration_seconds=86400,
)

# Run audit round
scheduler = AuditScheduler(registry, audit_db, config)
results = scheduler.run_audit_round()

# Get stats
stats = scheduler.get_audit_stats(provider_id)
print(f"Pass rate: {stats['pass_rate'] * 100:.1f}%")
```

### Jailing

```python
from da.provider.audit_scheduler import (
    jail_provider,
    unjail_provider,
    get_jailed_providers,
)

# Jail provider
jail_provider(
    registry=registry,
    provider_id=provider_id,
    duration_seconds=86400,
    reason="Low uptime",
)

# Unjail
unjail_provider(registry, provider_id)

# List jailed
jailed = get_jailed_providers(registry)
```

## CLI Commands

### Provider Management

```bash
# Register
animica da provider register --capacity 1TB --endpoint http://... --path /storage

# Status
animica da provider status

# List all
animica da provider list --active-only

# Sync blobs
animica da provider sync --path /storage --verify
```

### Audit Operations

```bash
# Challenge
animica da audit challenge <provider_id> <blob_hash>

# Respond (for providers)
animica da audit respond <challenge_id>

# Run audit
animica da audit run --sample-size 10

# Results
animica da audit results <provider_id>

# Jail
animica da audit jail <provider_id> --duration 86400 --reason "Low uptime"
animica da audit unjail <provider_id>
animica da audit jailed
```

## Configuration

### Environment Variables

```bash
export ANIMICA_REGISTRY_DB=~/.animica/provider_registry.db
export ANIMICA_AUDIT_DB=~/.animica/audit_results.db
export ANIMICA_DA_URL=http://127.0.0.1:8648
```

### Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| `replication_factor` | 3 | Number of replicas |
| `min_uptime_score` | 5000 | Minimum score for eligibility |
| `challenge_deadline` | 3600 | Seconds to respond |
| `sample_size` | 10 | Audits per round |
| `jail_threshold` | 1000 | Jail if score below |
| `jail_duration` | 86400 | Jail duration (seconds) |
| `score_delta_pass` | +100 | Points for passing |
| `score_delta_fail` | -200 | Points for failing |

## Scoring

### Formula

```
score = uptime_score                        # 0-10000
      + hash(blob + provider) % 1000        # fairness
      + (available / advertised) * 500      # capacity
```

### Updates

- **Pass**: +100 points
- **Fail**: -200 points
- **Range**: [0, 10000]
- **Jail**: score < 1000

## Challenge Types

| Type | Description | Response |
|------|-------------|----------|
| `byte-range` | Random byte range | Byte data (hex) |
| `merkle-proof` | Merkle tree leaf | Merkle path |
| `nmt-proof` | NMT namespace | NMT proof |

## Error Handling

### Assignment Errors

```python
try:
    assignments = assign_blob(...)
except AssignmentError as e:
    print(f"Assignment failed: {e}")
    # Solutions:
    # - Register more providers
    # - Increase provider capacity
    # - Lower min_uptime_score
```

### Verification Failures

Common reasons:
- `challenge_id mismatch`
- `response submitted after deadline`
- `invalid signature`
- `byte-range data mismatch`

## Testing

```bash
# Run unit tests
python -m pytest da/tests/test_assignment.py -v
python -m pytest da/tests/test_audit.py -v
python -m pytest da/tests/test_audit_scheduler.py -v

# Run integration demo
python -c "from da.provider.assignment import assign_blob; ..."
```

## Monitoring

### Provider Health

```bash
# Check score
animica da provider status

# Audit history
animica da audit results <provider_id> --limit 50

# Jailed providers
animica da audit jailed
```

### System Health

```python
# Total capacity
total_adv, total_comm = registry.get_total_capacity()
print(f"Capacity: {total_adv} advertised, {total_comm} committed")

# Audit stats
stats = scheduler.get_audit_stats(provider_id)
print(f"Pass rate: {stats['pass_rate']:.1%}")
```

## Production Setup

### Cron Jobs

```bash
# Audit round every 5 minutes
*/5 * * * * animica da audit run --sample-size 20

# Sync blobs hourly
0 * * * * animica da provider sync --path /storage --verify

# Heartbeat every minute
* * * * * animica da provider heartbeat
```

### Systemd Service

```ini
[Unit]
Description=Animica DA Provider
After=network.target

[Service]
Type=simple
User=animica
ExecStart=/usr/bin/animica da serve --path /storage --port 8648
Restart=always

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Low Score

1. Check audit history: `animica da audit results <provider_id>`
2. Verify blob integrity: `animica da provider sync --verify`
3. Check network connectivity
4. Review logs for errors

### Assignment Failures

1. List providers: `animica da provider list`
2. Check capacity: `animica da provider status`
3. Review jailed: `animica da audit jailed`
4. Lower threshold: `min_uptime_score=4000`

### Audit Failures

1. Increase deadline: `--deadline 7200`
2. Check provider service: `curl http://provider/health`
3. Verify signatures: Check PQ key configuration
4. Review challenge params

## Best Practices

1. **Redundancy**: Use replication_factor >= 3
2. **Diversity**: Ensure providers in different regions
3. **Monitoring**: Run regular audit rounds
4. **Maintenance**: Keep provider scores > 5000
5. **Capacity**: Monitor capacity commitments
6. **Jailing**: Review jailed providers regularly
7. **Verification**: Always verify blob hashes
8. **Backups**: Keep provider keys secure

## Resources

- Full Documentation: `DA_AUDIT_IMPLEMENTATION.md`
- Source: `da/provider/`
- Tests: `da/tests/`
- CLI: `da/cli/audit.py`
- Schemas: `da/schemas/provider_registry.cddl`
