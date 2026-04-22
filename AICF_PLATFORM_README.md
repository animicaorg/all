# AICF Platform (aicf.animica.org)

AICF Cloud is an ANM-native decentralized AI compute cloud built on Animica.

## Included implementation

- `apps/aicf-web`: full website + developer/provider/admin dashboard routes
- `apps/aicf-api`: control plane APIs, OpenAI-compatible model endpoints, auth, billing, jobs, contract-job orchestration
- `apps/aicf-docs`: docs web app
- `services/*`: scheduler, job-worker, usage meter, provider control plane, dispute worker, treasury worker, contract-job watcher, fulfillment scheduler, result submitter, finalization worker
- `provider-daemon`: provider execution runtime
- `contracts/packages/aicf_*`: VM-PY smart contracts for ANM economy + contract-driven AI jobs (`aicf_model_call`, `aicf_agent_task`)
- `contracts/examples/aicf/*`: VM-PY example contracts for summarize/classify/embeddings/agent workflows
- `packages/shared/aicf`: shared ANM economics and scheduler logic
- `packages/sdk/aicf`: TypeScript SDK
- `docs/aicf-cloud/*`: architecture and operational docs

## ANM-only economics

No fiat rails are used. The platform supports only ANM-denominated funding, budget reservation, spend, rewards, staking, slashing, and treasury grants/subsidies.
