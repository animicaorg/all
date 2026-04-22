# AICF Provider Daemon

Off-chain provider runtime that executes jobs and settles receipts through the AICF API.

## Features

- Authenticates with provider daemon token
- Heartbeats node health/load
- Claims assigned jobs
- Claims assigned contract jobs emitted by on-chain AICFModelCall/AICFAgentTask contracts
- Executes runtime adapters:
  - chat inference
  - embeddings
  - training scaffold
  - agent tasks
  - custom compute
- Submits output + usage receipts
- Reports failures for requeue/slashing logic
- Submits result commitments/references for deterministic on-chain settlement

## Run

```bash
cp .env.example .env
pnpm --filter @animica/aicf-provider-daemon dev
```
