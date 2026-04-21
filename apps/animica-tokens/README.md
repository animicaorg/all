# Animica Tokens (Launcher + DEX)

Unified token launcher + DEX web product for Animica VM-PY assets.

## What is here

- `src/`: React/Vite website (`/launch`, `/tokens`, `/dex/swap`, `/dex/pools`, `/portfolio`, `/admin`, `/faq`, `/docs`)
- `server/`: Express API for token launch, pool creation, swaps, liquidity, metadata uploads, moderation/reporting
- `server/data/`: local persisted index/store and local IPFS fallback blobs

## Contract stack used by this app

- `contracts/standards/animica_token`
- `contracts/standards/animica_dex_factory`
- `contracts/standards/animica_dex_router`
- `contracts/standards/animica_dex_pair`

## Deploy contracts + auto-wire envs

From repo root:

```bash
python scripts/animica_tokens/chain_ops.py deploy-stack \
  --rpc http://127.0.0.1:8545/rpc \
  --chain-id 1337 \
  --seed-hex "$ANIMICA_DEPLOY_SEED_HEX" \
  --network devnet \
  --default-fee-bps 30 \
  --launch-fee-anm 0
```

This writes addresses automatically to:

- `apps/animica-tokens/.env.local`
- `apps/animica-tokens/server/.env`

## Run locally

1. Copy env templates:
```bash
cp apps/animica-tokens/.env.example apps/animica-tokens/.env.local
cp apps/animica-tokens/server/.env.example apps/animica-tokens/server/.env
```

2. Install deps (repo root):
```bash
pnpm install
```

3. Start website + API:
```bash
pnpm --filter @animica/animica-tokens dev
```

Defaults:
- Web UI: `http://127.0.0.1:5182`
- API: `http://127.0.0.1:8787`

## Docker Compose

From repo root:

```bash
docker compose -f ops/docker/docker-compose.animica-tokens.yml up -d --build
docker compose -f ops/docker/docker-compose.animica-tokens.yml logs -f
```

## Chain ops utility

`scripts/animica_tokens/chain_ops.py` commands:

- `deploy-stack`
- `launch-token`
- `create-pair`
- `add-liquidity`
- `remove-liquidity`
- `swap-exact-in`
- `swap-exact-out`

For non-native token paths, add/swap operations auto-submit token `approve` calls for the pair spender before the router call.
