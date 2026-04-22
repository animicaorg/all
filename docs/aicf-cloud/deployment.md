# Deployment

1. Build artifacts with pnpm workspace filters.
2. Deploy API and services as separate workloads.
3. Inject secrets:
   - JWT secret
   - internal worker secret
   - admin bootstrap credentials
4. Deploy web/docs as static apps.
5. Deploy VM-PY contracts and set their addresses in API env:
   - `aicf_job_escrow`
   - `aicf_model_call`
   - `aicf_agent_task`
   - `aicf_provider_registry`
   - `aicf_stake_manager`
   - `aicf_dispute_manager`
6. Deploy worker set:
   - `contract-job-watcher`
   - `fulfillment-scheduler`
   - `result-submitter`
   - `finalization-worker`
7. Enable scheduler and provider network, then run smoke tests.
