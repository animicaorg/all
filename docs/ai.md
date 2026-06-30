# `animica ai` — the AI command namespace (5.2.0)

One place to use Animica's AI: check readiness, configure once, chat, serve an
OpenAI-compatible API, run paid inference jobs, earn as a provider, and do local
RAG. Everything binds to your wallet and the same model providers — local
(Ollama), configured (OpenAI-compatible), or the on-chain AICF marketplace.

```
animica ai doctor      # is this machine AI-ready? (with exact fix commands)
animica ai setup       # configure once → ~/.animica/config.toml
animica ai models      # what models can I use?
animica ai chat        # talk to a model (REPL or one-shot)
animica ai serve       # expose an OpenAI-compatible API
animica ai embed       # text → vectors
animica ai rag …       # index local docs + answer grounded questions
animica ai job …       # submit + track paid inference jobs (AICF)
animica ai provider …  # run as a compute provider
animica ai earnings    # provider earnings
animica ai balance     # wallet balance + spend cap (--watch)
animica ai benchmark   # local inference throughput
```

All commands support `--json` for scripting and degrade gracefully when a node,
model, or wallet isn't available (with a one-line hint, never a stack trace).
Pass `animica ai --no-color …` (or set `$NO_COLOR`) for plain output.

## First run

```bash
pip install animica            # base install stays light
animica ai doctor              # 14 checks: python/cpu/ram/gpu/cuda/torch/extras/
                               # ollama/network/node-rpc/wallet/pool/studio/bittensor
animica ai setup               # pick mode + default model (auto-detects Ollama)
animica ai chat "hello"        # one-shot, or `animica ai chat` for a REPL
```

`setup` writes `~/.animica/config.toml` (mode, default provider/model, optional
payout wallet, optional `max_spend_anm` cap). It never spends ANM.

## Serving an OpenAI-compatible API

```bash
pip install "animica[backend]"             # FastAPI/uvicorn (lazy; friendly hint if missing)
animica ai serve --port 8080               # open, local
animica ai serve --host 0.0.0.0 --api-key $KEY   # network + bearer auth
```

Endpoints (point any OpenAI client at `http://host:port/v1`):

| Endpoint | Notes |
|---|---|
| `GET /v1/models` | configured + local models |
| `POST /v1/chat/completions` | streaming (SSE) + non-streaming, JSON mode, tools-in-prompt |
| `POST /v1/completions` | legacy text completion |
| `POST /v1/embeddings` | embeddings |
| `GET /health` | liveness |

Errors use the OpenAI envelope `{"error": {message, type, code}}`. When
`--api-key` is set, every `/v1/*` call must send `Authorization: Bearer <key>`.

## Paid jobs (AICF marketplace)

```bash
animica ai job estimate "summarize this"          # quote, no spend
animica ai job submit "summarize this" --tier fast # quotes, then asks before paying
animica ai job result <job_id>                     # final text
animica ai job list                                # your local job history
```

**No surprise spending.** `submit` always quotes first, enforces your
`max_spend_anm` cap, and refuses to spend without an interactive confirmation or
`--yes`. Payment is a signed transfer to the AICF treasury built with the
canonical wallet signer (correct ANM→base-unit conversion).

### Be a provider

```bash
animica ai provider register --address <anm…> --tier fast --tier standard
animica ai provider start                  # delegates to the AICF worker runtime
animica ai earnings                        # what you've earned
animica ai benchmark                       # local tokens/sec (no spend)
```

## Embeddings & RAG

```bash
animica ai embed "some text" --json
animica ai rag index ./docs --name handbook
animica ai rag query "how do payouts work?" --name handbook --answer
```

`rag` chunks + embeds local files into `~/.animica/rag/<name>.json` and retrieves
by cosine similarity; `--answer` generates a grounded answer with citations. The
offline `hashing` embedding provider works with no node or network.

## Wallet

```bash
animica ai balance                 # base units + ANM + your spend cap
animica ai balance --watch         # refresh until Ctrl-C
```

Defaults to your configured `payout_wallet`, else your default wallet.

## MCP (use Animica tools from Claude / Cursor / VS Code)

```bash
pip install "animica[mcp]"
animica mcp install claude         # also: cursor | vscode (merges, never clobbers)
animica mcp install vscode --print # preview the JSON without writing
```

Exposes Animica's READ + COMPUTE tools (chain/pool reads, ENA inference, quantum
randomness, Studio estimates) — never signing, spending, or key access.

## `animica up` integration

`animica up` runs mining + AI together. 5.2.0 adds component selection (all
additive — existing invocations are unchanged):

```bash
animica up --plan                       # rich preview; launches nothing
animica up --profile miner              # presets: all | miner | ai | provider
animica up --only studio --only miner   # run only these
animica up --without bittensor          # disable specific components
```

## Config

`~/.animica/config.toml` (written by `setup`, read by every `ai` command):

```toml
mode = "consumer"          # consumer | provider | both

[ai]
provider = "ollama"        # default model provider
default_model = "qwen2.5:7b"
payout_wallet = "anim1…"   # provider earnings / job-submit default
max_spend_anm = 5          # consumer spend cap (0 = ask every time)
use_gpu = false
```

## Install extras

| Extra | Adds | For |
|---|---|---|
| `animica[backend]` | FastAPI/uvicorn | `ai serve` |
| `animica[ai]` | torch/transformers (CPU) | local training/serving |
| `animica[gpu]` | + CUDA quantization | GPU compute |
| `animica[provider]` | backend + on-chain SDK | running as a provider |

The base `pip install animica` keeps node/wallet/miner/RPC light; `chat`,
`models`, `embed`, `rag`, and the `job`/`provider` RPC commands work without any
extra (they speak HTTP to local/configured providers and the node).
