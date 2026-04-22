# Local Development

## Required

- Node.js 20+
- pnpm 9+
- Python (for VM-PY contract tests)

## Start stack

```bash
pnpm --filter @animica/aicf-api dev
pnpm --filter @animica/aicf-web dev
pnpm --filter @animica/aicf-scheduler dev
pnpm --filter @animica/aicf-job-worker dev
pnpm --filter @animica/aicf-usage-meter dev
pnpm --filter @animica/aicf-provider-control-plane dev
pnpm --filter @animica/aicf-dispute-worker dev
pnpm --filter @animica/aicf-treasury-worker dev
pnpm --filter @animica/aicf-contract-job-watcher dev
pnpm --filter @animica/aicf-fulfillment-scheduler dev
pnpm --filter @animica/aicf-result-submitter dev
pnpm --filter @animica/aicf-finalization-worker dev
```
