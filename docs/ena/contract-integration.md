# ENA Contract Integration Guide

## Prerequisites

- Familiarity with Animica Python VM contracts
- Understanding of the ENA [asynchronous oracle pattern](overview.md)
- A registered, active ENA model version on your target chain

---

## Available Functions

```python
from stdlib import ena

# Submit a request (async — does NOT run inference inline)
request_id = ena.request(
    model_version,    # str: required, e.g. "ena-v0.9.0-h10000"
    task_type,        # str: "classify" | "embed" | "summarize" | "custom"
    input_payload,    # bytes: raw input (max 4096 bytes by default)
    fee_limit,        # int: max ANM nano-units to spend
    callback="",      # str: optional method to call on completion
    nonce=0,          # int: extra nonce for uniqueness
) -> str              # returns request_id

# Poll status (read-only, deterministic)
status = ena.get_status(request_id)
# returns: "queued" | "running" | "completed" | "failed" | "expired" | ""

# Get result hash (deterministic, consensus-safe)
result_hash = ena.get_result_hash(request_id)
# returns: 64-char hex SHA3-256 hash, or "" if not complete

# Read inline result (only if small enough to store on-chain)
output = ena.read_result(request_id)
# returns: bytes | None

# Get DA pointer for large results
da_ptr = ena.get_da_ptr(request_id)
# returns: DA commitment string, or "" if not applicable

# Verify a worker receipt (structural validation)
valid, reason = ena.verify_receipt(request_id, result_hash, receipt_hash, worker_id)
# returns: (bool, str)
```

---

## Pattern 1: Simple Classification Request

```python
"""
Contract: Simple ENA classification.

Submits a text classification request and stores the request_id.
The result can be read in a subsequent transaction.
"""

from stdlib import ena, storage, events, abi

# Configuration
MODEL_VERSION = b"ena-v0.9.0-h10000"
CLASSIFY_TASK = b"classify"
FEE_LIMIT = 10000  # 10,000 ANM nano-units


def classify(text: bytes) -> bytes:
    """
    Submit an ENA classify request for the given text.
    
    Returns the request_id for later result retrieval.
    """
    if len(text) == 0 or len(text) > 4000:
        abi.revert(b"invalid_text_length")
    
    request_id = ena.request(
        model_version=MODEL_VERSION.decode(),
        task_type=CLASSIFY_TASK.decode(),
        input_payload=text,
        fee_limit=FEE_LIMIT,
    )
    
    # Store request_id for result retrieval
    storage.set(b"last_request_id", request_id.encode())
    
    events.emit(b"ClassifyRequested", {
        b"request_id": request_id.encode(),
        b"text_len": len(text),
    })
    
    return request_id.encode()


def get_classify_result() -> tuple:
    """
    Read the classification result.
    
    Returns (status: str, result_hash: str) tuple.
    Call only after the off-chain worker has submitted the result.
    """
    raw_id = storage.get(b"last_request_id")
    if not raw_id:
        return "not_found", ""
    
    request_id = raw_id.decode()
    status = ena.get_status(request_id)
    
    if status == "completed":
        result_hash = ena.get_result_hash(request_id)
        return status, result_hash
    
    return status, ""
```

---

## Pattern 2: Embedding Request with Result Hash Storage

```python
"""
Contract: ENA embedding request.

Stores the result hash on-chain for later verification.
Large embeddings are stored in the DA layer.
"""

from stdlib import ena, storage, events, hash as stdlib_hash, abi

MODEL_VERSION = "ena-v0.9.0-h10000"
EMBED_TASK = "embed"
FEE_LIMIT = 5000


def store_embedding(text: bytes, embedding_key: bytes) -> bytes:
    """
    Submit an ENA embedding request and associate it with a key.
    """
    if not embedding_key or len(embedding_key) > 64:
        abi.revert(b"invalid_key")
    if not text or len(text) > 4000:
        abi.revert(b"invalid_text")
    
    request_id = ena.request(
        model_version=MODEL_VERSION,
        task_type=EMBED_TASK,
        input_payload=text,
        fee_limit=FEE_LIMIT,
    )
    
    # Associate key → request_id
    storage.set(b"embed_req:" + embedding_key, request_id.encode())
    
    return request_id.encode()


def verify_embedding(embedding_key: bytes, claimed_hash: bytes) -> bool:
    """
    Verify that the committed embedding hash matches the claimed hash.
    
    Uses the deterministic result_hash stored on-chain.
    """
    raw_id = storage.get(b"embed_req:" + embedding_key)
    if not raw_id:
        return False
    
    request_id = raw_id.decode()
    status = ena.get_status(request_id)
    
    if status != "completed":
        return False
    
    on_chain_hash = ena.get_result_hash(request_id)
    return on_chain_hash == claimed_hash.decode()


def get_embedding_da_ptr(embedding_key: bytes) -> bytes:
    """
    Get the DA pointer for a completed embedding result.
    """
    raw_id = storage.get(b"embed_req:" + embedding_key)
    if not raw_id:
        return b""
    
    request_id = raw_id.decode()
    if ena.get_status(request_id) != "completed":
        return b""
    
    return ena.get_da_ptr(request_id).encode()
```

