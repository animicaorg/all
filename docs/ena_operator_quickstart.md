# ENA Operator Quickstart

This is the shortest end-to-end operator flow that exercises the upgraded ENA paths.

## 1. Install

```bash
cd /root/animica/python
python -m pip install -e '.[dev,ena]'
python -m animica ena config init
python -m animica ena doctor
```

## 2. Scrape Sources

```bash
python -m animica ena scrape url https://example.com --out /tmp/example.jsonl
python -m animica ena scrape crawl https://example.com --depth 1 --max-requests 10 --out /tmp/example_crawl.jsonl
```

## 3. Build an Index

```bash
python -m animica ena index build /tmp/example_crawl.jsonl --name example_docs
python -m animica ena index stats example_docs
```

## 4. Query With Semantic Or Hybrid Retrieval

```bash
python -m animica ena search "example domain" --index example_docs --hybrid
python -m animica ena summarize "What is this site about?" --index example_docs
```

## 5. Create, Run, and Verify Useful Work

```bash
python -m animica ena jobs create --type extract --source /tmp/example_crawl.jsonl
python -m animica ena jobs list
python -m animica ena jobs run <job_id>
python -m animica ena jobs verify <job_id>
python -m animica ena jobs receipt <job_id>
python -m animica ena jobs export-onchain <job_id>
python -m animica ena credits show
```

## 6. Build a Training Dataset and Launch Training

```bash
python -m animica ena collect build-dataset /tmp/example_crawl.jsonl --raw-out /tmp/raw.jsonl --manifest /tmp/dataset_manifest.json --split
python -m animica ena train prepare \
  --dataset /tmp/raw.deduped.jsonl \
  --out /tmp/train_manifest.json \
  --base-model tiny-local-model \
  --backend command \
  --auto-split \
  --launcher-command "python external_trainer.py --manifest {manifest} --output-dir {output_dir}"
python -m animica ena train run --manifest /tmp/train_manifest.json
python -m animica ena train status <run_id>
python -m animica ena train eval --run-id <run_id>
```

## 7. Inspect Artifacts and Prior Runs

```bash
python -m animica ena artifacts list
python -m animica ena artifacts show <artifact_id>
python -m animica ena artifacts verify <artifact_id>
python -m animica ena runs list
python -m animica ena runs show <session_id>
python -m animica ena mining status
```

## Fully Local Validation

If you want a no-network sanity pass first:

```bash
python -m animica ena demo
python -m animica ena verify --demo
pytest -q animica/ena/tests animica/tests/test_ena_e2e_smoke.py
```
