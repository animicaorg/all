# Architecture

Core components:

- `apps/aicf-web`: website + dashboards + wallet contract calls
- `apps/aicf-api`: auth, projects, API keys, model APIs, jobs, metering, admin
- `services/*`: scheduler, job-worker, usage-meter, provider-control, dispute, treasury
- `provider-daemon`: provider execution agent
- `contracts/packages/aicf_*`: VM-PY economy contracts
- `packages/shared/aicf`: shared domain types/economics/scheduler scoring
- `packages/sdk/aicf`: TypeScript client SDK
