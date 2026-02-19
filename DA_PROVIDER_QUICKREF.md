# DA Provider Quick Reference

## Installation

No additional dependencies required for basic functionality. Optional:
```bash
pip install cbor2         # For CBOR serialization (preferred)
pip install fastapi       # For provider service
pip install 'uvicorn[standard]'  # For serve daemon
pip install typer rich    # For CLI commands
```

## Quick Start (5 minutes)

### 1. Register as Provider

```bash
# Register with 1TB capacity
animica da provider register \
  --path /data/storage \
  --capacity 1TB \
  --endpoint https://provider.example.com:9090 \
  --region us-west,ssd

# Output:
# ✓ Provider registered successfully
# Provider ID: d86121ff9698f95106cb51b7ba5c4df9...
# Endpoint: https://provider.example.com:9090
# Capacity: 1,000,000,000,000 bytes
```

### 2. Start Provider Service

```bash
# Start HTTP service on port 9090
animica da serve \
  --path /data/storage \
  --port 9090 \
  --rate-limit 100

# Service runs at http://0.0.0.0:9090
# Endpoints:
#   GET  /blob/{commitment}
#   HEAD /blob/{commitment}
#   GET  /health
```

### 3. Check Status

```bash
# View your provider status
animica da provider status

# List all providers
animica da provider list

# List only active providers
animica da provider list --active-only
```

### 4. Sync Blobs

```bash
# Download assigned blobs from DA network
animica da provider sync \
  --path /data/storage \
  --da-url http://da-node.example.com:8648
```

### 5. Send Heartbeat

```bash
# Update last_heartbeat (run periodically)
animica da provider heartbeat
```

## CLI Command Reference

### `provider register`

Register as a storage provider.

```bash
animica da provider register \
  --path <storage_path>       # Required: Local storage directory
  --capacity <size>           # Required: Total capacity (e.g., "100GB", "1TB")
  --endpoint <url>            # Required: HTTP(S) endpoint URL
  [--address <hex>]           # Optional: 20-byte payment address
  [--region <tags>]           # Optional: Comma-separated region tags
  [--db <path>]               # Optional: Registry DB path
  [--keystore <path>]         # Optional: Keypair storage path
  [--json]                    # Optional: JSON output
```

**Example**:
```bash
animica da provider register \
  --path /mnt/storage \
  --capacity 2TB \
  --endpoint https://us-west.provider.com:9090 \
  --region "us-west,ssd,low-latency"
```

### `provider status`

Show provider status and capacity.

```bash
animica da provider status \
  [--db <path>]               # Optional: Registry DB path
  [--keystore <path>]         # Optional: Keypair storage path
  [--json]                    # Optional: JSON output
```

**Output**:
```
Provider ID      : d86121ff9698f95106cb51b7ba5c4df9...
Endpoint         : https://provider.example.com:9090
Capacity (Adv)   : 1,000,000,000,000 bytes
Capacity (Comm)  : 45,000,000 bytes
Capacity (Avail) : 999,955,000,000 bytes
Uptime Score     : 5000/10000 (50.00%)
Last Heartbeat   : 2024-01-15 10:30:45
Active           : Yes
```

### `provider heartbeat`

Update last_heartbeat timestamp.

```bash
animica da provider heartbeat \
  [--db <path>]               # Optional: Registry DB path
  [--keystore <path>]         # Optional: Keypair storage path
```

**Output**:
```
✓ Heartbeat updated at 2024-01-15 10:35:12
```

### `provider list`

List all registered providers.

```bash
animica da provider list \
  [--db <path>]               # Optional: Registry DB path
  [--active-only]             # Optional: Show only active providers
  [--json]                    # Optional: JSON output
```

**Output**:
```
Provider ID       Endpoint                          Capacity  Uptime  Active
d86121ff...       https://us-west.provider.com:9090 1TB       50.0%   ✓
a1b2c3d4...       https://eu-cent.provider.com:9090 500GB     75.2%   ✓

Total providers: 2
Total capacity: 1.5TB advertised, 0.05TB committed
```

### `provider sync`

Sync assigned blobs from DA network.

```bash
animica da provider sync \
  --path <storage_path>       # Required: Local storage directory
  [--da-url <url>]            # Optional: DA service URL (default: http://127.0.0.1:8648)
  [--db <path>]               # Optional: Registry DB path
  [--keystore <path>]         # Optional: Keypair storage path
```

**Output**:
```
Found 15 blob assignment(s)
✓ Synced 0000abc...def
✓ Synced 1111fed...cba
✗ Failed to sync 2222bad...123: Connection timeout

Sync complete: 13 synced, 1 skipped, 1 errors
```

### `da serve`

Start provider service daemon.

```bash
animica da serve \
  --path <storage_path>       # Required: Local storage directory
  [--port <port>]             # Optional: HTTP port (default: 9090)
  [--host <host>]             # Optional: Bind host (default: 0.0.0.0)
  [--rate-limit <rps>]        # Optional: Requests per second (default: 100)
  [--auth-token <token>]      # Optional: Bearer token for auth
  [--workers <n>]             # Optional: Number of workers (default: 1)
  [--reload]                  # Optional: Enable auto-reload (dev mode)
```

