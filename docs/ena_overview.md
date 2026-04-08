# ENA Overview

ENA is the Animica agent/runtime layer for:

- model-backed CLI reasoning
- semantic and hybrid retrieval
- scraping and ingestion
- useful-work job execution and receipt generation
- training orchestration

The runnable Python package lives under `/root/animica/python`.

## Install

```bash
cd /root/animica/python
python -m pip install -e '.[dev,ena]'
```

## Core Commands

```bash
python -m animica ena doctor
python -m animica ena config init
python -m animica ena models list
python -m animica ena embeddings test --provider ollama
python -m animica ena scrape url https://example.com --out /tmp/example.jsonl
python -m animica ena index build /tmp/example.jsonl --name example_index
python -m animica ena search "example domain" --index example_index --hybrid
python -m animica ena jobs list
python -m animica ena train list
python -m animica ena artifacts list
python -m animica ena runs list
```

## What Is Real Today

- Model providers: deterministic fallback, OpenAI-compatible APIs, Ollama, stub test provider
- Embeddings: OpenAI-compatible APIs, Ollama, explicit hashing fallback, stub test provider
- Retrieval: chunking, persistent SQLite index metadata, semantic search, keyword search, hybrid search, index manifests
- Scraping: URL fetch, batch scrape, constrained crawl, robots-aware fetch policy, sitemap-assisted crawl option
- Useful-work: create, claim, run, verify, receipt, on-chain export payload
- Training: prepare, run, eval, status, list, export, resume
- Credits/mining bridge: verified receipts are mirrored into a local AICF protocol-state ledger

## External Boundaries

- Remote model and embedding providers still depend on a configured upstream endpoint.
- Heavyweight GPU training still depends on either an external launcher command or optional local `transformers` dependencies.
- Chain submission is represented by canonical export payloads; direct node-side anchoring remains the explicit next boundary when node RPC support exists.
