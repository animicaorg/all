# Node-Side Data Availability (DA) Store

This document describes the **node-side DA store** — the subsystem that lets Animica nodes
store and serve blobs locally, enforce storage quotas, and expose blob operations via both
JSON-RPC and the `animica da` CLI.

## Overview

The node-side DA store is a content-addressed blob store backed by the local filesystem and
a SQLite index.  It is distinct from the higher-level DA layer (NMT trees, erasure coding,
data-availability sampling) and is designed for direct, operational use by node operators.

### What it provides

- **Store blobs** locally with atomic writes (tmp → fsync → rename)
- **Retrieve blobs** by content-addressed ID (`blob_id`)
- **Enforce quotas** — hard cap on total disk usage with LRU eviction or rejection
- **Persist configuration** in `<dir>/config.json` across restarts
- **Paginate** through stored blobs
- **Garbage-collect** old or oversized blobs
- **RPC surface** for Studio/Explorer and remote clients
- **CLI commands** (`animica da …`) for operators

---

## Storage Engine Choice

**SQLite** (WAL mode) is used as the metadata index.  Rationale:

- Already present in the Animica node stack (no new dependencies)
- WAL mode allows concurrent reads alongside writes without exclusive locks
- Sufficient for the expected blob counts (millions at most before archiving)
- ACID transactions ensure the index is always consistent with the filesystem

Blob content is stored as **sharded files** on the local filesystem:

```
<dir>/
  config.json          – persistent configuration (JSON)
  blobs/
    <ab>/
      <cd>/
        <blob_id>.blob  – raw blob bytes (content-addressed)
  index.sqlite          – SQLite WAL (metadata, LRU tracking)
  tmp/                  – staging area for atomic writes
```

---

## Blob ID and Commitment

**`blob_id`** = hex-encoded **SHA3-256** digest of the raw blob bytes.

SHA3-256 is from the Python standard library (`hashlib.sha3_256`), requires no extra
packages, and provides 256-bit collision resistance.  The blob ID is both the commitment
and the storage key.

```python
import hashlib
blob_id = hashlib.sha3_256(raw_bytes).hexdigest()  # 64 hex chars
```

> If BLAKE3 is required for cross-layer compatibility in future, the ID scheme can be
> versioned (e.g., `sha3:<hex>` vs `blake3:<hex>`) without breaking the on-disk layout.

---

## Configuration

The DA store is **disabled by default** and must be explicitly enabled.  Configuration is
persisted in `<dir>/config.json` and survives node restarts.

### Config fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable/disable the store |
| `dir` | string | `~/.animica/chain-<id>/da` | Store root directory |
| `max_bytes` | int | `10737418240` (10 GiB) | Hard storage cap (0 = unlimited) |
| `eviction_policy` | string | `"lru"` | Only `"lru"` is supported |
| `on_full` | string | `"evict"` | `"evict"` or `"reject"` |
| `allow_remote_get` | bool | `true` | Allow peers to fetch blobs via P2P |
| `allow_remote_put` | bool | `false` | Allow peers to upload blobs (disabled by default) |

### Security model

- **`allow_remote_put = false` by default** — accepting arbitrary uploads from untrusted
  peers would exhaust disk space.  Only enable if you have a trusted peer network and
  explicit rate limits in place.
- **`allow_remote_get = true` by default** — serving blobs to peers is low-risk since
  blobs are already public once stored.  Disable if you want a private store.
- The store root directory should be on a dedicated partition to prevent DA writes from
  filling the OS or chain data partition.

---

## Quota and Eviction

When `max_bytes > 0`, the store enforces a hard cap:

- **`on_full = "evict"`** (default): Before storing a new blob that would exceed the cap,
  the store evicts the least-recently-used (LRU) unpinned blobs until enough space is freed.
- **`on_full = "reject"`**: Refuse new blobs that would exceed the cap with an error.

LRU is tracked via `last_accessed_at` in the SQLite index, updated on every `get`.

---

## CLI Usage

All `animica da` commands call the node's JSON-RPC endpoint.

### Configure the store