**Example**:
```bash
# Production deployment
animica da serve \
  --path /mnt/storage \
  --port 9090 \
  --workers 4 \
  --rate-limit 200 \
  --auth-token "my-secret-token-12345"
```

## Python API Reference

### Provider Registry

```python
from da.provider.registry import (
    ProviderRegistry,
    create_provider_entry,
    create_provider_id,
    register_provider,
)

# Create registry
registry = ProviderRegistry(db_path="~/.animica/provider_registry.db")

# Create provider entry
entry = create_provider_entry(
    pubkey=my_pubkey,                    # bytes (Dilithium3)
    address=my_address,                  # bytes (20-byte)
    endpoint="https://provider.com:9090",
    capacity_bytes=1_000_000_000_000,    # 1TB
    region_tags=["us-west", "ssd"],
)

# Register
registry.register_provider(entry)

# Get provider
provider_id = create_provider_id(my_pubkey)
retrieved = registry.get_provider(provider_id)

# List all providers
providers = registry.list_providers(active_only=True)

# Update heartbeat
registry.update_heartbeat(provider_id, int(time.time()))

# Get total capacity
total_adv, total_comm = registry.get_total_capacity()
```

### Provider Service

```python
from da.provider.service import ProviderService
import uvicorn

# Create service
service = ProviderService(
    storage_path="/mnt/storage",
    rate_limit_rps=100,
    auth_token="optional-secret",
)

# Store blob
commitment = hashlib.sha3_256(blob_data).digest()
blob_path = service.store_blob(commitment, blob_data)

# Get blob
retrieved = service.get_blob(commitment)

# Check existence
exists = service.has_blob(commitment)

# Run service
uvicorn.run(service.app, host="0.0.0.0", port=9090, workers=4)
```

## HTTP API Reference

### GET /blob/{commitment}

Retrieve blob by commitment.

**Request**:
```http
GET /blob/0xd86121ff9698f95106cb51b7ba5c4df9abcd1234... HTTP/1.1
Host: provider.example.com:9090
Authorization: Bearer <token>
```

**Response**:
```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Length: 4096

<blob data>
```

**With Range**:
```http
GET /blob/0xd86121... HTTP/1.1
Range: bytes=1000-2000
```

```http
HTTP/1.1 206 Partial Content
Content-Range: bytes 1000-2000/4096
Content-Length: 1001

<partial blob data>
```

### HEAD /blob/{commitment}

Check if blob exists.

**Request**:
```http
HEAD /blob/0xd86121... HTTP/1.1
Host: provider.example.com:9090
```

**Response**:
```http
HTTP/1.1 200 OK
Content-Length: 4096
Content-Type: application/octet-stream
```

### GET /health

Health check.

**Request**:
```http
GET /health HTTP/1.1
```

**Response**:
```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status": "ok"}
```

## Error Codes

- **400** - Invalid commitment format
- **401** - Unauthorized (missing/invalid token)
- **404** - Blob not found
- **416** - Range not satisfiable
- **429** - Too many requests (rate limited)

## Storage Layout

```
/mnt/storage/
├── 0000/
│   ├── 0000abc123def456...789.blob
│   └── 0000fed987cba654...321.blob
├── 0001/
│   └── 0001123456789abc...def.blob
...
└── ffff/
    └── ffff987654321fed...cba.blob
```

- First 4 hex chars of commitment = directory prefix
- Reduces directory entry count
- Enables efficient lookup

## Configuration Files

### Registry DB
- Default: `~/.animica/provider_registry.db`
- SQLite database with providers and assignments

### Keystore
- Default: `~/.animica/provider_key.json`
- JSON file with pubkey and privkey (hex-encoded)
- **Keep secure!**

```json
{
  "pubkey": "d86121ff...",
  "privkey": "9a8b7c6d..."
}
```

## Monitoring

### Check Service Health

```bash
curl http://localhost:9090/health
# {"status": "ok"}
```

### Monitor Capacity

```bash
animica da provider status --json | jq '.capacity_available'
# 999955000000
```

### List Active Providers

```bash
animica da provider list --active-only --json | jq 'length'
# 5
```

## Troubleshooting

### "Provider not registered"

Run `animica da provider register` first.

### "FastAPI not available"

Install: `pip install fastapi 'uvicorn[standard]'`

### "Blob not found"

Run `animica da provider sync` to download assigned blobs.

### Rate limited (429)

Increase `--rate-limit` or reduce request frequency.

### Authentication failed (401)

Include `Authorization: Bearer <token>` header.

## Best Practices

1. **Run heartbeat regularly** - Every 5-10 minutes
2. **Monitor uptime score** - Maintain >80% for good reputation
3. **Sync periodically** - Check for new assignments every hour
4. **Use authentication** - Always set `--auth-token` in production
5. **Configure rate limits** - Match your bandwidth capacity
6. **Backup keystore** - Keep `provider_key.json` secure and backed up

## Support

- Documentation: `da/provider/README.md`
- Examples: `da/provider/example_usage.py`
- Schema: `da/schemas/provider_registry.cddl`
- Issues: File on GitHub with `[DA Provider]` prefix
