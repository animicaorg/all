# DA Storage Provider Subsystem

The storage provider subsystem enables participants to contribute disk space to the DA (Data Availability) layer and earn AICF credits for storing and serving blobs.

## Architecture

### Components

1. **Provider Registry** (`da/provider/registry.py`)
   - Manages provider registration and metadata
   - Tracks blob assignments and capacity utilization
   - Handles audit challenges and responses
   - SQLite-backed persistence with CBOR/JSON encoding

2. **Provider Service** (`da/provider/service.py`)
   - FastAPI-based HTTP service for serving blobs
   - Supports range requests for partial retrieval
   - Built-in rate limiting and optional authentication
   - Content-addressed storage organized by commitment prefix

3. **Provider CLI** (`da/cli/provider.py`)
   - Registration and status management
   - Heartbeat updates
   - Blob synchronization from DA network
   - Provider discovery and listing

4. **Serve Daemon** (`da/cli/serve.py`)
   - Standalone HTTP server for provider service
   - Production-ready with uvicorn workers
   - Configurable ports, authentication, and rate limits

## Quick Start

### 1. Register as a Provider

```bash
# Generate keypair and register
animica da provider register \
  --path /data/storage \
  --capacity 1TB \
  --endpoint https://provider.example.com:9090 \
  --region us-west,ssd
```

This will:
- Generate a PQ keypair (stored in `~/.animica/provider_key.json`)
- Create a provider ID (SHA3-256 hash of pubkey)
- Register with the network
- Initialize local storage directory

### 2. Check Provider Status

```bash
# View provider information
animica da provider status

# List all registered providers
animica da provider list --active-only
```

### 3. Start Provider Service

```bash
# Start HTTP service to serve blobs
animica da serve \
  --path /data/storage \
  --port 9090 \
  --rate-limit 100 \
  --auth-token <optional-secret>
```

The service will expose:
- `GET /blob/{commitment}` - Retrieve blob
- `HEAD /blob/{commitment}` - Check existence
- `GET /health` - Health check

### 4. Sync Assigned Blobs

```bash
# Download blobs assigned to this provider
animica da provider sync \
  --path /data/storage \
  --da-url http://da-node.example.com:8648
```

### 5. Send Heartbeats

```bash
# Update last_heartbeat timestamp (run periodically)
animica da provider heartbeat
```

## Data Structures

### ProviderEntry

Provider registration entry with identity, capacity, and status:

```python
@dataclass
class ProviderEntry:
    provider_id: bytes        # 32-byte SHA3-256(pubkey)
    pubkey: bytes             # Post-quantum public key
    address: bytes            # 20-byte payment address
    endpoint: str             # HTTP(S) URL
    capacity_bytes_advertised: int
    capacity_bytes_committed: int
    region_tags: List[str]
    uptime_score: int         # 0-10000 (0% to 100%)
    last_heartbeat: int
    registered_at: int
    active: bool
```

### BlobAssignment

Tracks blob-to-provider assignments:

```python
@dataclass
class BlobAssignment:
    blob_commitment: bytes
    provider_id: bytes
    assigned_at: int
    replicas: int             # Replication factor
    blob_size: int
```

### AuditChallenge

Challenge sent to providers to prove storage:

```python
@dataclass
class AuditChallenge:
    challenge_id: bytes
    provider_id: bytes
    blob_commitment: bytes
    nonce: bytes
    challenge_type: str       # "byte-range", "merkle-proof", "nmt-proof"
    params: Dict              # Challenge-specific parameters
    created_at: int
    deadline: int
```

## Storage Organization

Blobs are stored on disk using content-addressed organization:

```
/data/storage/
├── 0000/
│   ├── 0000abc...def.blob
│   └── 0000123...456.blob
├── 0001/
│   └── 0001fed...cba.blob
└── ffff/
    └── ffff999...888.blob
```

- First 4 hex chars of commitment used as directory prefix
- Provides efficient lookup and reduces directory size

## Security

### Authentication

Optional bearer token authentication:

```bash
animica da serve --auth-token <secret>
```

Clients must include header:
```
Authorization: Bearer <secret>
```

### Rate Limiting

Default: 100 requests per second per client IP

```bash
animica da serve --rate-limit 200
```

