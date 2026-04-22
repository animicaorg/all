# Contract Jobs Local Development

Run API + workers + provider daemon:

```bash
pnpm --filter @animica/aicf-api dev
pnpm --filter @animica/aicf-contract-job-watcher dev
pnpm --filter @animica/aicf-fulfillment-scheduler dev
pnpm --filter @animica/aicf-result-submitter dev
pnpm --filter @animica/aicf-finalization-worker dev
pnpm --filter @animica/aicf-provider-daemon dev
pnpm --filter @animica/aicf-web dev
```

Then open:

- `/app/contracts`
- `/app/contract-jobs`
- `/app/agent-tasks`
- `/app/disputes`
