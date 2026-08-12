# ai-summarizer

Summarizes text using `animica.ai.infer` — the capability-mediated AI host call. The function
must declare the `AI_INFERENCE` capability at deploy time or every `ai.infer` call is refused
by the host broker with `CapabilityDenied`.

## What it demonstrates

* `animica.ai.infer(prompt, max_tokens=...)` — AI inference served by the Animica miner
  network, metered per token (`aiTokenInNanm` / `aiTokenOutNanm` in the active pricing
  policy) and billed to the execution.
* Per-execution AI budgets: at most `CLOUD_MAX_AI_CALLS_PER_EXECUTION` calls (default 8) and
  `CLOUD_MAX_AI_TOKENS_PER_EXECUTION` tokens (default 8192); exceeding them raises
  `animica.BudgetExceeded`.
* The error taxonomy: when no healthy miner is serving, `ai.infer` raises
  `animica.AnimicaError`. This function catches it and degrades to a real, deterministic
  extractive summary (frequency-scored sentence selection) computed inside the sandbox, and
  reports `engine: "extractive-fallback"` instead of `engine: "animica-ai"`. The caller
  always gets a genuine summary and an honest statement of how it was produced.
* Using `ctx` (the optional second argument): `ctx.request_id` ties the response to the
  execution receipt.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 90 000 ms (leaves room for the AI attempt + fallback) |
| memory | 256 MB |
| capabilities | `AI_INFERENCE` |
| per-call surcharge | 0 nANM |

## Invoke

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/ai-summarizer \
  -H 'content-type: application/json' \
  -d '{"text": "<a few paragraphs of text>"}'
```

Response shape:

```json
{"summary": "...", "engine": "animica-ai" | "extractive-fallback", "chars_in": 1234, "request_id": "rq_..."}
```

Note: AI tokens make this function's price vary per call — the exact charge is always in the
`x-animica-cost-nanm` response header and the execution receipt.
