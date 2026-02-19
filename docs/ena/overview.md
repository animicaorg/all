# ENA — Embedded Neural Agent: Overview

## What is ENA?

ENA (Embedded Neural Agent) is Animica's decentralized open-source AI model,
trained and improved collaboratively by the network via the AICF (AI Compute Fund).
ENA enables smart contracts to request AI inference in a **safe, deterministic,
and auditable** way — without compromising consensus integrity.

ENA checkpoints are published to the DA (Data Availability) layer approximately
every 10,000 blocks, providing verifiable provenance for every model version
used in on-chain requests.

---

## Why Are On-Chain ENA Calls Asynchronous?

Blockchain consensus requires that every validator arrives at the **exact same
result** from the same inputs. Traditional AI inference is:

- Non-deterministic (floating-point variability, sampling randomness)
- Slow (cannot fit in a block production time window)
- Resource-intensive (requires GPU or large RAM)

Therefore, ENA on-chain calls use an **asynchronous oracle / receipt-based** pattern:

```
Contract                    Chain State               Off-chain Worker
  │                             │                          │
  ├── ena.request(...) ────────>│ create ENARequest         │
  │   (returns request_id)      │ status=queued             │
  │                             │                          │
  │                             │<─── worker polls queued ──┤
  │                             │     requests              │
  │                             │                          │
  │                             │<─── submit_result ────────┤
  │                             │     (receipt + result hash│
  │                             │      + DA pointer)        │
  │                             │                          │
  ├── ena.get_status(req_id) ──>│ returns "completed"       │
  ├── ena.get_result_hash(...)─>│ returns hash of output    │
  └── ena.read_result(...)─────>│ returns inline output     │
                                │ (if small enough)         │
```

Contracts do **not** run inference — they submit a request, and later read
the committed, verified result.

---

## Fee Model & AICF Linkage

Every ENA request locks a small ANM fee. On completion, the fee is split:

| Recipient          | Default Share | Description                          |
|--------------------|---------------|--------------------------------------|
| Inference Worker   | 60%           | Compensation for running inference   |
| AICF Pool          | 30%           | Funds model training and improvement |
| Treasury/Protocol  | 10%           | Protocol sustainability              |

On failure or expiry:
- Creator receives ~99% refund
- AICF receives 1% slashing fee (discourages spam)

Fee splits are governance-adjustable via chain parameters.

---

## Model Versioning & DA Anchoring

Every ~10,000 blocks, a new ENA checkpoint is:
1. Published to the DA layer (content-addressed manifest + weights reference)
2. Registered in the on-chain model version registry
3. Optionally set as the active model for new requests

Model version strings follow the format: `ena-v{major}.{minor}.{patch}-h{height}`

Example: `ena-v0.9.0-h10000`

Contracts **must** specify a model version when submitting requests. This ensures:
- Reproducibility: the exact model used is recorded on-chain
- Auditability: anyone can verify what model produced a given result
- Governance: deprecated models are rejected by chain policy

---

## Security & Policy Guardrails

The ENA system enforces these protections at the chain level:

- **Max input bytes**: Prevents unbounded payload storage (default: 4096 bytes)
- **Max output bytes**: Prevents large inline results (default: 8192 bytes, large outputs go to DA)
- **Allowed task types**: Whitelist of permitted operations (classify, embed, summarize, custom)
- **Model allowlist**: Only active, registered model versions are accepted
- **Request expiry**: Requests expire after N blocks (default: 1440) if not fulfilled
- **No live inference in VM**: AI inference NEVER runs inside contract execution
- **No internet access**: Contracts cannot trigger arbitrary external data fetching
- **No hidden system prompts**: All request parameters are fully on-chain and auditable
- **No nondeterministic sampling**: On-chain mode uses fixed parameters only
- **Replay protection**: Receipt hashes prevent duplicate result submissions

---

## Quick Example

```python
# In a Python contract (vm_py/stdlib/ena)
from stdlib import ena

# Submit an ENA classify request
request_id = ena.request(
    model_version="ena-v0.9.0-h10000",
    task_type="classify",
    input_payload=b"Is this message spam?",
    fee_limit=10000,  # ANM nano-units
)

# In a later transaction / callback:
status = ena.get_status(request_id)
if status == "completed":
    result_hash = ena.get_result_hash(request_id)
    # result_hash is a deterministic SHA3-256 hex string
    # Use it for on-chain verification
```

See [contract-integration.md](contract-integration.md) for full examples.

---

## See Also

- [contract-integration.md](contract-integration.md) — Python contract examples
- [rpc.md](rpc.md) — JSON-RPC API reference
- [cli.md](cli.md) — CLI usage examples
- [operator.md](operator.md) — Node operator guide (model registration, DA anchoring)
