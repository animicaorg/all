# SDK Deploy Command

Working deploy command (direct module invocation):

```bash
PYTHONPATH=sdk/python python -m omni_sdk.cli.deploy \
  --rpc http://127.0.0.1:8545 \
  --chain-id 1 \
  --keystore ~/.animica/wallets.json \
  --manifest contracts/packages/counter/manifest.json \
  --ir contracts/build/counter/counter.ir
```

If the wallet file contains multiple entries, add `--wallet-label <label>`.
