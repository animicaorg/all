# Animica Studio — Architecture

Studio is a Modal-style Functions-as-a-Service layer. Its design principle is
**reuse, not reinvention**: Animica already runs a live job broker, a provider
fleet, an execution sandbox, on-chain payment, and payout math. Studio adds the
thin developer-facing glue — a decorator SDK, an image spec, a `function_compute`
job class, a function registry, and `.remote()`/`.map()` ergonomics.

## The rails we build on

| Concern | Existing system | Interface Studio calls |
|---|---|---|
| Job lifecycle (submit/lease/result/settle) | **AICF broker** (`rpc/methods/aicf_jobs.py`) | `aicf.submitInferenceJob`, `aicf.estimateJobCost`, `aicf.settleJob` |
| Worker fleet | **provider-daemon** + unified `animica up` rigs | `POST /provider/daemon/jobs/claim` → `…/result` |
| Execution body | **sandbox-runner** (`packages/sandbox-runner`) | `POST :8004/execute` |
| Payment in | **treasury-escrow leg** (chat-bridge model) | sign `transfer→treasury`, pass `payment_tx_hash` |
| Payout math | **AICF economics** | `split_for_kind('ai', total, policy)` (85/10/5) |
| Function registry | **NEW** sidecar | `aicf.fn.deploy/get/list` over SQLite |
| Tx build / PQ sign | **omni_sdk** | `tx.build.transfer` → `signing.sign_*` → `send.submit_raw` |

## Components (this repo)

```
python/animica/studio/        the importable SDK (ships with `pip install animica`)
  app.py        App + @app.function decorator + Function (.remote/.map/.spawn/.local)
  image.py      Image spec → deterministic content-addressed ref
  serialize.py  pack/unpack a call (ref+JSON default, cloudpickle opt-in)
  _wrapper.py   self-contained in-sandbox bootstrap (no animica import needed)
  runner_local.py   subprocess sandbox execution (zero-infra dev mode)
  runner_remote.py  escrow → submit → poll → unpack against the AICF broker
  runners.py    local-vs-remote selection (auto: remote iff node reachable + wallet)
  client.py     JSON-RPC client for aicf.* broker + aicf.fn.* registry
  billing.py    quote + on-chain ANM escrow leg
  secret.py / volume.py / schedule.py   Secrets, Volumes, Cron/Period
  config.py / errors.py

python/animica/cli/studio_serverless.py   `animica studio run|deploy|serve|fn`
rpc/methods/fn.py                          aicf.fn.* registry          [staged]
provider-daemon/src/adapters/function.ts   function_compute executor   [staged]
```

## The exact wire — `train.remote(42)`

1. **serialize** — `serialize.pack_call(fn, (42,), {})`. Default *ref* mode:
   `{entrypoint: "app:train", args: [42], source_b64: <app.py>}`. (cloudpickle is
   opt-in via `serialization="pickle"`, gated behind a hardened sandbox.)
2. **quote** — `billing.quote_for(fn, client)` → `aicf.estimateJobCost`, else a
   local resource-seconds estimate.
3. **escrow** (onchain billing) — `omni_sdk.tx.build.transfer(caller → treasury,
   cost_nanos)` → `sign_transaction_with_rpc_context` → `tx.sendRawTransaction`
   → `payment_tx_hash`.
4. **submit** — `aicf.submitInferenceJob({spec:{class:"function_compute",
   input:{call, image_ref, image, secrets, volumes}, timeoutSeconds},
   payment:{payment_tx_hash}})` → `{job_id, payment_accepted}`. The node admits
   the job only once the transfer has landed.
5. **claim** — a provider daemon polls `POST /provider/daemon/jobs/claim` and
   leases the job.
6. **execute** — `runtime.ts` dispatches `case 'function_compute'` →
   `runFunctionAdapter` → `POST :8004/execute` running `_wrapper.py` over the
   shipped source → captures the `__ANIMICA_STUDIO_RESULT__<b64>` sentinel.
7. **result** — adapter posts `{output, usage}`; broker marks the job complete and
   accrues the worker's `earnings_pending_animica`.
8. **settle** — payout split by `split_for_kind`; provider IOU until the epoch
   treasury→pool sweep.
9. **poll + unpack** — SDK loops `aicf.settleJob` until terminal, then
   `serialize.unpack_result` → the Python object.

`train.map([...])` fires steps 2–4 concurrently (distinct `job_id`s) and gathers
at step 9. Fan-out lives entirely in the SDK; the broker stays per-job.

In **local mode** (`ANIMICA_STUDIO_MODE=local`, the dev default when no node +
wallet are present) steps 2–8 collapse to a single `runner_local` subprocess —
the SDK is fully demoable with zero infrastructure.

## Key decisions (and why)

- **Reuse the AICF broker, don't fork it.** It already dispatches to a real
  multi-tenant fleet with leasing, replication, and settlement. Studio adds a job
  *class*, not a scheduler.
- **Ref + JSON serialization by default.** A decentralized fleet of untrusted
  providers must not execute arbitrary pickles until the sandbox is hardened
  (gVisor/Firecracker). cloudpickle is opt-in and ROADMAP-gated.
- **Treasury-transfer escrow for v1.** It settles on-chain *today* (proven by the
  chat-bridge). The `marketplace.py` per-job escrow contract (with a real
  `settle()` + refund/slashing) is the better long-term model and is scheduled.
- **Registry beside the broker, not on-chain.** Deploy metadata (name, version,
  image, schedule) needs no consensus — only *payment* does.

See [ROADMAP.md](./ROADMAP.md) for what is built, tested, and staged, including
the two genuine build-outs: sandbox hardening and the on-chain `settle()`.
