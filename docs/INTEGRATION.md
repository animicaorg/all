# Animica Integration Contract

**Version:** 1.0.0  
**Status:** Active  
**Scope:** DA ↔ ENA ↔ AICF ↔ Mining end-to-end wiring

---

## 1. Artifact Types and Lifecycle

### 1.1 ENA Artifacts

ENA (Embedded Neural Agent) jobs produce one or more *artifacts*:

| Kind | Examples |
|---|---|
| `dataset_shard` | Tokenised training shards, eval splits |
| `eval_metrics` | JSON metrics files (loss, accuracy, BLEU, …) |
| `checkpoint` | Model weight files |
| `inference_result` | JSON/CBOR result payload |
| `manifest` | The manifest itself (self-referential) |

### 1.2 Artifact Lifecycle

```
ENA job runs
    │
    ├─ for each output file:
    │       da.put(bytes) → blob_id
    │
    ├─ build ArtifactManifest {
    │       job_id, model_id, dataset_id,
    │       config_hash, produced_files: [{name, blob_id, size, sha256}]
    │   }
    │
    ├─ da.put(manifest_bytes) → manifest_blob_id
    │
    ├─ ena.submitArtifact(manifest_blob_id, job_metadata)
    │       → stores pending artifact record
    │
    ├─ ena.verifyArtifact(manifest_blob_id)
    │       → da.get(manifest_blob_id) → parse manifest
    │       → for each file: da.has(blob_id) + hash check
    │       → produces VerificationResult {ok, missing, errors}
    │
    └─ on ok: AICF credit event created and persisted
```

---

## 2. Identifiers and Canonical Formats

### 2.1 Blob ID / Commitment (DA)

```
blob_id = hex( sha3_256( raw_blob_bytes ) )
```

- Always lowercase hex, no `0x` prefix, 64 characters.
- Produced and validated by `da.node_store`.
- Example: `"3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b"`

### 2.2 Artifact Manifest Schema (ENA)

Canonical JSON (keys sorted, no trailing whitespace):

```jsonc
{
  "version": 1,
  "job_id": "<string>",               // ENA job identifier
  "model_id": "<string|null>",        // model name/hash
  "dataset_id": "<string|null>",      // dataset name/hash
  "config_hash": "<hex64>",           // sha3-256 of serialised job config
  "produced_files": [
    {
      "name": "<filename>",
      "blob_id": "<hex64>",           // da blob_id
      "size": <int>,                  // bytes
      "sha256": "<hex64>"             // sha-256 of file bytes (redundant check)
    }
  ],
  "created_at": <unix_seconds_float>,
  "node_id": "<string|null>"          // verifier node identity
}
```

### 2.3 Credit Event Schema (AICF)

```jsonc
{
  "event_id": "<hex64>",              // sha3-256 of deterministic payload
  "event_type": "artifact_verified",
  "manifest_blob_id": "<hex64>",
  "verifier_address": "<hex>",        // node/verifier address
  "score": <float>,                   // psi(p) weight
  "created_at": <unix_seconds_float>,
  "idempotency_key": "<hex64>"        // sha3-256(manifest_blob_id + verifier_address)
}
```

### 2.4 Transaction / Receipt Schema

Unchanged from existing `core/types/tx.py` and `core/types/receipt.py`.  
Useful-work artifacts are carried in the block *header* extra fields, not in
transaction payloads, so the base tx/receipt schema is unaffected.

---

## 3. Verification and Accounting Rules

### 3.1 When does an artifact earn AICF credits?

An artifact earns credits when **all** of the following hold:

1. `manifest_blob_id` resolves in the local DA store (`da.has` returns `true`).
2. The manifest parses as a valid `ArtifactManifest` (schema v1).
3. Every `blob_id` referenced by `produced_files` exists in the local DA store.
4. The `sha256` of each file's bytes matches the manifest entry.
5. The `idempotency_key` (`sha3-256(manifest_blob_id ‖ verifier_address)`) has not
   been used before in the AICF credit ledger.

### 3.2 Who verifies?

- **Primary:** the local node, via `ena.verifyArtifact`.
- **Optional quorum:** peers may independently call `ena.verifyArtifact` and
  submit their own credit events; the ledger deduplicates by `idempotency_key`.

### 3.3 What is recorded on-chain vs off-chain?

| Data | Storage |
|---|---|
| Artifact bytes | DA (off-chain, content-addressed) |
| Manifest bytes | DA (off-chain, content-addressed) |
| `manifest_blob_id` commitment | Block header `usefulWorkPayload` (on-chain) |
| Credit events | AICF ledger DB (off-chain, node-local) |
| Credit event Merkle root | Block header `creditEventRoot` (on-chain) |

### 3.4 Minimum metadata to prevent spoofing

The manifest **must** include:

- `job_id` — links the artifact to a specific ENA job run.
- `config_hash` — prevents manifest replay from a different config.
- `produced_files[*].sha256` — content integrity independent of DA blob_id.
- `node_id` — attributes the artifact to a specific node identity.

