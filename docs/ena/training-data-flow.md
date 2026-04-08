# ENA Training Data Flow

ENA training now covers both data preparation and run orchestration.

## End-to-End Flow

1. ingest or scrape raw sources into normalized records
2. register datasets in the ENA store
3. normalize records into training samples
4. dedupe exact and near-duplicate rows
5. split datasets into train/eval/test sets when needed
6. generate a training manifest with dataset hashes and launcher metadata
7. launch a tracked training run
8. store artifacts, checkpoint metadata, and eval reports

## Commands

```bash
animica ena scrape https://docs.example.org --out out/raw.jsonl
animica ena datasets ingest out/raw.jsonl --kind scrape_records
animica ena datasets normalize out/raw.jsonl --out out/train.jsonl
animica ena datasets dedupe out/train.jsonl --out out/train.clean.jsonl
animica ena datasets split out/train.clean.jsonl --out-dir out/splits

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

## Manifest Shape

Training manifests now include:

- `run_name`
- `backend`
- `base_model`
- `output_dir`
- `train`
- `eval`
- `test`
- `hyperparameters`
- `launcher`
- `metadata`

Each split record includes:

- `split`
- `path`
- `row_count`
- `sha256`
- `metadata`

Compatibility fields such as `train_dataset` and `train_sha256` are still emitted for older consumers.

## Training Backends

ENA currently supports two orchestration backends:

- `command`
  Use an explicit external trainer command with `{manifest}` and `{output_dir}` placeholders.
- `python_transformers`
  Optional in-process local fine-tune runner when `datasets` and `transformers` are installed.

## Stored Run State

Each training run persists:

- `run_id`
- `status`
- `backend`
- `manifest_path`
- `base_model`
- `output_dir`
- `command`
- `checkpoint_paths`
- `artifact_ids`
- `metrics`
- `eval_report`
- `metadata`
- `error`

The ENA store keeps this in SQLite and materializes artifacts into the ENA artifact directory.

## What Is Fully In Repo

Implemented in-repo:

- dataset normalization, dedupe, split, validation, and export
- training manifest generation
- training run tracking
- command launch orchestration
- optional Python/Transformers runner code path
- evaluation against configured model providers
- artifact, checkpoint, and run metadata tracking

## What Remains External

Still external by design:

- GPU/accelerator compute when using the `command` backend
- model downloads and runtime dependencies for the `python_transformers` backend
- future chain-side training receipt submission methods

The orchestration boundary is explicit. ENA owns the manifests, bookkeeping, and artifact lineage even when compute happens elsewhere.
