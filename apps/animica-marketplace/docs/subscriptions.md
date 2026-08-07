# Animica.dev platform subscriptions (fiat SaaS tiers) + Animica Workers

> "AI is free. Put it to work."

This document describes the PayPal subscription + entitlement system that powers
`/pricing`, `/settings/billing`, `/workers`, and `/admin/billing` on animica.dev.

The core proposition:

- **Free** — create and experiment. All existing free AI stays free.
- **Starter $9.99/mo** — start automating (1 Worker, 50 scheduled executions/mo).
- **Pro $29.99/mo** — build and monetize (5 Workers, production API keys, marketplace selling, teams). MOST POPULAR.
- **Operator $79.99/mo** — run autonomous infrastructure (20 Workers, external triggers, white-label).
- **Business $199.99/mo** — build a business on Animica (75 Workers, client workspaces, reseller features).

Nothing here paywalls the existing free AI (`/v1`, homepage chat, media studios) and nothing
sells storage. Tiers gate **automation (Workers), production APIs, .anm deployment counts,
marketplace selling, teams, and business features**.

---

## Architecture at a glance

```
Browser (/pricing PricingClient)
   │  POST /api/mkt/v1/billing/subscribe        ← wallet session (Account)
   │  → draft PlanSubscription (PENDING) + {paypalPlanId, clientId, env}
   │  PayPal Buttons createSubscription(plan_id, custom_id=subRowId)
   │  onApprove → POST /api/mkt/v1/billing/confirm {id, subscriptionId}
   │  → server re-fetches sub from PayPal, asserts plan/custom_id/money (cents), ACTIVE ⇒ entitlements
   ▼
PayPal → POST /api/mkt/v1/billing/paypal/webhook   (signature-verified, FAIL-CLOSED on
        missing SUBS_PAYPAL_WEBHOOK_ID; BillingEvent row per PayPal event id = idempotency)
   ▼
PlanSubscription.status: PENDING → ACTIVE ⇄ PAST_DUE → GRACE_PERIOD → SUSPENDED → CANCELED
   ▼
lib/plan.ts  — THE centralized entitlement layer (all checks server-side, DB-backed):
   getAccountPlan(accountId) → { key, limits, state }
   hasEntitlement / getPlanLimit / getUsage / remainingUsage / consumeUsage (atomic)
   canCreateWorker / canExecuteWorker / canScheduleWorker / canCreateDeployment / canCreateApiKey
   ▼
Enforcement hooks (backend, independent of any frontend check):
   POST /api/mkt/v1/names                  → anm_deployments count gate
   lib/appStore.ts + listings/prices       → marketplace_selling (paid listings) gate
   POST /api/mkt/v1/keys (production kind) → api_keys count + api_rate_limit
   /api/mkt/v1/workers/*                   → workers count, schedule, execution quota
   /api/mkt/v1/workspaces/*                → team_members / workspaces + RBAC
   ▼
scripts/worker-runner.ts  (systemd timer, oneshot, flock + pg advisory lock, WORKERS_ENABLED gate)
   scheduler pass: due ACTIVE workers → WorkerRun rows (quota consumed atomically)
   executor pass:  claim via FOR UPDATE SKIP LOCKED → lease + heartbeat → chat()/tools → result
   safety: max duration, concurrency caps, failure threshold auto-disable, admin + user kill switches
```

## Identity

Tiers attach to the **wallet-keyed `Account`** (`accountId`) — the identity that owns API
keys, listings, `.anm` domains, agents and Workers. Checkout requires a wallet session
(same `authenticate()` used by all 54 API routes). The PayPal payer email is snapshotted on
the subscription (`payerEmail`) for the admin console and receipts. The separate
`HireCustomer` email identity (managed services on `/hire`) is untouched.

## Money

- Tier prices are **USD integer cents** (`priceUsdCents`: 999 / 2999 / 7999 / 19999) — the
  hire vertical's whole-USD `Int` columns don't fit $x.99 pricing.
- PayPal plan prices are immutable: a price change mints a NEW PayPal plan
  (`scripts/subs-setup.ts --recreate-plans`); existing subscribers keep their old plan/price.
