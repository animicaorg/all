# PQ Mainnet Enablement Checklist (Dilithium3 + SPHINCS+ 128s)

## 1) Configure policy / fallback (operator)

Preferred (normal operation):

- Keep policy load healthy (no missing/invalid override file).
- Do **not** set `ANIMICA_ALLOWED_SIG_SCHEMES` unless intentionally constraining schemes.

Emergency fallback (only if policy load fails and explicitly enabled):

```bash
export ANIMICA_ENABLE_PQ_ALLOWLIST_FALLBACK=1
export ANIMICA_PQ_ALLOWLIST="dilithium3,sphincs128s"
```

## 2) Restart node

```bash
# example
pkill -f "python.*rpc" || true
python -m rpc.server
```

At startup, the node logs effective signature policy and fails fast on chainId=1 / validator role if required PQ schemes are not effectively enabled.

## 3) Verify enabled schemes via RPC

```bash
curl -s http://127.0.0.1:8547/ \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tx.getSupportedSignatureSchemes","params":[]}' | jq
```

```bash
curl -s http://127.0.0.1:8547/ \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"policy.getEffective","params":[]}' | jq
```

```bash
curl -s http://127.0.0.1:8547/ \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"rpc.discover","params":[]}' | jq '.result.methods[]?.name // .result.methods[]?'
```

## 4) Create a Dilithium3 account (CLI)

```bash
python -m pq.cli.pq_keygen --alg dilithium3 --out-dir ./keys --name mainnet_d3
```

## 5) Submit tx successfully

Generate/sign raw tx with your wallet/SDK using Dilithium3 (or SPHINCS+ 128s), then submit:

```bash
curl -s http://127.0.0.1:8547/ \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tx.sendRawTransaction","params":["0x<raw_tx_hex>"]}' | jq
```

If rejected, inspect structured error fields (`kind`, `schemeId`, `policyRoot`, `supported`) to distinguish unsupported vs policy-disabled schemes.
