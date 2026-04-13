# Python VM Deploy + Call Manual Validation

```bash
cd /root/animica
export OMNI_RPC_URL=http://127.0.0.1:8545/rpc
export OMNI_CHAIN_ID=1
```

## 1) Compile Counter

```bash
python -m vm_py.cli.compile \
  --manifest vm_py/examples/counter/manifest.json \
  --out /tmp/counter.ir
```

## 2) Deploy Counter Package

```bash
python -m omni_sdk.cli.deploy package \
  --manifest vm_py/examples/counter/manifest.json \
  --ir /tmp/counter.ir \
  --wallet-file ~/.animica/wallets.json \
  --wallet-label test \
  --alg sphincs_shake_128s \
  --max-fee 1 \
  --wait
```

Capture:
- `TX_HASH` from `tx_hash`
- `CONTRACT` from `contract_address`

## 3) Fetch Receipt

```bash
curl -s "$OMNI_RPC_URL" -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":1,
  \"method\":\"tx.getReceipt\",
  \"params\":[\"$TX_HASH\"]
}" | jq .
```

## 4) Fetch Transaction

```bash
curl -s "$OMNI_RPC_URL" -H 'content-type: application/json' -d "{
  \"jsonrpc\":\"2.0\",
  \"id\":1,
  \"method\":\"tx.get\",
  \"params\":[\"$TX_HASH\"]
}" | jq .
```

## 5) Read `get()`

```bash
python -m omni_sdk.cli.call read \
  --address "$CONTRACT" \
  --abi vm_py/examples/counter/manifest.json \
  --func get
```

## 6) Write `inc()` and `set(3)`

```bash
python -m omni_sdk.cli.call write \
  --address "$CONTRACT" \
  --abi vm_py/examples/counter/manifest.json \
  --func inc \
  --wallet-file ~/.animica/wallets.json \
  --wallet-label test \
  --alg sphincs_shake_128s \
  --max-fee 1 \
  --wait
```

```bash
python -m omni_sdk.cli.call write \
  --address "$CONTRACT" \
  --abi vm_py/examples/counter/manifest.json \
  --func set \
  --args-json '[3]' \
  --wallet-file ~/.animica/wallets.json \
  --wallet-label test \
  --alg sphincs_shake_128s \
  --max-fee 1 \
  --wait
```
