# ENA RPC Reference

## Endpoint

All RPC calls use POST to `/rpc` with `Content-Type: application/json`.

```
POST https://<node>/rpc
```

## Methods

---

### `ena.submitRequest`

Submit an ENA inference request.

**Params (object or array):**
```json
{
  "model_version": "ena-v0.9.0-h10000",
  "task_type": "classify",
  "input_hex": "0x48656c6c6f20776f726c64",
  "fee_limit": 10000,
  "creator_hex": "0xab...ab",
  "callback": "",
  "nonce": 0
}
```

**Returns:**
```json
{
  "request_id": "ena-a1b2c3d4e5f6...",
  "status": "queued",
  "input_hash": "sha3hex...",
  "fee_locked": 10000,
  "model_version": "ena-v0.9.0-h10000",
  "task_type": "classify",
  "expiry_height": 1440
}
```

**Errors:**
- `-32602` Invalid params: missing or invalid fields
- `-32603` Internal error: state module not available

---

### `ena.getRequest`

Get full details of an ENA request.

**Params:** `[request_id: string]`

**Returns:**
```json
{
  "request_id": "ena-...",
  "creator": "0x...",
  "model_version": "ena-v0.9.0-h10000",
  "task_type": "classify",
  "input_hash": "...",
  "fee_locked": 10000,
  "status": "queued",
  "created_height": 1000,
  "expiry_height": 2440
}
```

---

### `ena.getRequestStatus`

Get the status of an ENA request.

**Params:** `[request_id: string]`

**Returns:**
```json
{
  "request_id": "ena-...",
  "status": "completed"
}
```

Possible status values: `queued`, `running`, `completed`, `failed`, `expired`

---

### `ena.getResult`

Get the result record for a completed ENA request.

**Params:** `[request_id: string]`

**Returns:**
```json
{
  "request_id": "ena-...",
  "result_hash": "sha3hex...",
  "da_ptr": "da:commitment...",
  "worker_id": "provider-0x1234",
  "accepted_height": 1001,
  "status": "completed"
}
```

---

### `ena.getResultReceipt`

Get the receipt/proof metadata for a completed ENA result.

**Params:** `[request_id: string]`

**Returns:**
```json
{
  "request_id": "ena-...",
  "receipt_hash": "receipt...",
  "worker_id": "provider-0x1234",
  "accepted_height": 1001
}
```

---

### `ena.listModels`

List known ENA model versions.

**Params:** none

**Returns:**
```json
{
  "models": [
    {
      "version": "ena-v0.9.0-h10000",
      "da_ptr": "da:abc...",
      "activation_height": 10000,
      "status": "active",
      "metadata_hash": ""
    }
  ],
  "active_version": "ena-v0.9.0-h10000"
}
```

---

### `ena.getActiveModel`

Get the currently active ENA model version.

**Params:** none

**Returns:**
```json
{
  "version": "ena-v0.9.0-h10000",
  "da_ptr": "da:abc...",
  "activation_height": 10000,
  "status": "active"
}
```

---

### `ena.explainReject`

Debug method: explains why an ENA request would be rejected.

**Params (object or array):**
```json
{
  "model_version": "ena-v0.9.0-h10000",
  "task_type": "classify",
  "input_size": 512,
  "fee_limit": 10000
}
```

**Returns:**
```json
{
  "allowed": true,
  "reasons": [],
  "policy": {
    "max_input_bytes": 4096,
    "allowed_tasks": ["classify", "embed", "summarize", "custom"]
  }
}
```

---

## Example: Submit Request via curl

```bash
curl -s -X POST https://mainnet.animica.org/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "ena.submitRequest",
    "params": {
      "model_version": "ena-v0.9.0-h10000",
      "task_type": "classify",
      "input_hex": "0x496e70757420746578742068657265",
      "fee_limit": 10000
    }
  }'
```

## Error Codes

| Code   | Meaning                            |
|--------|------------------------------------|
| -32700 | Parse error                        |
| -32600 | Invalid request                    |
| -32601 | Method not found                   |
| -32602 | Invalid params (see reasons field) |
| -32603 | Internal error                     |

## See Also

- [cli.md](cli.md) — CLI wrapper for these RPC methods
- [contract-integration.md](contract-integration.md) — Contract API