```bash
# Enable DA with a 10 GB quota at a custom path
animica da configure --enabled --dir ~/.animica/da_store --max-gb 10

# Disable DA
animica da configure --disabled

# Set reject policy instead of eviction
animica da configure --on-full reject

# Allow remote peers to push blobs (use with caution)
animica da configure --allow-remote-put
```

### Check status

```bash
animica da status
animica da status --json
```

### Store a blob

```bash
animica da put --file ./sample.bin
animica da put --file ./sample.bin --json
```

Output includes the `blob_id` which is used for all subsequent operations.

### Retrieve a blob

```bash
animica da get --id <blob_id> --out ./output.bin
```

### Check existence

```bash
animica da has --id <blob_id>
# Exits 0 if present, 1 if not found
```

### List stored blobs

```bash
animica da list
animica da list --limit 20 --json
animica da list --order lru     # least-recently-used first
```

### Delete a blob

```bash
animica da delete --id <blob_id>
```

### Prune (garbage-collect)

```bash
# Free at least 2 GB via LRU eviction
animica da prune --target-gb 2

# Remove blobs older than 24 hours (86400 seconds)
animica da prune --older-than 86400

# Combine: evict old blobs AND ensure at least 1 GB freed
animica da prune --target-gb 1 --older-than 3600
```

---

## RPC Methods

All methods are available under the `da.*` namespace.

| Method | Description |
|--------|-------------|
| `da.status` | Current store status (enabled, used_bytes, blob_count, etc.) |
| `da.configure` | Configure the store (enables, sets quota, etc.) |
| `da.put` | Ingest a blob (base64 bytes) — returns `blob_id` |
| `da.get` | Retrieve a blob by `blob_id` — returns base64 bytes |
| `da.has` | Check if a `blob_id` exists — returns `{exists: bool}` |
| `da.list` | Paginated list of blobs |
| `da.delete` | Delete a blob by `blob_id` |
| `da.gc` / `da.prune` | Garbage-collect by target bytes or age |

### Example: `da.status` response

```json
{
  "enabled": true,
  "dir": "/home/user/.animica/chain-1/da",
  "max_bytes": 10737418240,
  "used_bytes": 104857600,
  "free_bytes_fs": 50000000000,
  "blob_count": 42,
  "last_error": null,
  "peer_serving": true,
  "allow_remote_get": true,
  "allow_remote_put": false,
  "eviction_policy": "lru",
  "on_full": "evict",
  "version": "1.0.0"
}
```

### Example: `da.put`

```json
// Request
{"jsonrpc":"2.0","id":1,"method":"da.put","params":{"bytes":"<base64>"}}

// Response
{"jsonrpc":"2.0","id":1,"result":{"blob_id":"<64-char-hex>","size_bytes":1234}}
```

### Example: `da.get`

```json
// Request
{"jsonrpc":"2.0","id":1,"method":"da.get","params":{"blob_id":"<64-char-hex>"}}

// Response
{"jsonrpc":"2.0","id":1,"result":{"blob_id":"...","bytes":"<base64>","size_bytes":1234,"metadata":{}}}
```

---

## Persistence and Restarts

The SQLite index and `config.json` are durable across node restarts:

1. Blob files are written atomically (written to `tmp/`, fsynced, renamed)
2. The SQLite index is updated in a transaction after the file is safely on disk
3. `config.json` is also written atomically (write to `.tmp`, rename)

On node restart, the store re-opens the SQLite index and reads `config.json` from disk —
all previously stored blobs are immediately available.

---

## AICF Integration Hooks (Future)

The `owner` field in blob metadata is reserved for AICF accounting.  When AICF settles
a job that produced a blob, it can tag the blob with the provider's address:

```python
store.put(data, owner="anim1provider...", metadata={"job_id": "..."})
```

This is a placeholder; billing/quota integration with AICF is not yet implemented.

---

## P2P Serving

When `allow_remote_get = true`, the node will respond to P2P `da.has_request` and
`da.get_request` messages from peers.  Rate limiting and concurrency caps apply.

`allow_remote_put` (default `false`) controls whether peers can push blobs to this node.
It should only be enabled in trusted environments with known peers.
