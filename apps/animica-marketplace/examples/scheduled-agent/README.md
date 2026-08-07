# scheduled-agent

A chain-height monitor that runs on a schedule and remembers its previous runs. Demonstrates
the two capabilities that make long-lived agents possible: `READ_CHAIN` (live chain data) and
`PERSIST_STATE` (the per-function encrypted key/value store).

## What it demonstrates

* **CloudSchedule** — `scripts/cloud-examples.ts` creates an hourly interval schedule for
  this function. The platform scheduler (`scripts/cloud-scheduler.ts`, run by
  `animica-cloud-scheduler.timer`) fires due schedules through the normal execution path:
  `callerKind: "schedule"`, the schedule's owner as the paying account, full admission
  control (plan, quota, affordability) applied. Consecutive hard failures auto-disable a
  schedule.
* **`animica.state`** — `state.get/set/delete` persist JSON values between executions,
  encrypted at rest (AES-256-GCM), scoped to this function. Limits: 200 keys per function,
  16 KB per value.
* **`animica.chain.head()`** — read-only chain access via the `READ_CHAIN` capability.
* Memory discipline: the run history is bounded (last 48 runs) so the state value can never
  outgrow its 16 KB cap.

## Deploy configuration (set by `scripts/cloud-examples.ts`)

| setting | value |
| --- | --- |
| entrypoint | `main` |
| timeout | 15 000 ms |
| memory | 128 MB |
| capabilities | `READ_CHAIN`, `PERSIST_STATE` |
| schedule | interval, every 60 minutes |

## Economics of scheduled runs

A scheduled run is billed to the schedule's **owner**. Because the owner is also this
function's developer, the executor treats it as an own-function call: it consumes the plan's
monthly execution/compute quota rather than charging ANM.

## Invoke on demand

```bash
curl -s https://animica.dev/api/cloud/v1/fn/examples/scheduled-agent -X POST
```

Response shape (second run onward):

```json
{"height": 65330, "new_blocks_since_last_run": 4, "seconds_since_last_run": 3600,
 "runs_recorded": 2, "caller": "account", "request_id": "rq_..."}
```
