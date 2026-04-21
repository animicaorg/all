# Animica Tokens Chain Ops

Operational helper scripts for deploying and interacting with the Animica Token Launcher + DEX stack.

## Main Entry Point
- `scripts/animica_tokens/chain_ops.py`

## Commands
- `deploy-stack`: deploy and initialize DEX factory + router, then auto-write env files:
  - `apps/animica-tokens/.env.local`
  - `apps/animica-tokens/server/.env`
- `launch-token`: deploy + initialize a token contract instance.
- `create-pair`: deploy pair contract + register via router/factory.
- `add-liquidity`, `remove-liquidity`, `swap-exact-in`, `swap-exact-out`.
  - For non-native token paths, add/swap operations first submit `approve` calls for the resolved pair spender automatically.

## Quick Start
```bash
python scripts/animica_tokens/chain_ops.py deploy-stack \
  --rpc http://127.0.0.1:8545/rpc \
  --chain-id 1337 \
  --seed-hex "$ANIMICA_DEPLOY_SEED_HEX" \
  --network devnet
```

All commands return JSON to stdout for easy backend integration.
