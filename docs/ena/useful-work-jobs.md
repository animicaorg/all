# ENA Useful-Work Jobs

Useful-work jobs are the bridge between ENA operator workflows, AICF-style accounting, and future chain anchoring.

## Supported Job Types

Current useful-work job types:

- `scrape`
- `extract`
- `chunk`
- `label`
- `embed`
- `index`
- `summarize`
- `eval`
- `dataset_clean`
- `training_records`
- `train_prepare`

## Lifecycle

The lifecycle is concrete and persisted:

1. `proposed`
2. `claimed`
3. `running`
4. `submitted` or `completed`
5. `verified` or `rejected`
6. `receipt` generation
7. `export-onchain`

`submitted` is used for externally produced results that are uploaded later. `completed` is used for local worker execution.

## Stable IDs and Hashes

Every job has:

- `job_id`: deterministic id for the canonical spec
- `job_hash`: deterministic SHA3 hash of the canonical spec with `job_hash` cleared
- `aicf_task_id`: deterministic AICF-compatible task id derived from the spec payload

This makes local receipts and downstream queue integration reproducible.

## Receipt Schema

Each verified or submitted/completed job can emit a `JobReceipt` with:

- `receipt_id`
- `receipt_version`
- `receipt_hash`
- `job_id`
- `job_hash`
- `job_type`
- `job_status`
- `aicf_task_id`
- `aicf_job_kind`
- `requester`
- `worker_id`
- `provider_id`
- `verification_id`
- `verification_hash`
- `verification_passed`
- `result_hash`
- `source_hashes`
- `artifact_ids`
- `artifact_hashes`
- `score`
- `score_components`
- `reward`
- `onchain_payload`
- `created_at`
- `metadata`

## Deterministic Hashing

`receipt_hash` is the SHA3 hash of the canonical JSON receipt with:

- `receipt_hash = ""`
- `onchain_payload = {}`

Validation recomputes the same normalized hash and compares it to the stored `receipt_hash`.

## Verification

Verification records are machine-readable and include per-check status. Checks vary by job type and include:

- output presence and non-emptiness
- provenance for scrape/extract outputs
- allowed-domain enforcement where configured
- row-count checks for chunk/label/embed/dataset jobs
- index metadata checks for index jobs
- embedding dimension checks for embed jobs
- label presence checks for label/classify jobs
- manifest-shape checks for train-prepare jobs

## AICF and Mining Export

`animica ena jobs export-onchain <job_id>` produces an envelope with:

- the full receipt
- receipt validation result
- a chain-consumable `onchain` payload
- a deterministic credit-event candidate

The on-chain payload includes:

- `receipt_hash`
- `job_id`
- `job_hash`
- `aicf_task_id`
- `aicf_job_kind`
- `result_hash`
- `verification_hash`
- `artifact_hashes`
- `reward`
- `credit_event`

`credit_event` is shaped to match future AICF ledger ingestion with minimal follow-up glue.

## Example Flow

```bash
animica ena jobs create --type extract --source docs/sync.md
animica ena jobs claim --worker-id miner-01 --types extract
animica ena jobs run --worker-id miner-01 --types extract
animica ena jobs receipt <job_id>
animica ena jobs export-onchain <job_id>
```

## Example Mined Useful-Work Job

```bash
animica ena jobs create --type embed --dataset out/train.clean.jsonl
animica ena jobs run <job_id>
animica ena jobs export-onchain <job_id>
```

That flow yields:

- a deterministic `job_hash`
- embedding output artifacts
- a receipt with a deterministic `receipt_hash`
- an export envelope ready for future node/chain-side anchoring
