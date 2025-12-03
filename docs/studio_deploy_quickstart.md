# Studio deploy quickstart (devnet)

This short guide shows how to compile a sample contract, emit a manifest, and deploy it to a local devnet node using the existing Animica CLIs. It is intentionally copy-pasteable for ad-hoc end-to-end testing of Studio Web/WASM flows.

## Prerequisites
- Python 3.11+ with the repo dependencies installed:
  ```bash
  pip install -r contracts/requirements.txt
  ```
- A devnet node running at `http://127.0.0.1:8545` (for example via [`ops/run.sh --profile devnet`](quickstart-devnet.md)).
- A funded development mnemonic in `DEPLOYER_MNEMONIC` (any dev-only test words; never reuse on mainnet). The defaults in [`tests/devnet/seed_wallets.json`](tests/devnet/seed_wallets.json) work with the compose devnet.

## One-command build → manifest → deploy
The snippet below builds the canonical Counter sample, writes a manifest with the computed `code_hash`, and deploys the resulting package to the devnet node in one flow. All commands are existing CLIs; swap `RPC_URL`, `CHAIN_ID`, or the source paths to point at your own project.

```bash
export RPC_URL=${RPC_URL:-http://127.0.0.1:8545}
export CHAIN_ID=${CHAIN_ID:-1337}
export DEPLOYER_MNEMONIC="${DEPLOYER_MNEMONIC:-abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about}"

python -m contracts.tools.build_package \
  --source tests/fixtures/contracts/counter/contract.py \
  --manifest tests/fixtures/contracts/counter/manifest.json \
  --out-dir contracts/build/studio-counter \
  --json \
&& python -m contracts.tools.deploy \
  --package contracts/build/studio-counter/Counter.pkg.json \
  --rpc "$RPC_URL" \
  --chain-id "$CHAIN_ID" \
  --mnemonic "$DEPLOYER_MNEMONIC" \
  --wait \
  --json
```

On success the deploy step prints JSON that includes the transaction hash, receipt status, and the deployed address (bech32m `anim1…`). You can feed the same package (`Counter.pkg.json`) into Studio Web/WASM for verification or follow-up calls.
