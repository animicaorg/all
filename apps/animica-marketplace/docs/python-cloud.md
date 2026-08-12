# Animica Python Cloud — operator & engineering reference

> Write Python. Deploy to Animica. Get paid when people use it.

This document is the operator-side reference for the Python Cloud subsystem inside
`apps/animica-marketplace`. Developer-facing documentation lives at `/docs/cloud` on the site;
this file covers architecture, the data model, the economic flow, the security model, every
environment variable, the deployment procedure and the background workers.

---

## 1. Architecture

**The one sentence that must never be overstated:** deployments are **anchored on-chain**
(source hash + artifact hash + DA blob id inside a signed DEPLOY tx) and **executed
off-chain** in a hardened container. Animica consensus does not execute arbitrary Python —
vm_py CALL transactions revert on mainnet by design (raw exec is fail-closed; enabling it
would be node RCE).

```
                       ┌────────────────────────────────────────────────────────┐
 internet ── nginx ──► │ Next.js app :4950  (app/api/cloud/v1/**)               │
                       │                                                        │
                       │  lib/cloud/                                            │
                       │   ratelimit ─► entitlements ─► pricing ─► executor ────┼──► docker run (one
                       │        config (all knobs)         │          │         │    container per
                       │   validate (spawns sandbox/       │       sandbox.ts   │    execution,
                       │   validate.py, AST-only)          │       host broker  │    --network none)
                       │   deploy ─► anchor ───────────────┼──────────┐         │
                       │   settle ─► lib/ledger (append-only ledger)  │         │
                       └──────────────┬──────────────────────────────┬┴─────────┘
                                      │                              │
                        Postgres 127.0.0.1:5443            Animica node RPC :8545
                        (Prisma; Cloud* + Pricing* +       (da.put/da.get, DEPLOY tx via
                         Ledger* + Finance* tables)         the animica CLI, chain reads)
```

Components:

| piece | file(s) | role |
| --- | --- | --- |
| config | `lib/cloud/config.ts` | THE single place for flags, limits, economics, plans, founding program, managed offerings, runtime wiring. Everything env-overridable. |
| pricing | `lib/cloud/pricing.ts` | `activePolicy()` (DB `PricingPolicy`, 15 s cache) · `quote()` customer price · `costOf()` COGS · `splitOf()` exact split · `minPriceForMargin()` floor · `estimate()` pre-flight · `priceForFailure()` |
| entitlements | `lib/cloud/entitlements.ts` | plan resolution (subscription ∪ founding grant), slot/quota checks against **live counts**, metered monthly quotas + USD overage accrual, concurrency/priority/log-retention/fee-rate derivation |
| executor | `lib/cloud/executor.ts` | one invocation end-to-end: admit → quote → authorize funds → record → run → meter → price → settle. Also the **capability broker** (every `animica.*` host call is authorized here against server-held state). |
| sandbox | `lib/cloud/sandbox.ts` + `sandbox/runner.py` + `sandbox/Dockerfile` | one hardened container per execution; unforgeable line protocol; host-measured wall time; global semaphore so the co-hosted mainnet node is never starved; `reapOrphans()` |
| validation | `lib/cloud/validate.ts` + `sandbox/validate.py` | AST-only pre-deploy validation, fail-closed on validator faults |
| deploy | `lib/cloud/deploy.ts` | lifecycle DRAFT→…→ACTIVE, immutable versions, canonical artifact hashes, rollback-as-new-deployment, node function-registry registration (best-effort) |
| anchor | `lib/cloud/anchor.ts` | DA blob put/get (content-address verified both ways) + DEPLOY (t=1) broadcast via the `animica` CLI + confirmation polling. **Never fabricates a txid.** |
| settle | `lib/cloud/settle.ts` | exactly-once, all-or-nothing, exact-sum settlement through `lib/ledger.ts`; promotional credits drawn before balance; app purchases |
| dispatch | `lib/cloud/dispatch.ts` | fleet lane decision + provider registry + lease-based job queue (media-miner pattern) |
| rate limits | `lib/cloud/ratelimit.ts` | in-process burst limiter + durable per-identity free-tier counters (salted-hash identities) + platform free-tier cost ceiling |
| finance | `lib/cloud/finance.ts` + `scripts/cloud-finance-rollup.ts` | FinanceDaily rollup from authoritative rows; ANM/USD reference only from the real price feed |

