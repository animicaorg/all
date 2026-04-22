# Contract Jobs Deployment

1. Deploy VM-PY contracts:
- `aicf_job_escrow`
- `aicf_model_call`
- `aicf_agent_task`
- registry/stake/dispute/rewards/config contracts

2. Configure API environment with deployed addresses.

3. Deploy services:
- contract-job-watcher
- fulfillment-scheduler
- result-submitter
- finalization-worker
- provider-control-plane

4. Deploy web/docs and enable navigation links.

5. Run end-to-end flow tests before production enablement.
