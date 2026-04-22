# Provider Daemon Install

```bash
cd provider-daemon
cp .env.example .env
# fill AICF_PROVIDER_ID, AICF_PROVIDER_TOKEN, AICF_NODE_ID
pnpm --filter @animica/aicf-provider-daemon dev
```

Daemon responsibilities:

- heartbeat node state
- claim jobs
- execute runtime adapters
- submit result receipts
- report failures