- USD subscription accounting (PayPal) and the ANM ledger are **never mixed**. A tier
  subscription buys platform capability; it is not ANM and not an investment product.

## Plans + limits

Plan rows (`Plan`) are the catalog (like `HireService`): `key` (string — `free | starter |
pro | operator | business`, extensible to `enterprise` without schema change), price,
`paypalPlanId`, `featured`, `limitsJson`.

Effective limits = `PLAN_DEFAULTS[key]` (lib/planConfig.ts) **merged with `Plan.limitsJson`**
overrides — so every limit is adjustable from the DB/admin without code changes. Vocabulary:

| feature key                     | free | starter | pro  | operator | business |
|---------------------------------|------|---------|------|----------|----------|
| `workers`                       | 0    | 1       | 5    | 20       | 75       |
| `scheduled_executions_monthly`  | 0    | 50      | 1000 | 10000    | 50000    |
| `anm_deployments`               | 1    | 3       | 15   | 50       | 250      |
| `team_members` (per workspace)  | 0    | 0       | 3    | 10       | 50       |
| `workspaces`                    | 0    | 0       | 1    | 1        | 25       |
| `api_keys` (production)         | 0    | 0       | 3    | 10       | 50       |
| `api_rate_limit` (req/min)      | 0    | 0       | 300  | 1200     | 3000     |
| `marketplace_selling`           | ✗    | ✗       | ✓    | ✓        | ✓        |
| `private_agents`                | ✗    | ✓       | ✓    | ✓        | ✓        |
| `external_triggers`             | ✗    | ✗       | ✗    | ✓        | ✓        |
| `white_label`                   | ✗    | ✗       | ✗    | ✓        | ✓        |
| `custom_branding`               | ✗    | ✗       | ✗    | ✓        | ✓        |
| `reseller_features`             | ✗    | ✗       | ✗    | ✗        | ✓        |
| `advanced_analytics`            | ✗    | ✗       | ✓    | ✓        | ✓        |
| `execution_priority`            | 0    | 1       | 2    | 3        | 4        |
| `worker_min_interval_minutes`   | —    | 60      | 15   | 5        | 5        |
| `worker_max_concurrency`        | 0    | 1       | 2    | 5        | 10       |

Existing accounts implicitly have `free` (no subscription row needed). Existing free
functionality — chat, media, agent listings, one public `.anm` deployment, basic
`anm_mkt_` API keys — is untouched.

## Subscription states

`PENDING` (draft, pre-approval) → `ACTIVE` ⇄ `PAST_DUE` (payment failed; paid limits kept,
warned, new paid-resource creation blocked) → `GRACE_PERIOD` (dunning window
`SUBS_GRACE_DAYS`, default 7) → `SUSPENDED` (free limits; Workers paused; config preserved;
reactivation possible) → `CANCELED` (terminal for that row; account drops to free at period
end). Nothing is ever auto-deleted because billing stopped.

Downgrades/lapses never delete resources: over-limit Workers become `PLAN_LIMIT`
(config preserved, cannot execute), over-limit `.anm` domains become `SUSPENDED` (oldest
registrations stay active — predictable + documented; the user can swap actives in the UI),
over-limit production keys become `SUSPENDED`. The enforcement pass runs inside the worker
runner tick.

## Usage counters

`UsageCounter` rows keyed `(accountId, feature, period)` where `period = 'YYYY-MM'` (UTC
calendar month; quotas reset on the 1st, 00:00 UTC). `consumeUsage` is an atomic
conditional increment (`used + n <= limit`) — race-safe under concurrent executions.
Every Worker execution (scheduled, manual, external) consumes 1 from
`worker_executions`.

## Animica Workers

A normal Animica agent responds when a user interacts with it. **A Worker performs
authorized work automatically**: scheduled/recurring prompts against the Animica AI,
optional tools (`webhook` = POST the result to the owner's HTTPS endpoint, SSRF-guarded;
`anm_publish` = update the HTML of a `.anm` name the owner already controls), execution
history, health, and hard safety rails. Workers never perform unauthorized actions on
third-party services.

Safety (infrastructure limits — separate from and in addition to plan limits; apply to
every tier incl. Business):

