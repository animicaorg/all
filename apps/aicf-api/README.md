# AICF API

ANM-native control plane for `aicf.animica.org`.

## Core capabilities

- SaaS auth (`/auth/signup`, `/auth/login`) with session tokens
- Wallet linking for direct Animica smart contract flows
- Project balances and ANM funding ledger
- API key issuance with scopes and revocation
- OpenAI-compatible inference APIs:
  - `POST /v1/chat/completions`
  - `POST /v1/embeddings`
- Async jobs (training/inference/agent/custom)
- Contract-driven jobs and agent tasks (`/contracts`, `/contract-jobs`, `/agent-tasks`)
- Deterministic commitment/challenge/finalization flow for on-chain AI orchestration
- Provider onboarding + daemon endpoints
- Scheduler tick + assignment
- Usage metering and settlement ledger in ANM
- Admin controls (treasury grants, disputes, feature flags, pause)

## Run locally

```bash
cp .env.example .env
pnpm --filter @animica/aicf-api dev
```

## Tests

```bash
pnpm --filter @animica/aicf-api test
```

## OpenAPI

- [`openapi.yaml`](./openapi.yaml)
