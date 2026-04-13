# SDK Deploy Command

Working deploy command (direct module invocation):

```bash
PYTHONPATH=sdk/python python -m omni_sdk.cli.deploy \
  --rpc http://127.0.0.1:8545 \
  --chain-id 1 \
  --wallet-store ~/.animica/wallets.json \
  --label main \
  --manifest contracts/packages/counter/manifest.json \
  --ir contracts/build/counter/counter.ir
```

Selection options:

- `--label <label>`: choose a wallet by label.
- `--address <address>`: choose a wallet by address.
- If `--wallet-store` is used without `--label/--address`, deploy uses the default wallet from `wallets.json`.

Backward compatibility:

- `--keystore` is still accepted for existing scripts.
