# ENA Overview

ENA is the CLI-first agent, retrieval, useful-work, and training orchestration layer for Animica.

It now has four concrete production-facing pillars:

1. A model-backed agent runtime with pluggable providers and deterministic fallback
2. A real embedding-backed retrieval layer with keyword and hybrid search
3. A useful-work lifecycle with stable job hashes, receipts, and on-chain export envelopes
4. A training orchestrator that tracks manifests, runs, artifacts, checkpoints, and eval reports

## Architecture

The implementation lives under `python/animica/ena/`.

- `config.py`: layered config loading plus provider defaults and env overrides
- `models.py`: typed config, retrieval, receipt, and training schemas
- `providers.py`: model and embedding provider adapters
- `store.py`: SQLite state for sessions, traces, chunks, indexes, jobs, receipts, evals, and training runs
- `retrieval.py`: chunking, index builds, embedding writes, and hybrid search
- `agent.py`: plan/act/observe/retry/summarize loop with auditable tools
- `jobs.py`: useful-work create/claim/run/submit/verify/receipt/export-onchain lifecycle
- `receipts.py`: deterministic receipt hashing, validation, credit-event envelopes
- `datasets.py`: normalize, dedupe, shard, split, validate, export, and manifest generation
- `training.py`: prepare/run/eval/status/list/export orchestration
- `service.py`: HTTP API for sessions, indexes, jobs, receipts, evals, and training runs

## Model Runtime

ENA supports multiple model adapters through config:

- `deterministic`: extractive fallback for offline or policy-only flows
- `openai_compatible`: remote or self-hosted OpenAI-style APIs
- `ollama`: local or remote Ollama runtimes

The agent loop persists every tool action and result. Tool invocations are traceable in:

- session traces in SQLite
- `logs/audit.jsonl`
- output artifacts for fetch, crawl, search-backed summaries, and agent runs

## Retrieval

Semantic retrieval is no longer hard-wired to local hash vectors.

ENA now supports:

- real embedding providers through `providers.py`
- stored chunk metadata and index metadata
- keyword search
- semantic search
- hybrid ranking
- explicit embedding provider tests

Legacy hashing vectors remain available only as a backward-compatible fallback provider.

## Useful-Work

Useful-work jobs now carry:

- deterministic `job_id`
- deterministic `job_hash`
- deterministic `aicf_task_id`
- typed lifecycle states
- verification records
- machine-readable receipts
- export-ready on-chain envelopes

The receipt/export path is designed so future chain-side or AICF-side consumers can ingest the local artifacts without re-deriving the job state model.

## Training

ENA training is no longer limited to `train prepare`.

It now includes:

- manifest generation with dataset split records
- tracked training runs
- command-launcher backend for external trainers
- optional Python/Transformers backend for local fine-tunes when dependencies are installed
- eval reports against configured model providers
- artifact and run metadata storage in the ENA store

## What Remains External

ENA owns orchestration, manifests, receipts, and artifact tracking in-repo.

The heavyweight parts that may still be external are:

- the remote model endpoint or local model server itself
- the external trainer command or GPU compute stack when using `train run --backend command`
- future chain submission methods for receipts, if the node-side RPC is not yet implemented

Those boundaries are explicit and typed. ENA does not pretend they are complete when they are not.

## Related Docs

- [cli.md](cli.md)
- [providers.md](providers.md)
- [useful-work-jobs.md](useful-work-jobs.md)
- [training-data-flow.md](training-data-flow.md)
- [studio-integration.md](studio-integration.md)