---

## 4. System Flows

### 4.1 Full DA → ENA → AICF → Mining Flow

```
Node A                   DA Store              AICF Ledger          Block
─────────────────────────────────────────────────────────────────────────
ENA job completes
  │
  ├─ da.put(file_bytes) ──────────> blob_id_1
  ├─ da.put(manifest)  ──────────> manifest_blob_id
  │
  ├─ ena.submitArtifact(manifest_blob_id)
  │       stores pending
  │
  ├─ ena.verifyArtifact(manifest_blob_id)
  │       da.get(manifest_blob_id) <───────── bytes
  │       da.has(blob_id_1) ──────────────── true
  │       hash check ✓
  │       → VerificationResult {ok: true}
  │       → aicf.logCreditEvent(...)  ──────> credit record
  │
miner.getBlockTemplate(include_aicf=true)
  │       queries verified artifacts
  │       da.has(manifest_blob_id) ✓ (preflight)
  │       → template.usefulWorkPayload = [manifest_blob_id]
  │       → template.creditEventRoot = merkle(credit_events)
  │
miner submits block ────────────────────────────────────> block with
                                                          usefulWorkPayload
```

### 4.2 Peer Sync Flow

```
Node B receives block referencing manifest_blob_id
  │
  ├─ da.has(manifest_blob_id) → false
  ├─ fetch manifest_blob_id from Node A via P2P
  ├─ da.put(manifest_bytes) locally
  ├─ verify referenced blobs are fetchable
  └─ block accepted
```

---

## 5. RPC API Reference

### DA

| Method | Description |
|---|---|
| `da.status` | Node DA status |
| `da.configure` | Configure DA dir/quota |
| `da.put` | Store a blob, returns `blob_id` |
| `da.get` | Retrieve blob bytes |
| `da.has` | Check blob existence |
| `da.list` | List stored blobs |
| `da.prune` | Evict expired/LRU blobs |

### ENA

| Method | Description |
|---|---|
| `ena.submitArtifact` | Submit a manifest blob for credit processing |
| `ena.verifyArtifact` | Verify artifact and optionally award credits |
| `ena.submitRequest` | Submit an ENA inference request |
| `ena.getRequest` | Get request details |
| `ena.getRequestStatus` | Get request status |
| `ena.getResult` | Get result record |
| `ena.listModels` | List known models |

### AICF

| Method | Description |
|---|---|
| `aicf.status` | AICF pool status |
| `aicf.summary` | Summary of credits and events |
| `aicf.creditsByAddress` | Credits for a given address |
| `aicf.recentEvents` | Recent credit events |
| `aicf.getParams` | AICF configuration parameters |
| `aicf.getClaimable` | Claimable rewards for an address |

### Mining

| Method | Description |
|---|---|
| `miner.getBlockTemplate` | Block template; pass `include_aicf=true` for useful-work payload |
| `miner.status` | Miner status |
| `miner.start` / `miner.stop` | Start/stop auto-mining |

---

## 6. Explorer2 API Reference

| Path | Description |
|---|---|
| `GET /api/da/status` | DA status |
| `GET /api/da/blob/:id` | Blob metadata (no bytes) |
| `GET /api/da/recent` | Recent blobs |
| `GET /api/aicf/summary` | AICF summary |
| `GET /api/aicf/events` | Recent credit events |
| `GET /api/aicf/address/:addr` | Credits for address |
| `GET /api/ena/jobs` | Recent ENA jobs |
| `GET /api/ena/artifacts` | Recent artifact manifests |
| `GET /api/ena/job/:id` | Specific job details |

---

## 7. CLI Reference

```bash
# DA
animica da status --json
animica da configure --dir PATH --quota SIZE
animica da put FILE [--json]
animica da get BLOB_ID [--out FILE]
animica da has BLOB_ID [--json]
animica da list [--json]
animica da prune [--json]

# ENA
animica ena artifact submit MANIFEST_BLOB_ID [--json]
animica ena artifact verify MANIFEST_BLOB_ID [--json]
animica ena job run [--json]
animica ena job status JOB_ID [--json]

# AICF
animica aicf status [--json]
animica aicf summary [--json]
animica aicf recent [--limit N] [--json]
animica aicf credits ADDRESS [--json]

# Mining
animica miner mine-blocks [--include-aicf] [--json]
```

---

## 8. Idempotency and Safety Guarantees

- `da.put` is idempotent: storing the same bytes twice returns the same `blob_id`.
- `ena.submitArtifact` is idempotent: submitting the same `manifest_blob_id` twice
  creates only one pending record.
- `ena.verifyArtifact` is idempotent: the credit event `idempotency_key` prevents
  double-crediting. Re-verification is safe.
- Mining preflight: `miner.getBlockTemplate` with `include_aicf=true` only includes
  manifests for which `da.has(manifest_blob_id)` is `true` locally.
