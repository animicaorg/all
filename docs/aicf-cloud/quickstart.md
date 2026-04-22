# Quickstart

1. Start API: `pnpm --filter @animica/aicf-api dev`.
2. Start workers: scheduler, job-worker, usage-meter, provider-control-plane, dispute-worker, treasury-worker.
3. Start web app: `pnpm --filter @animica/aicf-web dev`.
4. Create account on `/app/onboarding`.
5. Create project, fund with ANM, and issue API key.
6. Call `POST /v1/chat/completions` or `POST /v1/embeddings`.
7. Optional: start provider daemon and submit async jobs.
8. Register contract at `/app/contracts` and create contract job at `/app/contract-jobs/new`.
9. Track commitment/dispute/finalization state at `/app/contract-jobs/[id]` and `/app/disputes`.
