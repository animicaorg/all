# ENA Agent Runtime

## Configure a Real Provider

OpenAI-compatible:

```bash
export OPENAI_API_KEY=YOUR_KEY
export ANIMICA_ENA_MODEL_PROVIDER=openai
export ANIMICA_ENA_MODEL_ADAPTER=openai_compatible
export ANIMICA_ENA_MODEL_TRANSPORT=remote_api
export ANIMICA_ENA_MODEL_NAME=gpt-4.1-mini
export ANIMICA_ENA_MODEL_BASE_URL=https://api.openai.com/v1
```

Ollama:

```bash
export ANIMICA_ENA_MODEL_PROVIDER=ollama
export ANIMICA_ENA_MODEL_ADAPTER=ollama
export ANIMICA_ENA_MODEL_TRANSPORT=local_runtime
export ANIMICA_ENA_MODEL_NAME=llama3.1
export ANIMICA_ENA_MODEL_BASE_URL=http://127.0.0.1:11434
```

## Inspect and Test

```bash
cd /root/animica/python
python -m animica ena models list
python -m animica ena models test --provider openai
python -m animica ena models test --provider ollama
```

## Day-to-Day Agent Commands

```bash
python -m animica ena ask "Summarize sync in this repo" --context /root/animica
python -m animica ena plan "Build a docs index and answer finality questions" --context /root/animica/docs
python -m animica ena agent run task.yaml
python -m animica ena chat --repo /root/animica
python -m animica ena summarize "How does sync work?" --source /root/animica/docs
```

## Runtime Guarantees

- Plans and tool decisions are audited into `logs/audit.jsonl`.
- Agent runs persist sessions, traces, final artifacts, and citations.
- Deterministic fallback remains available when no live provider is configured.
- Structured final output is supported through JSON-schema extraction paths.
