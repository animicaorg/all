# ENA Useful Work And Mining

## Useful-Work Lifecycle

```bash
cd /root/animica/python
python -m animica ena jobs create --type scrape --source https://example.com
python -m animica ena jobs list
python -m animica ena jobs claim --worker-id miner-01 --types scrape,extract,index
python -m animica ena jobs run --worker-id miner-01 --types scrape,extract,index
python -m animica ena jobs verify <job_id>
python -m animica ena jobs receipt <job_id>
python -m animica ena jobs export-onchain <job_id>
```

## Supported Job Categories

- `scrape`
- `extract`
- `clean`
- `dedupe`
- `chunk`
- `classify`
- `label`
- `summarize`
- `embed`
- `index`
- `eval`
- `dataset_build`
- `training_records`
- `train_prepare`

## Receipt Fields

Every ENA receipt includes:

- `job_id`
- `job_hash`
- `manifest_hash`
- `worker_id`
- `event_timestamps`
- `input_refs`
- `output_refs`
- `result_hash`
- `verification_hash`
- `score`
- `reward`
- `export_payload_hash`

## Mining And Credits

Verified receipts are mirrored into a local AICF protocol-state ledger.

```bash
python -m animica ena credits show
python -m animica ena credits show --miner-address miner-01
python -m animica ena mining status
python -m animica ena mining status --miner-address miner-01
```

## Remaining Chain Boundary

Local lifecycle, receipts, credits, and export payloads are operational now. Direct chain anchoring still depends on the node-side submission hook that will consume the exported receipt envelope.