- per-run `maxRunSeconds` (user-settable, capped by `WORKER_MAX_RUN_SECONDS`, default 300)
- per-account concurrency (`worker_max_concurrency`) + global `WORKERS_GLOBAL_CONCURRENCY` (default 4)
- monthly execution quota (plan) + per-tick batch cap (`WORKERS_MAX_PER_TICK`, default 25)
- min schedule interval per plan; recursion/runaway protection (a Worker cannot create or
  trigger Workers; a run is one bounded LLM task, attempts capped)
- `failureThreshold` consecutive failures → status `DISABLED` (auto), user re-enables
- user kill switch (Emergency Stop → DISABLED + pending runs CANCELLED)
- admin kill switches: `WorkerEngineState.paused` singleton (checked every tick) and the
  `WORKERS_ENABLED` env gate (disarmed by default, armed in `.env.production`)

Execution engine: `scripts/worker-runner.ts` — a oneshot tsx script fired by
`animica-workers.timer` (1 min), `flock` + pg advisory lock, cloned from the store-worker
pattern. Runs claim with `FOR UPDATE SKIP LOCKED` ordered by plan `execution_priority`
then age; leases + conditional status flips prevent double execution (MediaJob pattern).

## PayPal integration

- Env: `PAYPAL_ENV`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` (shared with hire),
  `SUBS_PAYPAL_WEBHOOK_ID` (NEW — separate webhook; `$`-free value), optional
  `SUBS_GRACE_DAYS`. Sandbox = `PAYPAL_ENV=sandbox` + sandbox creds.
- The browser NEVER supplies plan ids or prices: `/billing/subscribe` resolves the internal
  plan key server-side to the approved `paypalPlanId`.
- `/billing/confirm` re-fetches the subscription from PayPal and requires: plan_id match,
  `custom_id` == our subscription row id, `assertSubscriptionMoneyCents` (rejects PayPal's
  inline plan-override attack), status ACTIVE/APPROVED. `paypalSubscriptionId` is `@unique`
  (P2002 ⇒ one PayPal sub can never pay for two rows).
- Webhook `/api/mkt/v1/billing/paypal/webhook`: `verifyWebhookSignatureFor(SUBS_PAYPAL_WEBHOOK_ID)`
  fail-closed (503 when unset so PayPal retries); **`BillingEvent` insert keyed by the PayPal
  event id is the replay guard** — a duplicate delivery short-circuits to 200 before any
  state change. Handles ACTIVATED, PAYMENT.SALE.COMPLETED (heals PAST_DUE→ACTIVE, advances
  `currentPeriodEnd`), PAYMENT.FAILED (→PAST_DUE, then GRACE_PERIOD), SUSPENDED, CANCELLED,
  EXPIRED. A `CANCELED` row is terminal (replayed ACTIVATED cannot resurrect it).
- Upgrades: existing ACTIVE PayPal sub → `actions.subscription.revise(subId, {plan_id})` in
  the Buttons flow, confirmed server-side the same way (money re-asserted against the NEW
  plan). No active sub → fresh subscribe. Downgrade to a paid tier = same revise flow;
  downgrade to free = cancel (access until `currentPeriodEnd`).

## API routes (all `runtime='nodejs'`, `dynamic='force-dynamic'`)

```
GET  /api/mkt/v1/billing/plans            public catalog (no PayPal plan ids leaked beyond checkoutReady)
GET  /api/mkt/v1/billing/summary          auth: plan, state, usage meters, subscription info
POST /api/mkt/v1/billing/subscribe        auth: {planKey} → draft row + paypal client config
POST /api/mkt/v1/billing/confirm          auth: {id, subscriptionId} → server-verified activation
POST /api/mkt/v1/billing/change           auth: {planKey} → revise/subscribe/cancel path decision
POST /api/mkt/v1/billing/cancel           auth: cancel at PayPal + mark row
POST /api/mkt/v1/billing/keep             auth: {kind, keepIds[]} → choose actives when over-limit
POST /api/mkt/v1/billing/track            public-ish analytics beacon (rate-limited, no PII)
POST /api/mkt/v1/billing/paypal/webhook   PayPal only (signature-verified)
GET/POST/PATCH/DELETE /api/mkt/v1/workers[...]         Worker CRUD + actions (run/pause/stop/logs/trigger)
GET/POST ... /api/mkt/v1/workspaces[...]               teams + RBAC (OWNER/ADMIN/MEMBER)
GET  /api/mkt/v1/billing/admin/*          requireAdmin: subscriber list, analytics, kill switch
```

Every limit violation returns the standard envelope with `code: 'plan_limit'` plus
`{feature, limit, used, plan, requiredPlan}` in the message payload — frontends render the
contextual upgrade CTA from it, and the desktop app won't confuse it with
`insufficient_funds`.

## Security posture

Server-side authorization everywhere (`authenticate()` + DB-backed plan checks — sessions
are stateless/irrevocable so entitlements are NEVER cached in cookies). Webhook signature
verification fail-closed. Event-id replay protection (`BillingEvent`). One-PayPal-sub-one-row
uniqueness. No PayPal secrets to the browser (only `PAYPAL_CLIENT_ID`). No card data stored
anywhere (PayPal holds payment credentials). Workspace access checked by membership row —
IDs in URLs grant nothing. Admin surface behind `requireAdmin` (x-admin-token or ADMIN role).
All new env values `$`-free (systemd + dotenv-expand double-mangle).

## Files

- `lib/planConfig.ts` — pure plan/limits/state logic (unit-tested, no DB)
- `lib/plan.ts` — DB-backed entitlement + usage API (the ONLY place plan checks live)
- `lib/workers.ts` — Worker queue/scheduling/safety logic
- `lib/paypal.ts` — extended: `createMonthlyPlanCents`, `verifyWebhookSignatureFor`, `reviseSubscription`
- `app/api/mkt/v1/billing/**`, `app/api/mkt/v1/workers/**`, `app/api/mkt/v1/workspaces/**`
- `app/pricing/`, `app/settings/billing/`, `app/workers/`, `app/admin/billing/`
- `scripts/subs-setup.ts` (idempotent PayPal plan/webhook bootstrap), `scripts/worker-runner.ts`
- `deploy/systemd/animica-workers.{service,timer}`
- `prisma/subs-migration.sql` (additive, hand-applied via `prisma db execute`)
- `test/*.test.ts` (node:test via tsx — activates the existing `npm test` script)

---

# Operational runbook

## Environment variables

In `.env.production` (ALL values must be `$`-free — systemd + dotenv-expand double-mangle):

```
SUBS_PAYPAL_WEBHOOK_ID=<printed by subs-setup --webhook>   # empty ⇒ webhook 503s (fail closed)
SUBS_GRACE_DAYS=7
WORKERS_ENABLED=1            # arm the runner (unit default is 0 = observe-only)
WORKER_MAX_RUN_SECONDS=300
WORKERS_GLOBAL_CONCURRENCY=4
WORKERS_MAX_PER_TICK=25
WORKER_MAX_ATTEMPTS=2
```

`PAYPAL_ENV` / `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` are shared with the hire vertical.

## Migration (BEFORE deploying code)

```
cd /root/animica/apps/animica-marketplace
set -a; . ./.env.production; set +a
npx prisma db execute --file prisma/subs-migration.sql --schema prisma/schema.prisma
```

Additive-only (audited 0 DROPs). The two `ALTER TYPE ... ADD VALUE` statements are safe
here because nothing in the file uses the new values.

## Deploy (never build in place)

```
cd /root/animica/apps/animica-marketplace
cp -a .next .next.bak && systemctl stop animica-marketplace
npm run build && rm -rf .next.bak || { rm -rf .next && mv .next.bak .next; }
systemctl start animica-marketplace
curl -s http://127.0.0.1:4950/api/mkt/v1/health
curl -s http://127.0.0.1:4950/api/mkt/v1/billing/plans | head -c 300
```

nginx: the `/pricing`, `/settings`, `/workers` `^~` blocks from
`deploy/animica.dev-marketplace.nginx.conf` must exist in
`/etc/nginx/sites-available/animica.dev.conf`; then `nginx -t && systemctl reload nginx`.

Workers runner:

```
cp deploy/systemd/animica-workers.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now animica-workers.timer
# arm via WORKERS_ENABLED=1 in .env.production (unit default is 0 = observe-only)
```

## PayPal setup (live)

```
set -a; . ./.env.production; set +a
npx tsx scripts/subs-setup.ts \
  --webhook https://animica.dev/api/mkt/v1/billing/paypal/webhook
# paste the printed SUBS_PAYPAL_WEBHOOK_ID into .env.production, then restart the service
```

Idempotent: re-running verifies stored plan ids via getPlan and re-mints only broken ones.

## PayPal Sandbox test procedure

1. Create a sandbox REST app at developer.paypal.com; note client id/secret. Create a
   sandbox personal (buyer) account.
2. In a staging env file: `PAYPAL_ENV=sandbox`, sandbox `PAYPAL_CLIENT_ID`/`PAYPAL_CLIENT_SECRET`,
   empty `SUBS_PAYPAL_WEBHOOK_ID`.
3. `npx tsx scripts/subs-setup.ts --webhook https://<staging-host>/api/mkt/v1/billing/paypal/webhook`
   (the webhook host must be publicly reachable for sandbox deliveries). Set the printed id.
4. On `/pricing`: connect a wallet, subscribe to Starter with the sandbox buyer. Verify:
   draft PENDING row → confirm → ACTIVE; `/settings/billing` shows the plan; a `BillingEvent`
   row per delivery; duplicate webhook deliveries (Developer Dashboard → resend) are acked
   as `duplicate:true` with no state change.
5. Upgrade Starter→Pro on `/pricing` (revise flow) — verify plan/money re-assertion and the
   `upgrade` analytics event. Downgrade Pro→Starter — verify over-limit Workers become
   PLAN_LIMIT on the next runner tick and `/settings/billing` offers the picker.
6. Sandbox → Subscriptions: suspend/cancel the subscription; verify SUSPENDED/CANCELED
   propagate via webhook and Workers stop executing.
7. Payment failure: use a sandbox card that declines (or suspend funding); verify
   PAST_DUE → GRACE_PERIOD, the operator email, and that a later successful charge heals
   to ACTIVE.

## Changing prices / limits / plans

- **Limits**: edit `Plan.limitsJson` (JSON object of overrides, e.g. `{"workers": 10}`) —
  live immediately, no deploy. Code defaults live in `lib/planConfig.ts PLAN_DEFAULTS`.
- **Prices**: PayPal plans are immutable. Change the price in `PLAN_CATALOG`
  (lib/planConfig.ts), run `subs-setup.ts --recreate-plans` — new subscribers get the new
  price; existing subscribers keep the old plan/price (their snapshots are per-row).
- **New tier (e.g. enterprise)**: add to `PLAN_KEYS` + `PLAN_DEFAULTS` + `PLAN_CATALOG`,
  rerun subs-setup. Plan keys are strings end-to-end; no schema migration needed.

## Investigating billing problems

- `/admin/billing` — subscriber list (filter by email / plan / status / PayPal id), per-row
  `lastError`, `lastWebhookAt/Type`, BillingEvent history, admin suspend/reactivate/cancel.
- Webhook deliveries: PayPal Developer Dashboard → Webhooks events → resend is always safe
  (event-id dedup). A 503 means `SUBS_PAYPAL_WEBHOOK_ID` is unset; a 400 means signature
  verification failed.
- Worker engine: `/admin/billing` → Workers engine tab (pause-all kill switch, queue depth);
  `journalctl -u animica-workers.service -n 50` for runner ticks.

## Rollback

Code: restore `.next.bak` (stop service, swap back, start) — the schema is additive, old
code ignores the new tables entirely. Workers: `systemctl disable --now animica-workers.timer`
or set `WORKERS_ENABLED=0` (next tick exits immediately) or flip the admin engine pause.
Billing intake: unset `SUBS_PAYPAL_WEBHOOK_ID` (webhook 503s; PayPal queues + retries) —
subscriptions keep billing at PayPal and state heals when re-enabled. Never DROP the new
tables while any code referencing them is deployed.