### Provider ID Generation

Provider IDs are derived deterministically:

```python
provider_id = SHA3-256(pubkey)
```

This prevents spoofing and ties identity to cryptographic keys.

## Database Schema

### Providers Table

```sql
CREATE TABLE providers (
    provider_id BLOB PRIMARY KEY,
    pubkey BLOB NOT NULL,
    address BLOB NOT NULL,
    endpoint TEXT,
    capacity_bytes_advertised INTEGER NOT NULL,
    capacity_bytes_committed INTEGER NOT NULL,
    pricing TEXT,
    region_tags TEXT,
    uptime_score INTEGER NOT NULL,
    last_heartbeat INTEGER NOT NULL,
    registered_at INTEGER NOT NULL,
    active INTEGER NOT NULL,
    jailed_until INTEGER,
    notes TEXT,
    cbor_data BLOB NOT NULL
);
```

### Blob Assignments Table

```sql
CREATE TABLE blob_assignments (
    blob_commitment BLOB NOT NULL,
    provider_id BLOB NOT NULL,
    assigned_at INTEGER NOT NULL,
    replicas INTEGER NOT NULL,
    blob_size INTEGER NOT NULL,
    cbor_data BLOB NOT NULL,
    PRIMARY KEY (blob_commitment, provider_id)
);

CREATE INDEX idx_assignments_provider ON blob_assignments(provider_id);
```

## Configuration

### Environment Variables

- `ANIMICA_DA_PROVIDER_DB` - Registry database path (default: `~/.animica/provider_registry.db`)
- `ANIMICA_DA_PROVIDER_KEYSTORE` - Keypair storage path (default: `~/.animica/provider_key.json`)

### Defaults

- **Replication Factor**: 3 copies per blob
- **Initial Uptime Score**: 5000 (50%)
- **Rate Limit**: 100 req/s
- **HTTP Port**: 9090

## Integration with AICF

Storage providers earn AICF credits based on:
1. **Capacity provided** - Credits per GB-month
2. **Uptime score** - Reliability multiplier
3. **Successful audits** - Proof-of-storage rewards

Credits can be used to:
- Submit AI/Quantum jobs
- Purchase additional storage
- Trade on marketplace

## API Examples

### Register Provider (Python)

```python
from da.provider.registry import (
    ProviderRegistry,
    register_provider,
)

registry = ProviderRegistry()
entry = register_provider(
    registry=registry,
    pubkey=my_pubkey,
    address=my_address,
    endpoint="https://provider.example.com:9090",
    capacity_bytes=1_000_000_000_000,  # 1TB
    region_tags=["us-west", "ssd"],
)
```

### Retrieve Blob (HTTP)

```bash
# Full retrieval
curl -H "Authorization: Bearer <token>" \
  https://provider.example.com:9090/blob/<commitment>

# Partial retrieval (bytes 1000-2000)
curl -H "Authorization: Bearer <token>" \
  -H "Range: bytes=1000-2000" \
  https://provider.example.com:9090/blob/<commitment>

# Check existence
curl -I -H "Authorization: Bearer <token>" \
  https://provider.example.com:9090/blob/<commitment>
```

### Start Service (Python)

```python
from da.provider.service import ProviderService
import uvicorn

service = ProviderService(
    storage_path="/data/storage",
    rate_limit_rps=100,
    auth_token="my_secret_token",
)

uvicorn.run(service.app, host="0.0.0.0", port=9090)
```

## Testing

Run the test suite:

```bash
# Unit tests
pytest da/tests/test_provider_registry.py -v
pytest da/tests/test_provider_service.py -v

# Integration test
python -m da.cli.provider --help
python -m da.cli.serve --help
```

## Future Work

1. **Audit System** - Automated proof-of-storage challenges
2. **Reputation** - Uptime tracking and slashing for failures
3. **Payment Integration** - Automatic credit distribution
4. **Discovery** - DHT-based provider lookup
5. **Erasure Coding** - Efficient redundancy with Reed-Solomon
6. **Bandwidth Optimization** - Multicast and P2P distribution

## References

- CDDL Schema: `da/schemas/provider_registry.cddl`
- AICF Integration: `aicf/registry/`
- DA Layer Spec: `da/specs/`
