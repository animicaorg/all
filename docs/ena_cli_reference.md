# ENA CLI Reference

## Install

```bash
cd /root/animica/python
python -m pip install -e '.[dev,ena]'
```

## Configuration

```bash
python -m animica ena config init
python -m animica ena config show
python -m animica ena doctor
python -m animica ena verify
python -m animica ena demo
```

## Models And Embeddings

```bash
python -m animica ena models list
python -m animica ena models test --provider openai
python -m animica ena embeddings test --provider ollama
```

## Agent

```bash
python -m animica ena ask "What is finality?" --context /root/animica/docs
python -m animica ena chat --repo /root/animica
python -m animica ena agent run task.yaml
python -m animica ena plan "Build a dataset from scraped pages"
python -m animica ena summarize "How does sync work?" --source /root/animica/docs
```

## Scrape / Ingest / Extract / Collect

```bash
python -m animica ena scrape url https://example.com --out /tmp/example.jsonl
python -m animica ena scrape batch /tmp/urls.txt --out /tmp/batch.jsonl
python -m animica ena scrape crawl https://example.com --depth 2 --max-requests 25 --include-sitemap --out /tmp/crawl.jsonl
python -m animica ena ingest file /root/animica/docs/ena/overview.md --out /tmp/overview.jsonl
python -m animica ena ingest dir /root/animica/docs --out /tmp/docs.jsonl --index --index-name animica_docs
python -m animica ena extract records /root/animica/docs/ena/overview.md
python -m animica ena extract schema /root/animica/docs/ena/overview.md --schema-file /tmp/schema.json
python -m animica ena collect build-dataset /tmp/crawl.jsonl --raw-out /tmp/raw.jsonl --manifest /tmp/dataset_manifest.json --split
```

## Retrieval

```bash
python -m animica ena index build /root/animica/docs --name animica_docs
python -m animica ena index rebuild /tmp/crawl.jsonl --name crawl_docs --embedding-provider openai
python -m animica ena index stats animica_docs
python -m animica ena search "stable chain head" --index animica_docs --hybrid
```

## Jobs / Credits / Mining

```bash
python -m animica ena jobs create --type extract --source /root/animica/docs/ena/overview.md
python -m animica ena jobs run <job_id>
python -m animica ena jobs verify <job_id>
python -m animica ena jobs receipt <job_id>
python -m animica ena jobs export-onchain <job_id>
python -m animica ena credits show
python -m animica ena mining status
```

## Training / Artifacts / Runs

```bash
python -m animica ena train prepare --dataset /tmp/train.jsonl --out /tmp/manifest.json --base-model tiny-local-model --backend command
python -m animica ena train run --manifest /tmp/manifest.json
python -m animica ena train resume <run_id>
python -m animica ena artifacts list
python -m animica ena artifacts show <artifact_id>
python -m animica ena artifacts verify <artifact_id>
python -m animica ena runs list
python -m animica ena runs show <session_id>
```
