# omni_sdk Python Examples

This directory contains runnable SDK examples. The canonical contract flow is:

- manifest: `vm_py/examples/counter/manifest.json`
- code: `vm_py/examples/counter/contract.py`
- deploy script: `sdk/python/examples/deploy_counter.py`

## Setup

From repo root:

```bash
python -m pip install -e ./sdk/python
```

Set environment defaults (dev/test):

```bash
export OMNI_SDK_RPC_URL="http://127.0.0.1:8545"
export OMNI_CHAIN_ID=1
export OMNI_SDK_SEED_HEX="<32-byte-seed-hex>" # exactly 64 hex chars
```

## Deploy Counter

CLI:

```bash
PYTHONPATH=sdk/python python -m omni_sdk.cli.main deploy package \
  --manifest vm_py/examples/counter/manifest.json \
  --code vm_py/examples/counter/contract.py \
  --alg dilithium3 \
  --max-fee 1 \
  --wait
```

Python example:

```bash
python sdk/python/examples/deploy_counter.py \
  --manifest vm_py/examples/counter/manifest.json \
  --code vm_py/examples/counter/contract.py \
  --alg dilithium3 \
  --seed-hex "$(openssl rand -hex 32)" \
  --max-fee 1
```

Existing wallet label (uses local wallets.json key material):

```bash
python sdk/python/examples/deploy_counter.py \
  --manifest vm_py/examples/counter/manifest.json \
  --code vm_py/examples/counter/contract.py \
  --wallet-label test \
  --wallet-file ~/.animica/wallets.json \
  --max-fee 1
```

Offline dry-run (no node required):

```bash
python sdk/python/examples/deploy_counter.py \
  --manifest vm_py/examples/counter/manifest.json \
  --code vm_py/examples/counter/contract.py \
  --sender anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j \
  --dry-run
```

Notes:
- `--seed-hex` expects a 32-byte seed (64 hex chars).
- Passing 64-byte material (128 hex chars) to `--seed-hex` is rejected; use wallet-backed signing via `--wallet-label` for existing key material.

## Call Contract

Read (simulate; requires node simulation RPC support):

```bash
PYTHONPATH=sdk/python python -m omni_sdk.cli.main call read \
  --address <contractAddress> \
  --abi vm_py/examples/counter/manifest.json \
  --func get
```

Write:

```bash
PYTHONPATH=sdk/python python -m omni_sdk.cli.main call write \
  --address <contractAddress> \
  --abi vm_py/examples/counter/manifest.json \
  --func inc \
  --alg dilithium3 \
  --max-fee 1 \
  --wait
```
