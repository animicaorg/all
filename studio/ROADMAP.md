# Animica Studio — Build Status & Roadmap

Status legend: ✅ built & tested · 🟡 built, needs a live node/fleet to exercise ·
⏳ staged/next · 🔬 genuine infra build-out.

## Built & verified (local, zero-infra)

- ✅ **SDK core** — `App`, `@app.function`, `Function.remote/.map/.spawn/.local`,
  `Image`, `Secret`, `Volume`, `Cron`/`Period`. Imports clean; `from animica.studio
  import App`.
- ✅ **Local executor** — real subprocess-sandbox execution via the self-contained
  `_wrapper`. `greet.remote()`, `fib.remote()`, `fib.map(range(10))`, and a numpy
  function all run end-to-end.
- ✅ **CLI** — `animica studio run|deploy|serve|fn`, attached to the existing
  `animica studio` group without disturbing `up/down/status/logs/config`.
  `animica studio run app.py::fib 30` → `832040`.
- ✅ **Serialization** — ref+JSON default (safe); cloudpickle opt-in fallback.
- ✅ **Examples** — `studio/examples/hello.py`.

## Built, awaiting a live node/fleet (🟡)

- 🟡 **Remote runner** — escrow → `aicf.submitInferenceJob` → poll `aicf.settleJob`
  → unpack. Correct against a broker that accepts `class:"function_compute"`.
- 🟡 **Billing leg** — `omni_sdk` transfer-to-treasury escrow + quote.
- 🟡 **Registry client** — `aicf.fn.deploy/get/list` calls.

## Staged / next (⏳)

- ⏳ **`rpc/methods/fn.py`** — the `aicf.fn.*` registry (SQLite sidecar) so the
  broker side of `deploy` exists.
- ⏳ **`provider-daemon/src/adapters/function.ts`** + `runtime.ts` case — the
  worker that actually runs `function_compute` jobs over the sandbox.
- ⏳ **Unified fleet capability** — add function-serving to `animica.unified`
  (`build_plan`) so the same rigs that mine/train/serve also pull Studio jobs.
- ⏳ **studio.animica.org** — web dashboard + landing (no remote terminal), nginx,
  cutover runbook.
- ⏳ **SEO** — `/studio` page positioned as a Modal-class serverless platform.
- ⏳ **Packaging** — `animica` 2.0.0 prep (build + `twine check` + clean-install).

## Genuine build-outs (🔬 — real engineering, not glue)

- 🔬 **Sandbox hardening** — the executor is `subprocess.run` today (no cgroups /
  seccomp / gVisor). Untrusted multi-tenant execution and cloudpickle-by-default
  require gVisor/Firecracker + an OCI image puller. Threat model:
  `docs/compute-platform/THREAT_MODEL.md`.
- 🔬 **On-chain `settle()`** — `contracts/compute/marketplace.py` holds escrow but
  has no verification-gated release/refund/slashing method. Writing it upgrades
  billing from treasury-transfer+IOU to true per-job escrow.
- 🔬 **Volume replication** — DA-backed (or MinIO) content-addressed volumes so any
  worker sees the same state. `Volume.commit()` raises clearly until then.
- 🔬 **Image distribution** — pip-install-at-invocation (v1, behind hardened
  sandbox) → full OCI pull (target).

## Open decisions (need a human call)

1. Serialization default in distributed mode: ref+JSON (safe) vs cloudpickle
   (Modal-parity) — currently ref+JSON, cloudpickle gated.
2. Billing default: on-chain per-call vs credits ledger for high fan-out.
3. IOU settlement acceptable for launch, or is the consensus treasury→pool sweep
   a hard prerequisite?
