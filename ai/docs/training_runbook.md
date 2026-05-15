# Training pipeline runbook

Trains the Animica-native flagship coding model from the monorepo. Produces
a downloadable bundle (weights + tokenizer + manifest) that miners install
to serve AICF inference jobs.

## One-command driver

```bash
bash ai/flagship_agent/scripts/train_flagship_agent.sh
```

Stages run in the order declared in `ai/configs/pipeline.yaml::stages`:

```
bootstrap_env → inventory_repo → build_corpora → clean_and_dedupe
              → build_graph → build_indices → build_datasets
              → check_contamination → train_cpt → train_sft
              → evaluate_model → run_critique → run_benchmark
              → export_bundle → smoke_test_bundle
```

Each stage emits a manifest at
`ai/flagship_agent/runs/<run_id>/_pipeline/<stage>.manifest.json`. Status
lives at `<run_id>/_pipeline/status.json` and updates after every stage.

## Modes

| `FLAGSHIP_MODE` | What runs | When to use |
|---|---|---|
| `simulate` (default) | artifact-only stubs; no model load; CPU-safe | smoke / CI |
| `lite` | tokenizer load + a few fake training steps | sanity on dev box |
| `full` | real CPT + SFT training; loads `base_model.id` | publish to network |

Strict full mode refuses to start if:
- the base model id matches `training.yaml::placeholders_reject`,
- the requested base model can't be loaded,
- the requested accelerator is unavailable (unless `FLAGSHIP_ALLOW_CPU_REAL=1`).

## CPU-only host

```bash
FLAGSHIP_MODE=full FLAGSHIP_ALLOW_CPU_REAL=1 \
  bash ai/flagship_agent/scripts/train_flagship_agent.sh
```

This is slow but produces a real bundle.

## Resume / stage selection

- `FLAGSHIP_RESUME=latest` — reuse the most recent run dir; stages with
  valid outputs are skipped automatically.
- `FLAGSHIP_RESUME=<run_id>` — resume a specific run.
- `FLAGSHIP_STAGES=inventory_repo,build_corpora` — run a subset.

## Multi-tier training

`ai/configs/model_catalog.yaml::train_tiers` lists which tiers a run trains.
Default: `tiny`, `small`, `flagship`. Override at the command line:

```bash
FLAGSHIP_TIERS=tiny,small \
  bash ai/flagship_agent/scripts/train_flagship_agent.sh
```

Each tier produces its own bundle under
`ai/flagship_agent/models/export/<run_id>/` with its own manifest CID.

## Outputs

```
runs/<run_id>/
  _pipeline/
    status.json
    run.summary.json
    <stage>.{log,jsonl,manifest.json}
    config.snapshot.{yaml,json}
  inventory/    files.jsonl, summary.json
  corpora/      raw.jsonl, clean.jsonl, quarantine.jsonl, summary.json
  graph/        nodes.jsonl, edges.jsonl, summary.json
  index/        lexical.idx, metadata.jsonl
  datasets/     cpt.jsonl, sft.jsonl, diff.jsonl, mutation.jsonl,
                incidents.jsonl, eval.jsonl, contamination_report.json
  training/
    cpt/        backend.json, summary.json, checkpoints/
    sft/        backend.json, summary.json, checkpoints/
  eval/         results.json, per_task.jsonl, critique.json, benchmark.json

models/export/<run_id>/
  manifest.json
  inference.json
  MODEL_CARD.md
  model/        weights + tokenizer
models/export/latest -> <run_id>
```

## Publishing the bundle to IPFS

`export_bundle.py` automatically pushes the bundle tarball to a local IPFS
daemon (`ipfs.api_endpoint` from `integration.yaml`) when one is reachable.
The resulting CID is written to `manifest.json::ipfs_cid` and surfaced in
the provider descriptor. Miners pull by CID via
`animica miner aicf-worker pull <cid> --tier <id>`.

## Honesty guarantees

Every bundle declares:

- `requested_base_model` vs `base_model` (loaded)
- `effective_mode` (one of simulate / lite / full)
- `available_for_real_inference` — true only when full-mode + score ≥ gate
  + not contaminated
- `fallback_reasons` — every degradation reason captured

`smoke_test_bundle.py` refuses to ship a bundle whose declared mode is
`full` but whose loaded model doesn't match the requested base.
