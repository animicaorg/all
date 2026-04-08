# ENA Provider Configuration

ENA separates model generation from embedding generation. Both are configured in the same ENA config file.

## Config Locations

ENA loads config in this order:

1. user config under `~/.animica/ena/config.{toml,yaml,json}`
2. workspace config under `.animica/ena/config.{toml,yaml,json}`
3. explicit `--config` or `ANIMICA_ENA_CONFIG`
4. env overrides

## Core Fields

Top-level provider selectors:

- `default_model_provider`
- `default_embedding_provider`

Model provider sections live under:

- `[model_providers.<name>]`

Embedding provider sections live under:

- `[embedding_providers.<name>]`

Shared knobs:

- `provider`
- `transport`
- `model`
- `base_url`
- `endpoint`
- `api_key_env_vars`
- `timeout_seconds`
- `retry_policy`

Model-only knobs:

- `max_tokens`
- `temperature`

Embedding-only knobs:

- `dimensions`
- `batch_size`

## Example Config

See [example-config.toml](example-config.toml).

Key patterns:

- use `provider = "deterministic"` for offline fallback
- use `provider = "openai_compatible"` for hosted or self-hosted OpenAI-style APIs
- use `provider = "ollama"` for local or remote Ollama runtimes
- keep `embedding_providers.hashing` only for legacy fallback, not for primary semantic retrieval

## Example: Remote API Model + Remote Embeddings

```toml
default_model_provider = "openai"
default_embedding_provider = "openai"

[model_providers.openai]
provider = "openai_compatible"
transport = "remote_api"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env_vars = ["OPENAI_API_KEY"]
max_tokens = 2048
temperature = 0.2
timeout_seconds = 30.0

[embedding_providers.openai]
provider = "openai_compatible"
transport = "remote_api"
model = "text-embedding-3-small"
base_url = "https://api.openai.com/v1"
api_key_env_vars = ["OPENAI_API_KEY"]
batch_size = 16
timeout_seconds = 30.0
```

## Example: Local Ollama

```toml
default_model_provider = "ollama"
default_embedding_provider = "ollama"

[model_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
max_tokens = 2048
temperature = 0.2

[embedding_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "nomic-embed-text"
base_url = "http://127.0.0.1:11434"
batch_size = 16
```

## Env Overrides

Useful env overrides:

- `ANIMICA_ENA_MODEL_PROVIDER`
- `ANIMICA_ENA_MODEL_ADAPTER`
- `ANIMICA_ENA_MODEL_NAME`
- `ANIMICA_ENA_MODEL_BASE_URL`
- `ANIMICA_ENA_MODEL_API_KEY_ENV`
- `ANIMICA_ENA_MODEL_MAX_TOKENS`
- `ANIMICA_ENA_MODEL_TEMPERATURE`
- `ANIMICA_ENA_MODEL_TIMEOUT`
- `ANIMICA_ENA_MODEL_RETRY_ATTEMPTS`
- `ANIMICA_ENA_EMBEDDING_PROVIDER`
- `ANIMICA_ENA_EMBEDDING_ADAPTER`
- `ANIMICA_ENA_EMBEDDING_MODEL`
- `ANIMICA_ENA_EMBEDDING_BASE_URL`
- `ANIMICA_ENA_EMBEDDING_API_KEY_ENV`
- `ANIMICA_ENA_EMBEDDING_DIMENSIONS`
- `ANIMICA_ENA_EMBEDDING_TIMEOUT`
- `ANIMICA_ENA_EMBEDDING_RETRY_ATTEMPTS`

## Running With A Real Model

```bash
animica ena models test --provider ollama
animica ena ask "Explain finality in this repo" --context . --model-provider ollama --model llama3.1
animica ena chat --repo . --model-provider openai --model gpt-4.1-mini
```

## Running With Real Embeddings

```bash
animica ena embeddings test --provider openai
animica ena index build ./docs --embedding-provider openai
animica ena search "stable chain head" --semantic --embedding-provider openai
```
