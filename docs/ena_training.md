# ENA Training

## Prepare

```bash
cd /root/animica/python
python -m animica ena train prepare \
  --dataset /tmp/train.jsonl \
  --out /tmp/train_manifest.json \
  --base-model tiny-local-model \
  --backend command \
  --auto-split \
  --launcher-command "python external_trainer.py --manifest {manifest} --output-dir {output_dir}"
```

## Run

```bash
python -m animica ena train run --manifest /tmp/train_manifest.json
python -m animica ena train status <run_id>
python -m animica ena train list
```

## Resume

```bash
python -m animica ena train resume <run_id>
```

`resume` reuses the existing output directory and persists `resumed_from_run_id`.

## Evaluate And Export

```bash
python -m animica ena train eval --run-id <run_id> --model-provider ollama --model llama3.1
python -m animica ena train export <run_id> --out /tmp/training_export.json
```

## Backends

- `command`: run an external launcher command today
- `python_transformers`: local trainer path when optional `datasets` and `transformers` dependencies are installed

## Persisted Outputs

- training run summary artifact
- training output manifest artifact
- checkpoint manifest metadata
- eval report artifact
- training run store record
