# ENA CLI

All commands are under:

```bash
animica ena --help
```

Use `--json` for scriptable output.

## Model Commands

```bash
animica ena models list
animica ena models test --provider deterministic
animica ena models test --provider openai --model gpt-4.1-mini
```

`models list` shows configured providers and can query the endpoint with `--remote`. `models test` performs a structured-output smoke test through the selected adapter.

## Agent Commands

```bash
animica ena ask "Summarize sync in this repo" --context . --model-provider ollama --model llama3.1
animica ena plan "Build a retrieval index and answer docs questions" --context .
animica ena run task.yaml --model-provider openai
animica ena agent run task.yaml --model-provider ollama
animica ena chat --repo . --model-provider deterministic
```

`ask`, `run`, and `chat` all use the model-backed planner/executor when a real provider is configured. Deterministic mode remains available as fallback.

## Retrieval Commands

```bash
animica ena index build ./docs --embedding-provider ollama
animica ena index rebuild out/crawl.jsonl --name docs_crawl --embedding-provider openai
animica ena index list
animica ena search "consensus finality" --hybrid
animica ena search "stable chain head" --semantic --index docs_crawl --embedding-provider openai
animica ena search "header sync" --keyword
animica ena embeddings test --provider ollama
```

`index build` and `index rebuild` write chunk metadata plus embedding metadata to the ENA store. `search` supports keyword, semantic, and hybrid ranking.

## Useful-Work Commands

```bash
animica ena jobs create --type extract --source docs/guide.md
animica ena jobs create --type label --dataset out/train.jsonl --label safe --label unsafe
animica ena jobs claim --worker-id miner-01 --types extract,index
animica ena jobs run --worker-id miner-01 --types extract,index
animica ena jobs run <job_id>
animica ena jobs submit <job_id> result.json
animica ena jobs verify <job_id>
animica ena jobs receipt <job_id>
animica ena jobs export-onchain <job_id>
animica ena jobs list
animica ena jobs show <job_id>
```

Useful-work job types now include:

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

`jobs export-onchain` emits the receipt, validation result, and chain-consumable export envelope.

## Dataset Commands

```bash
animica ena datasets ingest out/raw.jsonl --kind scrape_records
animica ena datasets normalize out/raw.jsonl --out out/train.jsonl
animica ena datasets dedupe out/train.jsonl --out out/train.clean.jsonl
animica ena datasets split out/train.clean.jsonl --out-dir out/splits
animica ena datasets validate out/train.clean.jsonl
animica ena datasets export out/train.clean.jsonl --out out/train.parquet --format parquet
```

## Training Commands

```bash
animica ena train prepare \
  --dataset out/train.clean.jsonl \
  --out manifests/train_manifest.json \
  --base-model tiny-local-model \
  --backend command \
  --auto-split \
  --launcher-command "python external_trainer.py --manifest {manifest} --output-dir {output_dir}"

animica ena train run --manifest manifests/train_manifest.json
animica ena train eval --manifest manifests/train_manifest.json --model-provider ollama --model llama3.1
animica ena train status <run_id>
animica ena train list
animica ena train export <run_id> --out out/train-run.json
```

`train run` supports:

- `command`: launch an external trainer command described by the manifest
- `python_transformers`: optional local fine-tune backend when `datasets` and `transformers` are installed

## Memory, Config, and Service Commands

```bash
animica ena memory add "Sync uses header-first validation" --source docs/sync.md
animica ena memory query "header-first sync"
animica ena config init
animica ena config show
animica ena serve --host 127.0.0.1 --port 8787
```

The HTTP API now exposes sessions, indexes, jobs, receipts, eval runs, and training runs.
