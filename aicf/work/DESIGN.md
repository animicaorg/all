# `aicf.work` — Animica useful-work AI layer (design)

This module is the **source of truth** for the Animica useful-work loop:
users submit jobs, workers claim them, results are verified, AICF settles
rewards on-chain. `apps/chat-animica` becomes a UI proxy onto these RPCs.

## Status

Not yet implemented. The TypeScript prototype shipped at
`apps/chat-animica/src/server/work/` exercises every state transition end
-to-end against real Postgres (42 tests passing, live smoke at
https://studio.animica.org) and is the reference for the Python port.

## Why move it to Python

1. **Truth must live where the chain lives.** AICF rewards, slashing,
   miner reputation, and proof-of-useful-work payouts are settled on
   the Animica side. Putting the work-loop in Python next to
   `aicf.protocol`, `aicf.registry`, and `aicf.queue` removes a network
   hop and lets settlement happen inside one transaction surface.
2. **Reuse the existing primitives.** `aicf.queue.JobKind` already enumerates
   AI / QUANTUM / SFT_TRAIN / DPO_TRAIN / DATA_CURATION / EVAL_RUN / etc.
   The work-loop adds *user-facing* job orchestration on top — it does
   not replace the AICF queue.
3. **Many UIs, one core.** Studio is the first UI, but the explorer,
   buy.animica.org, and the wallet CLI all need to surface the same
   job state. RPC > shared-DB hop > many TypeScript copies.

## Mapping from the TypeScript prototype

| TS entity (`apps/chat-animica/prisma/schema.prisma`) | Python module | RPC namespace |
|---|---|---|
| `WorkJob` | `aicf/work/job.py` | `aicf.work.createJob`, `aicf.work.getJob`, `aicf.work.listJobs` |
| `WorkTask` | `aicf/work/task.py` | implicit (returned in job payload) |
| `WorkerNode` | `aicf/work/worker.py` | `aicf.work.registerWorker`, `aicf.work.heartbeatWorker` |
| `WorkClaim` | `aicf/work/claim.py` | `aicf.work.claimNext`, `aicf.work.heartbeatClaim`, `aicf.work.releaseClaim` |
| `WorkResult` | `aicf/work/result.py` | `aicf.work.submitResult` |
| `WorkVerification` | `aicf/work/verification.py` | `aicf.work.verifyResult` |
| `WorkPayout` | `aicf/work/payout.py` | `aicf.work.approvePayout` (admin) |
| `WorkAuditLog` | `aicf/work/audit.py` | exposed read-only via `aicf.work.auditLog` |

All enum sets, status names, and verdicts must match the TS prototype's
`src/shared/work-schemas.ts` byte-for-byte. The chat-animica routes
will then become a thin proxy: validate Zod → call the matching AICF
RPC → return the response unchanged.

## Storage

Two options, listed in order of preference:

1. **Lean on the existing AICF DB.** `aicf.db` already manages a SQLAlchemy
   session for the AICF state. Add new tables `work_job`, `work_task`,
   `work_worker`, `work_claim`, `work_result`, `work_verification`,
   `work_payout`, `work_audit_log` via an Alembic migration. Schema
   columns mirror Prisma 1:1 — including the unique indexes that drive
   idempotency (`work_job.idempotency_key`, `work_payout.result_id`,
   `work_result(job_id, worker_id, result_hash)`).
2. **Standalone SQLite for dev / Postgres for prod.** Keeps the AICF DB
   pure and avoids the Alembic dependency for cross-team contributors.
   Costs a `JOIN` across DBs for any operator dashboard that wants both
   AICF queue stats and work-loop stats.

Recommend (1) — operationally cleaner once we're past Phase 1.

## Invariants the implementation must enforce

These are the invariants the TS prototype tests; the Python port has to
match. They map to existing SQL-level guarantees, so don't reimplement
them in application code:

1. **Idempotent job creation.** `UNIQUE (idempotency_key) WHERE
   idempotency_key IS NOT NULL`. Second call returns the original job
   with `idempotent: true`.
2. **Lease expiry returns claims to the pool.** Every `claim_next` first
   runs `UPDATE work_claim SET status='expired' WHERE status='active'
   AND lease_expires_at < now()`. No background job needed.
3. **Capability gating.** Worker may only claim tasks whose
   `required_capabilities` ⊆ `worker.capabilities ∩ offered`.
4. **No double payout.** `UNIQUE (result_id)` on `work_payout`. On race,
   read-after-conflict returns the winning row.
5. **Cannot pay an unverified result.** Service-level check: latest
   `work_verification.verdict` must be `'accepted'` before
   `approve_payout` will insert a row.
6. **Result submission idempotent on hash.** `UNIQUE (job_id, worker_id,
   result_hash)`. Same submission returns the existing row.

## RPC method shapes

Mirror the TS Zod schemas in `apps/chat-animica/src/shared/work-schemas.ts`.
Implement with Pydantic v2 models so the same enum tuples are usable from
Python adapters, the RPC dispatcher, and the OpenRPC spec generator.

```python
# aicf/work/schemas.py — sketch
from typing import Literal
from pydantic import BaseModel, Field

JOB_TYPES = Literal[
    "code_generation", "code_fix", "code_review", "test_generation",
    "documentation", "smart_contract", "app_build", "llm_inference",
    "embedding", "summarization", "classification", "data_processing",
    "security_review", "simulation", "research", "creative_asset", "custom",
]

class CreateJobRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=20_000)
    job_type: JOB_TYPES
    reward_amount_anm: str  # decimal string; validated by regex
    creator_wallet: str | None = None
    required_capabilities: list[str] = []
    priority: int = 0
    max_workers: int = 1
    verification_mode: str = "user_acceptance"
    result_visibility: str = "private"
    plan: bool = True
    expires_in_seconds: int | None = None
    idempotency_key: str | None = None
```

## Adapters

Same shape as the TS prototype: `Planner`, `Verifier`, `Payout`, `Rpc`.
The Python port can immediately use:

- **Planner**: call `animica chat --agentic` (or the lower-level
  `agent_runtime.providers.ProviderCascade`) with the new
  AICF-aware RAG corpus. The TS mock planner moves into Phase 1 of
  the Python port as `MockPlanner`; the real one is one prompt away
  because `chat --agentic` already speaks tool calls.
- **Verifier**: bind to the existing `aicf.bench` runners for code/test
  jobs; fall through to a deterministic scoring stub for everything
  else (matches the TS `MockVerifier`).
- **Payout**: build a signed Animica tx with the existing `aicf.treasury`
  helpers; emit through the RPC node. The TS mock is a hex digest.
- **Rpc**: thin wrapper around the existing `aicf.node` JSON-RPC client.

## Migration plan

1. **Phase 1 (current session)**: design doc + TS prototype live.
   ✅ Done. URL: https://studio.animica.org. 42/42 tests passing,
   real-DB smoke loop green.
2. **Phase 2 (next session)**: implement `aicf/work/*` modules with
   in-memory store + RPC methods registered. Mirror the TS Zod schemas
   with Pydantic. Match the 6 invariants above with unit tests
   exercising the same 4 scenarios the TS suite covers.
3. **Phase 3**: add the SQL migration, persist to the AICF DB. Wire
   `apps/chat-animica/app/api/work/*` to proxy via the Animica RPC
   client instead of writing to its own Prisma DB. Keep the Prisma
   tables around as the Studio's read cache for a release cycle, then
   drop.
4. **Phase 4**: real adapters (planner via `animica chat --agentic`,
   payout via signed RPC, verification via `aicf.bench`).

## What lives where after the migration

| Concern | Source of truth | Surface |
|---|---|---|
| Job state machine, claim leasing, payout idempotency | `aicf/work/` (Python) | RPC + WebSocket events |
| ANM math, signing | `aicf/treasury/` (existing) | called by `payout` adapter |
| User-facing form, dashboard, marketplace UI | `apps/chat-animica/app/jobs/` (Next.js) | https://studio.animica.org |
| Worker CLI | `packages/animica-agent` (existing) | calls AICF RPC + Studio for visibility |
| Audit log | `aicf/work/audit.py` | RPC read-only; mirrored to chain receipts |

## What does NOT change

- `aicf.queue.JobKind` and the existing AI/QUANTUM/SFT_TRAIN pipelines
- Studio's contract-generation chat (`apps/chat-animica/app/api/chat`)
- The Animica explorer, wallet CLI, mining stack
- The TS work-layer code shipped this session — it becomes a UI proxy,
  not a parallel system of record.
