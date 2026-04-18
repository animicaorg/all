# BANM Bridge Operations

## Runtime Services

- Backend API + worker: `python -m animica.bridge_banm`
- Public web UI: `pnpm --filter @animica/banm-bridge-web dev`
- Admin UI: `pnpm --filter @animica/banm-bridge-admin dev`
- PostgreSQL

## Environment Baseline

Use `python/animica/bridge_banm/.env.example` and set:

- Animica RPC and custody values
- EVM RPC, chain ID, contract addresses, operator key
- limits and fees
- pause flags

## Startup Sequence

1. Deploy contracts and configure roles.
2. Export backend env.
3. Run DB migration.
4. Seed admin account.
5. Start backend worker/API.
6. Start web/admin apps.

## Health and Readiness

- `GET /healthz`
- `GET /readyz`

Readiness includes current pause flags.

## Worker Responsibilities

- expire overdue orders
- poll confirmation progress for deposit txs
- submit settlement txs exactly once
- confirm settlement txs
- trigger release leg for reverse direction

## Pause Controls

Admin API:

- `POST /api/v1/admin/pause/bridge_paused`
- `POST /api/v1/admin/pause/bridge_paused_forward`
- `POST /api/v1/admin/pause/bridge_paused_reverse`

Contract pause scripts:

- `pnpm --dir evm/banm-bridge run pause --network <network>`
- `pnpm --dir evm/banm-bridge run unpause --network <network>`

## Key Rotation

- Rotate `EVM_OPERATOR_PRIVATE_KEY` and grant roles to new operator.
- Rotate `BANM_BRIDGE_ADMIN_TOKEN_SECRET` (forces admin re-auth).
- Rotate Animica custody signer based on custody key reference runbook.
- Confirm new keys with low-value canary orders.

## Add a New EVM Chain

1. Add chain metadata in `evm/banm-bridge/config/chains/<chain>.json`.
2. Deploy contracts on target chain.
3. Set backend env (`EVM_RPC_URL`, `EVM_CHAIN_ID`, contract addresses).
4. Run `scripts/configureChain.ts` for controller config.
5. Add chain entry in `chain_configs` table for operations metadata.
6. Validate end-to-end with canary deposits in both directions.

## Partial Failure Recovery

- Burn confirmed, release missing:
  - keep reverse paused
  - rerun order retry; backend uses idempotent release record and continues from stored state
- Mint submitted, confirmation unknown:
  - refresh settlement receipt; do not submit second mint tx for same order ID
- API restart during settlement:
  - worker rehydrates state from DB and resumes from `SETTLEMENT_SUBMITTED` rows