## 2. Data model (Prisma)

All in `prisma/schema.prisma` (owned by the core agent — reference only):

| model | purpose |
| --- | --- |
| `CloudFunction` | the unit of deployment/execution/earnings; config envelope; capability declarations; surcharge; denormalized counters are caches only |
| `CloudFunctionVersion` | **immutable** source snapshot: verbatim source, `sourceSha3`, `artifactSha3`, packages, validation report, deploy-time estimate |
| `CloudDeployment` | one deployment attempt with status, per-step `logsJson`, `daBlobId`, `anchorTxid/Height/Confirms`, endpoint |
| `CloudExecution` | THE authoritative record of one invocation: status, measured usage, customer money (price/fee/developer/provider, `feeBps` snapshot), internal COGS + contribution, `billed` idempotency anchor, trace links (`parentExecutionId`/`rootId`/`depth`) |
| `CloudExecutionLog` | structured logs, plan-bounded retention |
| `CloudApp` / `CloudAppPurchase` / `CloudReview` / `CloudReport` | marketplace listing, exact-split purchases, verified-user reviews, moderation queue |
| `CloudAgent` | persistent program bound to a function; optional own anim1 address; per-run + daily spend caps enforced atomically |
| `CloudGrant` | a user's revocable authorization (capabilities + per-call/per-exec/daily caps + payee allowlist) for an app/agent/function |
| `CloudSecret` | AES-256-GCM-sealed secrets (also backs `animica.state` under reserved `__state__…` names) |
| `CloudSchedule` | interval/cron schedules; auto-disable bookkeeping |
| `CloudProvider` / `CloudJob` | fleet registry (sha3'd bearer tokens) + lease-based job queue |
| `CloudCodeDenylist` | blocked source/artifact fingerprints |
| `CloudAuditLog` | every admin action touching money/pricing/availability |
| `PricingPolicy` | versioned economics; ACTIVE row prices new executions; old rows retained so history is reproducible |
| `CloudCredit` | promotional credits, drawn before balance, never withdrawable, folded into COGS as `cogsPromoNanm` |
| `FoundingDeveloper` | the first-100 program: time-boxed Pro grant, reduced fee (snapshotted), credits, featured flag |
| `UsageCounter` / `UsageCharge` | durable metered quotas (monthly UTC buckets) and USD overage accrual |
| `FinanceDaily` / `FinanceAlert` / `ReconciliationReport` | daily business snapshot cache, alerting, reconciliation evidence |
| `EnterpriseInquiry` | sales pipeline |

## 3. Economic flow

One successful third-party execution:

```
1. admission   burst limit → plan resolution → concurrency → monthly execution quota
2. estimate    estimate(timeout, memory, surcharge, feeBps) → typical + worst case
3. funding     checkAffordable(balance + credits − in-flight reservations ≥ worst case)
4. record      CloudExecution row (RUNNING, quotedNanm = worst case, feeBps snapshot)
5. run         one container; host calls metered as they happen (AI tokens, egress, nested spend)
6. meter       billed CPU = host-measured wall ms; memoryMbMs = ms × configured MB
7. price       quote(usage) = base + cpu + mem + ai + egress (+ gpu) + surcharge,
               raised to minPriceForMargin(COGS, feeBps, targetMargin) if below the floor
8. settle      ONE transaction: billed-claim (exactly-once) + ledger postings
                 caller   −(price − credit)   USAGE_DEBIT
                 treasury −credit             ADJUSTMENT     (promo portion)
                 developer +developerNanm     SALE_CREDIT
                 provider  +providerNanm      SALE_CREDIT    (fleet lane only)
                 treasury  +platformFeeNanm   FEE
               plus COGS/contribution recorded on the row; margin alert if materially negative
9. after       compute-unit + AI-unit quotas metered from real usage (overage → UsageCharge)
```

Failure economics (§46): REJECTED/never-ran ⇒ charge nothing and refund quota; FAILED/TIMEOUT
⇒ charge metered resource cost only — no surcharge, no margin floor, `feeBps` 10000 (the
developer earns nothing from a failed run; the recovery goes to the treasury).

Free tier: anonymous callers (salted-IP identity) and own-function calls don't pay ANM; the
work is still costed and recorded (`freeTier=true`, `contribution = −COGS`) so
`/admin/profitability` is truthful. A configurable monthly free-tier COGS ceiling can pause
free execution platform-wide.

Fee-rate discipline (§88): the fee applied to any sale is **snapshotted** onto the row at
settlement time. Policy changes, founding-window expiry — nothing rewrites history. The
Founding Developer rate is `min(fd.feeBps, policy.platformFeeBps)` until `feeUntil`.

Verification: `scripts/cloud-e2e.ts` (money-path proof with ledger assertions) and
`scripts/cloud-examples.ts` (deploys + executes the six documented examples end-to-end).
Daily, `scripts/cloud-reconcile.ts` re-verifies balance==SUM(ledger), price==fee+dev+prov,
zero-sum-per-ref, PayPal↔subscription mapping and provider caches — writing reports and
alerts, never "fixing" anything.

## 4. Security model

Layered, guest-hostile (full developer-facing text: `/docs/cloud/security`):

1. **OS isolation** (the boundary): one `docker run` per execution — `--network none`,
   `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`, non-root uid 10001,
   cgroup memory (+ equal swap) / CPU / pids caps, noexec+nosuid tmpfs, no socket/host/env
   mounts. Image contains no Animica code or credentials; setuid bits stripped.
2. **Mediated capabilities**: every privileged operation is a host-broker RPC authorized
   against the deployment's declared capabilities, the caller's `CloudGrant` (with per-call /
   per-exec / daily caps and payee allowlist, atomically claimed), tree budgets, depth and
   quotas. Secrets never leave the gateway; fleet dispatch refuses functions with
   capabilities or secrets.
3. **Unforgeable protocol**: the runner privatizes the control descriptors before importing
   user code; frames carry a per-execution token; results are size-capped; billing uses
   host-measured time.
4. **Deploy-time controls**: fail-closed AST validation, code denylist, recomputed-hash
   immutability check before anchoring, honest unanchored deployments (reason recorded, txid
   never fabricated).
5. **HTTP egress (`animica.http.fetch`)**: https-only, credential-free URLs, no IPv6
   literals, internal-hostname and private/loopback IP blocks re-checked after DNS
   resolution, redirects not followed, hop headers stripped, 512 KB cap, 1–30 s timeout.
6. **Abuse control**: burst + durable free-tier counters, global/queue/account concurrency,
   deploy-rate gates, suspension flags checked per execution, admin audit log.

## 5. Environment variables

Everything is env-overridable with these defaults (see `lib/cloud/config.ts` unless noted).
Money values are integer nANM unless the name says CENTS/BPS.

### Feature flags

| var | default | meaning |
| --- | --- | --- |
| `PYTHON_CLOUD_ENABLED` | `true` | master kill switch for the whole subsystem |
| `CLOUD_MARKETPLACE_ENABLED` | `true` | apps catalog + purchases |
| `CLOUD_AGENTS_ENABLED` | `true` | agents API |
| `CLOUD_COMPUTE_MARKET_ENABLED` | `true` | fleet dispatch (off ⇒ everything runs locally) |
| `CLOUD_SCHEDULES_ENABLED` | `true` | schedules API + scheduler worker honor this |
| `CLOUD_ANCHOR_ENABLED` | `true` | off ⇒ deployments complete but are honestly marked unanchored |
| `CLOUD_BILLING_ENABLED` | `true` | USD plan checkout |

### Execution envelope (hard ceilings)

| var | default | meaning |
| --- | --- | --- |
| `CLOUD_MAX_SOURCE_BYTES` | `262144` | max function source size |
| `CLOUD_MAX_TIMEOUT_MS` / `CLOUD_DEFAULT_TIMEOUT_MS` | `300000` / `30000` | execution wall-clock budget (min 1000, not env) |
| `CLOUD_MAX_MEMORY_MB` / `CLOUD_DEFAULT_MEMORY_MB` | `1024` / `256` | container memory cap (min 64) |
| `CLOUD_MAX_PIDS` | `128` | cgroup pids limit per container |
| `CLOUD_MAX_OUTPUT_BYTES` | `1048576` | result + stdout cap |
| `CLOUD_MAX_REQUEST_BYTES` | `524288` | request payload cap |
| `CLOUD_MAX_LOG_LINES` / `CLOUD_MAX_LOG_LINE_CHARS` | `500` / `2000` | per-execution log caps |
| `CLOUD_MAX_TMPFS_MB` | `64` | writable /tmp size |
| `CLOUD_MAX_CALL_DEPTH` / `CLOUD_MAX_CALLS_PER_EXECUTION` | `4` / `16` | nested-call guards |
| `CLOUD_MAX_AI_CALLS_PER_EXECUTION` / `CLOUD_MAX_AI_TOKENS_PER_EXECUTION` | `8` / `8192` | per-execution AI budget |
| `CLOUD_GLOBAL_CONCURRENCY` | `6` | sandbox semaphore — the box also runs the mainnet node; never starve it |
| `CLOUD_ACCOUNT_CONCURRENCY` | `2` | default per-account concurrent executions (plans raise it, capped by global) |
| `CLOUD_QUEUE_MAX_DEPTH` | `500` | admission queue bound |
| `CLOUD_RATE_ANON_PER_MIN` / `CLOUD_RATE_USER_PER_MIN` | `10` / `60` | burst limits on invocation |
| `CLOUD_RATE_DEPLOY_PER_HOUR` / `CLOUD_RATE_AIGEN_PER_HOUR` | `30` / `20` | deploy / AI-generation route rates |
| `CLOUD_MAX_SECRET_BYTES` | `8192` | max secret value size |
| `CLOUD_MIN_SCHEDULE_MINUTES` | `5` | platform floor under every plan's schedule interval |
| `CLOUD_JOB_LEASE_SECONDS` / `CLOUD_JOB_MAX_ATTEMPTS` | `300` / `3` | fleet job lease + retry bound |
| `CLOUD_PROVIDER_STALE_SECONDS` | `120` | heartbeat staleness ⇒ provider IDLE |
| `CLOUD_SANDBOX_CPUS` | `1` | `--cpus` per container (read in `sandbox.ts`) |

### Economics (bootstrap defaults — the ACTIVE `PricingPolicy` row wins at runtime)

| var | default | meaning |
| --- | --- | --- |
| `CLOUD_BASE_CALL_NANM` | `100000` | base price per invocation (0.0001 ANM) |
| `CLOUD_CPU_MS_NANM` | `20` | price per billed CPU-ms |
| `CLOUD_MEM_MB_MS_NANM` | `1` | price per MB-ms |
| `CLOUD_AI_TOKEN_IN_NANM` / `CLOUD_AI_TOKEN_OUT_NANM` | `1000` / `3000` | AI token prices |
| `CLOUD_EGRESS_KB_NANM` | `50` | price per egress KB (rounded up) |
| `CLOUD_GPU_MS_NANM` | `400` | price per GPU-ms |
| `CLOUD_PLATFORM_FEE_BPS` | `2000` | Animica take rate (20%) |
| `CLOUD_PROVIDER_SHARE_BPS` | `1000` | provider share when the fleet ran it (10%) |
| `CLOUD_COST_CPU_MS_NANM` / `CLOUD_COST_MEM_MB_MS_NANM` | `6` / `1` | internal unit costs (COGS) |
| `CLOUD_COST_AI_TOKEN_NANM` / `CLOUD_COST_EGRESS_KB_NANM` | `400` / `10` | internal unit costs (COGS) |
| `CLOUD_COST_PER_CALL_NANM` | `20000` | fixed per-call infra allocation (COGS) |
| `CLOUD_TARGET_MARGIN_BPS` | `6000` | gross-margin target protected by the price floor |
| `CLOUD_ENFORCE_MIN_MARGIN` | `true` | apply `minPriceForMargin` (floor = COGS × 12.5 at defaults) |
| `CLOUD_FREE_EXECUTIONS_PER_DAY` / `_PER_MONTH` | `50` / `500` | free-tier allowance per identity |
| `CLOUD_FREE_AI_TOKENS_PER_DAY` | `20000` | free AI token allowance |
| `CLOUD_FREE_TIER_CEILING_NANM` | `0` (off) | monthly platform-wide free-tier COGS ceiling |
| `CLOUD_ANM_USD_FLOOR_MICROS` | `0` (off) | optional ANM/USD floor for pricing sanity |
| `CLOUD_NEG_MARGIN_ALERT_NANM` | `1000000` | FinanceAlert threshold for below-cost executions (`executor.ts`) |

### Plans / overages / founding / managed services

| var | default | meaning |
| --- | --- | --- |
| `CLOUD_PLAN_DEVELOPER_CENTS` / `PRO` / `BUSINESS` / `ENTERPRISE_FROM` | `2900` / `9900` / `49900` / `150000` | USD plan prices (DB `Plan` row is authoritative for checkout) |
| `CLOUD_OVERAGE_EXEC_CENTS_PER_1K` | `20` | overage: per 1,000 executions (`entitlements.ts`) |
| `CLOUD_OVERAGE_CPU_CENTS_PER_1K` | `150` | overage: per 1,000 CPU-seconds |
| `CLOUD_OVERAGE_AI_CENTS_PER_1K` | `300` | overage: per 1,000 AI units (1k tokens each) |
| `FOUNDING_DEV_SEATS` | `100` | founding program seats |
| `FOUNDING_DEV_PRO_MONTHS` | `3` | Pro grant duration |
| `FOUNDING_DEV_FEE_BPS` / `FOUNDING_DEV_FEE_MONTHS` | `1000` / `12` | reduced fee + window |
| `FOUNDING_DEV_CREDITS_NANM` | `25 ANM` | execution credits granted |
| `FOUNDING_DEV_FEATURE_MIN_EXECUTIONS` | `25` | earned-feature threshold |
| `FOUNDING_DEV_AUTO_ACCEPT` | `true` | auto-accept applications while seats remain |
| `SVC_PRIVATE_CLOUD_SETUP_CENTS` / `_MONTHLY_CENTS` | `500000` / `150000` | managed offering prices |
| `SVC_DEDICATED_AI_MONTHLY_CENTS` | `150000` | managed offering price |
| `SVC_INTEGRATION_FROM_CENTS` / `SVC_CUSTOM_BUILD_FROM_CENTS` | `500000` / `250000` | quoted-from prices |

### Runtime / infrastructure

| var | default | meaning |
| --- | --- | --- |
| `CLOUD_SANDBOX_IMAGE` | `anm-pycloud-runtime:1` | the sandbox image (build: `sandbox/build-image.sh`) |
| `CLOUD_DOCKER_BIN` | `docker` | docker binary |
| `CLOUD_WORK_DIR` | `/var/lib/animica-pycloud` | artifact staging dir |
| `CLOUD_SANDBOX_UID` / `GID` | `10001` | in-container uid:gid |
| `PUBLIC_BASE_URL` | `https://animica.dev` | endpoint base shown to developers |
| `CLOUD_ANCHOR_WALLET_LABEL` | `mldsamain` | CLI wallet label that signs anchor DEPLOY txs (must exist in `ANIMICA_WALLETS_FILE` and hold gas) |
| `CLOUD_ANCHOR_CONFIRMATIONS` | `12` | finality depth for anchors |
| `CLOUD_ANCHOR_CLI_TIMEOUT_MS` | `60000` | CLI broadcast timeout (`anchor.ts`) |
| `CLOUD_ANCHOR_INCLUSION_WAIT_MS` | `90000` | bounded in-pipeline wait for inclusion (`deploy.ts`) |
| `CLOUD_PYTHON_BIN` | `/root/animica/.venv/bin/python3` → `python3` | interpreter for the validator (`validate.ts`) |
| `CLOUD_VALIDATOR_PATH` | `<app>/sandbox/validate.py` | validator script path |
| `ANM_PRICE_FILE` | `/var/www/animica.dev/anm-price.json` | ANM/USD reference feed (written by anm-price.timer) |
| `CLOUD_FLEET_DEV_CAP` | `20` | per-developer in-flight fleet jobs (`dispatch.ts`) |
| `CLOUD_PROVIDER_REPUTATION_FLOOR` | `-8` | dispatch skips providers below this |

### Background workers

| var | default | meaning |
| --- | --- | --- |
| `CLOUD_SCHEDULER_ENABLED` | off (`1` to enable) | master switch for the schedule ticker |
| `CLOUD_SCHEDULER_CONCURRENCY` | `3` | invocation pool per tick |
| `CLOUD_SCHEDULER_INFLIGHT_CAP` | global concurrency | DB-wide cap on schedule-lane in-flight executions |
| `CLOUD_SCHEDULER_MAX_PER_TICK` | `50` | claim bound per tick |
| `CLOUD_SCHEDULE_MAX_FAILURES` | `5` | consecutive hard failures ⇒ auto-disable |
| `CLOUD_SCHEDULER_TICK_BUDGET_MS` | `600000` | stop starting new invocations after this much tick time |
| `CLOUD_JANITOR_ENABLED` | off (`1` to enable) | hygiene sweeps master switch |
| `CLOUD_RECONCILE_ENABLED` | off (`1` to enable) | daily reconciliation master switch |
| `CLOUD_FINANCE_ROLLUP_ENABLED` | on (`0` to disable) | FinanceDaily rollup (cache-only, moves no money) |

## 6. Deployment procedure

1. **Sandbox image**: `cd apps/animica-marketplace/sandbox && ./build-image.sh` — builds
   `anm-pycloud-runtime:1` (python:3.12-slim + the curated pinned package set + runner.py,
   non-root, setuid-stripped). Rebuild + bump `CLOUD_SANDBOX_IMAGE` to change the package set.
2. **Schema**: `npm run db:push` (Prisma → Postgres 127.0.0.1:5443).
3. **Seed the pricing policy**: `npx tsx scripts/cloud-seed.ts` — creates PricingPolicy v1
   from the env defaults, idempotent, never rewrites an existing policy. (Pricing changes
   afterwards go through the admin pricing console, which writes a NEW version.)
4. **Anchor wallet**: ensure the label in `CLOUD_ANCHOR_WALLET_LABEL` exists in
   `ANIMICA_WALLETS_FILE` and holds ANM for gas. Zero balance ⇒ deployments activate
   honestly-unanchored with the reason recorded.
5. **App**: `npm run build && systemctl restart animica-marketplace` (Next.js on :4950 behind
   nginx).
6. **Workers**: copy `deploy/systemd/animica-cloud-{scheduler,janitor,reconcile}.{service,timer}`
   into systemd, set the `CLOUD_*_ENABLED=1` env in the units, `systemctl enable --now` the
   timers. The units use flock + a pg advisory lock, so double-installation is safe.
7. **Prove the money path**: `npx tsx scripts/cloud-e2e.ts` (asserts debits, credits, fee,
   exact sum, zero-sum ledger, exactly-once settlement, balance invariant) and
   `npx tsx scripts/cloud-examples.ts` (deploys + runs the six documented examples; also the
   content behind `/docs/cloud/examples`).

## 7. Background workers

| worker | cadence | what it does |
| --- | --- | --- |
| `scripts/cloud-scheduler.ts` | 1 min timer | fires due `CloudSchedule` rows through the normal execution path (owner pays, full admission control); strict 5-field UTC cron or intervals; conditional claim on the exact `nextRunAt` read so concurrent ticks can't double-fire; soft admission errors defer, hard failures auto-disable after `CLOUD_SCHEDULE_MAX_FAILURES` |
| `scripts/cloud-janitor.ts` | 5 min timer | six independent sweeps: per-plan log retention deletes; orphaned sandbox containers reaped by label; expired fleet leases requeued/expired (expiring a job fails its execution so reservations release); silent providers → IDLE; RUNNING/QUEUED watchdog (crashed-process rows closed as TIMEOUT/CANCELLED); expired grants stamped revoked |
| `scripts/cloud-reconcile.ts` | daily (after UTC midnight) | verifies, never fixes: balance==SUM(ledger) per account; price==fee+dev+prov and zero-sum ledger per execution; PayPal captures ↔ subscription states; provider earnings caches. Writes `ReconciliationReport` + `FinanceAlert` on mismatch. Refreshes FinanceDaily first via `rollupFinanceDay()` |
| `scripts/cloud-finance-rollup.ts` | invoked by reconcile (backfill: `--day YYYY-MM-DD`) | FinanceDaily snapshot from authoritative rows; ANM/USD only from the real price feed or a same-day snapshot — never invented |

## 8. Examples & docs surfaces

* `/docs/cloud` — 16 developer documentation pages (server-rendered, live values from config
  + the active pricing policy + the examples' DB rows).
* `examples/` — six example functions (source + README each); `scripts/cloud-examples.ts`
  deploys them under the designated `examples` account (address
  `anim1examplesdev0cloud0demo0acct0000`, Founding-Pro grant with expired fee benefit) and
  executes each one, printing real results and receipts. Idempotent; `--cleanup` reverses it.
* Known operational dependency: the two AI examples exercise `animica.ai.infer`, which needs
  the miner network to be serving inference. When it is not, they degrade to their documented
  deterministic fallbacks and say so in the `engine` field — re-run the script during healthy
  serving to capture the `animica-ai` path.
