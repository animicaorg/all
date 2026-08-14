# ai-python-workflow (deployed as `chain-pulse`)

The full data-workflow shape: **fetch → transform with pandas/numpy → summarize with AI**,
using live Animica chain data as the dataset.

## What it demonstrates

* **`animica.http.fetch`** (`HTTP_FETCH` capability) — the sandbox has **no network at all**
  (`--network none`); the host performs the request on the function's behalf and enforces
  https-only, SSRF/private-target blocking, response-size caps and timeouts. The fetched
  bytes are metered as egress. Here it POSTs JSON-RPC to the public Animica node
  (`chain.getHead`, then `chain.getBlockByNumber` for a window of recent blocks).
* **The curated package set** — `pandas` and `numpy` come pre-installed in the sandbox image
  (declared in the deploy's `packages` list). A DataFrame of block heights/timestamps is
  reduced with numpy into block-interval statistics.
* **`animica.ai.infer`** (`AI_INFERENCE` capability) — turns the measured statistics into a
  one-paragraph analyst narrative. If the miner network cannot serve inference at that
  moment, the function returns a deterministic report composed from the same real
  measurements and honestly reports `engine: "deterministic-fallback"`.
* Multiple metered resources in one execution: CPU + memory + egress + AI tokens, all
  itemized in the receipt.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 120 000 ms |
| memory | 256 MB |
| capabilities | `HTTP_FETCH`, `AI_INFERENCE` |
| packages | `numpy`, `pandas` |
| per-call surcharge | 0 nANM |

## Invoke (requires an API key)

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/chain-pulse \
  -H "authorization: Bearer $ANM_KEY" \
  -H 'content-type: application/json' \
  -d '{"blocks": 12}'
```

Response shape:

```json
{"tip_height": 65321, "window": 12,
 "block_time_seconds": {"blocks": 12, "mean_s": 41.3, "median_s": 33.5, "min_s": 2, "max_s": 130},
 "narrative": "...", "engine": "animica-ai" | "deterministic-fallback", "request_id": "rq_..."}
```
