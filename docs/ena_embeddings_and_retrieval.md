# ENA Embeddings And Retrieval

## Configure Embeddings

OpenAI-compatible embeddings:

```bash
export OPENAI_API_KEY=YOUR_KEY
export ANIMICA_ENA_EMBEDDING_PROVIDER=openai
export ANIMICA_ENA_EMBEDDING_ADAPTER=openai_compatible
export ANIMICA_ENA_EMBEDDING_TRANSPORT=remote_api
export ANIMICA_ENA_EMBEDDING_MODEL=text-embedding-3-small
export ANIMICA_ENA_EMBEDDING_BASE_URL=https://api.openai.com/v1
```

Ollama embeddings:

```bash
export ANIMICA_ENA_EMBEDDING_PROVIDER=ollama
export ANIMICA_ENA_EMBEDDING_ADAPTER=ollama
export ANIMICA_ENA_EMBEDDING_TRANSPORT=local_runtime
export ANIMICA_ENA_EMBEDDING_MODEL=nomic-embed-text
export ANIMICA_ENA_EMBEDDING_BASE_URL=http://127.0.0.1:11434
```

## Verify Provider Health

```bash
cd /root/animica/python
python -m animica ena embeddings test --provider openai
python -m animica ena embeddings test --provider ollama
```

## Build and Inspect Indexes

```bash
python -m animica ena index build /root/animica/docs --name animica_docs --embedding-provider ollama
python -m animica ena index rebuild /tmp/crawl.jsonl --name crawl_docs --embedding-provider openai
python -m animica ena index stats animica_docs
python -m animica ena artifacts show <index_manifest_artifact_id>
```

## Query

```bash
python -m animica ena search "stable chain head" --index animica_docs --semantic --embedding-provider ollama
python -m animica ena search "header sync" --index animica_docs --hybrid --embedding-provider openai
python -m animica ena search "receipts root" --index animica_docs --keyword
```

## Retrieval Notes

- SQLite stores chunks, embeddings, and index metadata.
- Each build writes an index manifest artifact and a chunk-manifest artifact.
- Hashing embeddings still exist, but only as an explicit lower-tier fallback.