---

## Pattern 3: Callback-Based Workflow

```python
"""
Contract: ENA request with callback.

Registers a callback method to be invoked automatically when
the inference result is finalised on-chain.
"""

from stdlib import ena, storage, events, abi

MODEL_VERSION = "ena-v0.9.0-h10000"
SUMMARIZE_TASK = "summarize"
FEE_LIMIT = 15000
CALLBACK_METHOD = "on_summary_ready"


def request_summary(document: bytes) -> bytes:
    """Submit a summarization request with callback."""
    if not document or len(document) > 4000:
        abi.revert(b"invalid_document")
    
    request_id = ena.request(
        model_version=MODEL_VERSION,
        task_type=SUMMARIZE_TASK,
        input_payload=document,
        fee_limit=FEE_LIMIT,
        callback=CALLBACK_METHOD,
    )
    
    storage.set(b"summary_req", request_id.encode())
    storage.set(b"summary_status", b"pending")
    
    return request_id.encode()


def on_summary_ready(request_id: bytes, result_hash: bytes) -> None:
    """
    Callback: automatically called by the chain when the result is ready.
    
    This function receives:
    - request_id: the original request ID
    - result_hash: SHA3-256 hash of the result payload
    """
    stored_req = storage.get(b"summary_req")
    if not stored_req or stored_req != request_id:
        abi.revert(b"request_id_mismatch")
    
    # Store the result hash for verification
    storage.set(b"summary_result_hash", result_hash)
    storage.set(b"summary_status", b"ready")
    
    events.emit(b"SummaryReady", {
        b"request_id": request_id,
        b"result_hash": result_hash,
    })


def get_summary_status() -> bytes:
    return storage.get(b"summary_status") or b"unknown"


def get_summary_hash() -> bytes:
    return storage.get(b"summary_result_hash") or b""
```

---

## Pattern 4: DA-Pointer Result Retrieval

For large results that cannot be stored inline, use the DA pointer pattern:

```python
from stdlib import ena, storage, abi

MODEL_VERSION = "ena-v0.9.0-h10000"


def get_result_pointer(request_id: bytes) -> tuple:
    """
    Get the result for a completed request.
    
    Returns (result_hash: str, da_ptr: str, status: str).
    If da_ptr is non-empty, fetch the full result from the DA layer.
    """
    req_id = request_id.decode()
    status = ena.get_status(req_id)
    
    if status != "completed":
        return "", "", status
    
    result_hash = ena.get_result_hash(req_id)
    da_ptr = ena.get_da_ptr(req_id)
    
    return result_hash, da_ptr, status
```

Off-chain code can then fetch the full result:
```bash
animica da get <da_ptr>
```

---

## Gas Accounting

| Operation           | Gas Cost (default) |
|---------------------|--------------------|
| `ena.request()`     | 5000               |
| `ena.get_status()`  | 200                |
| `ena.get_result_hash()` | 200           |
| `ena.read_result()` | 1000               |
| `ena.get_da_ptr()`  | 200                |
| `ena.verify_receipt()` | 500            |

Gas costs are governance-adjustable via chain parameters.

---

## Best Practices

1. **Always store request_id** — You'll need it to poll for results.
2. **Check status before reading** — Don't call `read_result` before status == "completed".
3. **Use result_hash for verification** — It's the most gas-efficient way to verify results.
4. **Use DA pointers for large outputs** — Embeddings and large outputs go to DA automatically.
5. **Set reasonable fee limits** — Excess is refunded; too low may cause rejection.
6. **Include a callback for time-sensitive workflows** — Avoids polling overhead.
7. **Pin model versions** — Use specific version strings, not aliases, for reproducibility.

---

## See Also

- [overview.md](overview.md) — Architecture and design
- [rpc.md](rpc.md) — RPC methods for submitting/querying requests
- [cli.md](cli.md) — CLI tools
- [operator.md](operator.md) — Model version registration
