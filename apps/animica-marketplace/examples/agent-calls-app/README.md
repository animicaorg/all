# agent-calls-app

One deployed function invoking another with `animica.call()` — the primitive underneath the
agent-to-agent economy. By default it delegates to `examples/anm-toolkit` (the paid example),
so the nested call is a **paid** call and the delegation's exact cost is visible.

## What it demonstrates

* **`animica.call(target, payload)`** — nested execution of another published function
  (`"owner/slug"`, or a bare slug for a global PUBLIC lookup). The nested run is a real
  `CloudExecution` row with `parentExecutionId`/`rootId`/`depth` set, so the whole trace is
  reconstructable.
* **The `CALL_FUNCTION` capability** — without it, every `animica.call` is refused with
  `CapabilityDenied`.
* **The shared budget** — the entire call tree draws down one authorization
  (`maxSpendNanm` from the root caller, or the pre-execution estimate when not given).
  A nested call whose worst-case estimate would exceed the remaining budget is refused with
  `budget_exceeded` **before** it runs.
* **Depth and fan-out caps** — depth > 4 or more than 16 nested calls per execution are
  refused; self-calls are always refused.
* **Cost transparency** — the host returns `{status, result, request_id, cost_nanm}` for the
  nested call; this function passes them through.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 60 000 ms |
| memory | 128 MB |
| capabilities | `CALL_FUNCTION` |
| per-call surcharge | 0 nANM |

## Invoke (requires an API key — the nested target is a paid function)

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/agent-calls-app \
  -H "authorization: Bearer $ANM_KEY" \
  -H 'content-type: application/json' \
  -d '{"payload": {"op": "convert", "anm": "0.25"}}'
```

Response shape:

```json
{"target": "examples/anm-toolkit", "nested_status": "succeeded",
 "nested_request_id": "rq_...", "nested_cost_nanm": "5...", "result": {"op": "convert", ...}}
```

The root execution's receipt covers its own metered price; the nested execution settles
separately (same caller pays, `examples` earns the toolkit's surcharge) — both request ids
appear in the caller's execution history.
