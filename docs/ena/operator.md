# ENA Operator Guide

## Overview

This guide covers the operational tasks required to run ENA infrastructure:
- Model version registration and DA anchoring
- Worker node setup
- Checkpoint publishing
- Governance operations

---

## Model Version Registration

### Register a new model version

Model versions are registered via the `ena.chain_registry` module (Python) or
via a governance/admin transaction.

**Python API:**
```python
from ena.chain_registry import ENAChainModelRegistry
from execution.state.ena_state import register_model_version, set_active_model

# With chain state access:
registry = ENAChainModelRegistry(state)
registry.register_version(
    version="ena-v0.9.0-h10000",
    da_ptr="da:abc123...",         # DA commitment for checkpoint manifest
    activation_height=10000,
    status="active",
    metadata_hash="da:meta456...", # Optional metadata
    set_active=True,               # Set as active model
)
```

**CLI (future):**
```bash
animica ena model register \
  --version ena-v0.9.0-h10000 \
  --da-ptr da:abc123... \
  --height 10000 \
  --set-active
```

### Deprecate an old model version

```python
registry.deprecate_version("ena-v0.8.0-h0")
```

---

## DA Anchoring

### Checkpoint cadence

ENA checkpoints are published to the DA layer every **10,000 blocks**
(`CHECKPOINT_INTERVAL_BLOCKS = 10_000`).

### Automatic anchoring hook

The block processor calls `process_checkpoint_anchor` at each checkpoint height.
This is wired into the chain execution layer automatically.

If you are running a custom block processor, integrate as follows:

```python
from ena.chain_registry import ENAChainModelRegistry

def on_block_finalized(state, height, block_hash, chain_id):
    registry = ENAChainModelRegistry(state)
    
    entry = registry.process_checkpoint_anchor(
        height=height,
        block_hash=block_hash,
        da_ptr=da_commitment,        # DA commitment from training pipeline
        chain_id=chain_id,
        metadata_hash=metadata_da,   # Optional
        status="active",
    )
    if entry:
        print(f"New ENA model registered: {entry.version}")
```

### Publishing a checkpoint manually

```python
from ena.checkpoint import (
    create_checkpoint_manifest,
    serialize_manifest,
    publish_checkpoint_to_da,
)

manifest = create_checkpoint_manifest(
    height=10000,
    block_hash="0xabc123...",
    chain_id=1,
    training_runs=[...],
    evals=[...],
    weights={
        "format": "safetensors",
        "hash": "sha256:def...",
        "size": 1_500_000_000,
        "shards": ["da:shard1...", "da:shard2..."],
    },
)

commitment, receipt = await publish_checkpoint_to_da(manifest, da_client)
print(f"Published: {commitment}")
```

---

## Worker Node Setup

ENA inference workers:
1. Poll the chain for queued ENA requests
2. Fetch the input payload (from chain state or DA)
3. Run inference using the specified model version
4. Submit the result with a proof/receipt hash

### Worker registration

Workers must be registered with the chain to submit results:

```bash
# Register as an ENA worker (authorized provider)
animica aicf provider register \
  --pubkey 0x... \
  --label "my-ena-worker" \
  --caps '{"ena": true, "max_tokens": 1000}'
```

### Worker result submission

Workers submit results via the `ena.submitResult` RPC method (chain-side):

```python
# In worker code
result = await rpc.call("ena.submitResult", {
    "request_id": "ena-...",
    "worker_id": "provider-0x1234",
    "result_hex": "0x...",        # hex-encoded result payload
    "receipt_hash": "0x...",      # proof/receipt hash
    "da_ptr": "da:...",           # DA pointer for large results
})
```

---

## Policy Management

ENA policy parameters are stored in chain state and can be updated via governance.

| Parameter          | Default  | Description                               |
|--------------------|----------|-------------------------------------------|
| `max_input_bytes`  | 4096     | Maximum input payload size                |
| `max_output_bytes` | 8192     | Maximum inline output size                |
| `expiry_blocks`    | 1440     | Request expiry window in blocks           |
| `allowed_tasks`    | list     | Whitelist of allowed task types           |
| `enabled`          | true     | Enable/disable ENA requests globally      |

**Update via governance:**
```python
from execution.state.ena_state import (
    set_max_input_bytes,
    set_allowed_tasks,
    set_expiry_blocks,
)

# These are governance-only writes; validate caller before executing
set_max_input_bytes(state, 8192)
set_allowed_tasks(state, ["classify", "embed", "summarize", "custom", "my_task"])
set_expiry_blocks(state, 2880)  # ~8 hours
```

---

## Fee Split Configuration

Default fee split (governance-adjustable):
- 60% → inference worker
- 30% → AICF pool
- 10% → treasury

To change fee splits, update the chain parameters via governance:
```python
# In governance execution
params["aicf"]["ena_provider_bps"] = 5000   # 50%
params["aicf"]["ena_aicf_bps"] = 4000       # 40%
params["aicf"]["ena_treasury_bps"] = 1000   # 10%
```

---

## Monitoring

### Check active model
```bash
animica ena model show $(animica ena models --json | jq -r '.active_version')
```

### Monitor request queue
```bash
# List recent ENA requests (via chain state service)
animica chain state get "ena.req.*"
```

### View AICF contributions from ENA
```bash
animica aicf status --json | jq '.ena_contributions'
```

---

## Upgrade Checklist

When a new ENA model checkpoint is published at height `H`:

1. ✅ Publish checkpoint manifest to DA layer
2. ✅ Register new model version on-chain via governance transaction
3. ✅ Set new version as active (if production-ready)
4. ✅ Deprecate old version(s) (if no longer needed)
5. ✅ Update worker nodes to use new model weights
6. ✅ Verify `animica ena models` shows correct active version
7. ✅ Run smoke test: submit a classify request and verify completion

---

## See Also

- [overview.md](overview.md) — Architecture
- [rpc.md](rpc.md) — RPC reference
- [cli.md](cli.md) — CLI reference
