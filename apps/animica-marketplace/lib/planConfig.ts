// Pure plan/limit/state logic for the platform subscription tiers — NO database access here.
// lib/plan.ts wraps this with Prisma. Kept pure so test/plan-config.test.ts can cover every
// branch without a DB.
//
// PYTHON CLOUD CATALOG (2026-08): the old Workers-gating tiers (starter $9.99 / operator
// $79.99 ...) are retired. The five commercial tiers now live in lib/cloud/config.ts
// (CLOUD_PLAN_CATALOG + PLAN_ENTITLEMENTS) — the ONE source of truth for keys, prices and
// cloud quotas — and this module DERIVES from it so the legacy entitlement vocabulary
// (PlanLimits) that ~15 existing routes still consume keeps working on the new tiers.
// Workers are FREE on every tier now (AI stays free; the Cloud is what's sold), so the
// worker gates resolve to unlimited and can never again be tightened from the DB.

import {
  CLOUD_PLAN_KEYS,
  CLOUD_PLAN_CATALOG,
  PLAN_ENTITLEMENTS,
  type CloudPlanKey,
} from './cloud/config';

export const PLAN_KEYS = CLOUD_PLAN_KEYS;
export type PlanKey = CloudPlanKey;

export function isPlanKey(v: unknown): v is PlanKey {
  return typeof v === 'string' && (PLAN_KEYS as readonly string[]).includes(v);
}

// Ordered ranking for "requiredPlan" upsell hints + upgrade/downgrade direction. Retired
// legacy keys ('starter', 'operator') rank 0 — a leftover row can never outrank a real tier.
export function planRank(key: string): number {
  const i = (PLAN_KEYS as readonly string[]).indexOf(key);
  return i < 0 ? 0 : i;
}

// The legacy entitlement vocabulary. Numeric limits use -1 for "unlimited". Kept intact —
// routes across the app read these keys by name — but the VALUES now derive from the Python
// Cloud tiers.
export interface PlanLimits {
  workers: number;
  scheduled_executions_monthly: number;
  anm_deployments: number;
  team_members: number; // per workspace
  workspaces: number;
  api_keys: number; // production keys
  api_rate_limit: number; // per-minute, production keys
  marketplace_selling: boolean;
  private_agents: boolean;
  external_triggers: boolean;
  white_label: boolean;
  custom_branding: boolean;
  reseller_features: boolean;
  advanced_analytics: boolean;
  execution_priority: number;
  worker_min_interval_minutes: number;
  worker_max_concurrency: number;
}

export type PlanFeature = keyof PlanLimits;

// Workers/scheduled executions are un-gated everywhere: these two features are pinned to -1
// and limitsFor refuses overrides for them (see UNGATED below) so a stale limitsJson row from
// the old catalog can never re-brick a worker.
const UNGATED: ReadonlySet<PlanFeature> = new Set(['workers', 'scheduled_executions_monthly']);

// Map one Cloud tier's entitlements into the legacy vocabulary. Everything with a Cloud
// counterpart is READ from lib/cloud/config.ts (single source of truth); the handful of
// platform-only knobs (teams, workspaces, production keys, branding) ladder alongside it.
function fromCloud(
  key: CloudPlanKey,
  platform: {
    team_members: number;
    workspaces: number;
    api_keys: number;
    private_agents: boolean;
    white_label: boolean;
    custom_branding: boolean;
    reseller_features: boolean;
  },
): PlanLimits {
  const e = PLAN_ENTITLEMENTS[key];
  return {
    workers: -1, // Workers are free on every tier
    scheduled_executions_monthly: -1, // metered for analytics, never a wall
    anm_deployments: e.max_apps, // .anm sites ladder with the app ladder
    team_members: platform.team_members,
    workspaces: platform.workspaces,
    api_keys: platform.api_keys,
    api_rate_limit: e.api_rate_limit,
    marketplace_selling: e.marketplace_publishing,
    private_agents: platform.private_agents,
    external_triggers: true, // worker feature — free with workers
    white_label: platform.white_label,
    custom_branding: platform.custom_branding,
    reseller_features: platform.reseller_features,
    advanced_analytics: e.premium_analytics,
    execution_priority: e.priority_class,
    worker_min_interval_minutes: e.min_schedule_minutes,
    worker_max_concurrency: e.max_concurrency,
  };
}

// Code defaults; Plan.limitsJson (DB, admin-editable) overrides key-by-key (except UNGATED),
// so every limit is adjustable without a code change. Prices live on the Plan rows, not here.
export const PLAN_DEFAULTS: Record<PlanKey, PlanLimits> = {
  free: fromCloud('free', {
    team_members: 0,
    workspaces: 0,
    api_keys: 0,
    private_agents: false,
    white_label: false,
    custom_branding: false,
    reseller_features: false,
  }),
  // `starter` is the rung formerly named `developer`. It was missing here, so a
  // $4.99 subscriber resolved to the FREE defaults: 0 team members, 0 API keys.
  starter: fromCloud('starter', {
    team_members: 3,
    workspaces: 1,
    api_keys: 3,
    private_agents: true,
    white_label: false,
    custom_branding: false,
    reseller_features: false,
  }),
  developer: fromCloud('developer', {
    team_members: 3,
    workspaces: 1,
    api_keys: 3,
    private_agents: true,
    white_label: false,
    custom_branding: false,
    reseller_features: false,
  }),
  pro: fromCloud('pro', {
    team_members: 10,
    workspaces: 5,
    api_keys: 10,
    private_agents: true,
    white_label: true,
    custom_branding: true,
    reseller_features: false,
  }),
  // `operator` ($39.99) was missing, so it resolved to the FREE defaults while
  // `pro` ($14.99) got 10 team members and 10 API keys. These values come from the
  // features already marketed on the Plan row ("Up to 10 team members",
  // "Higher API limits, multiple keys", "White-label & custom branding") — nothing
  // invented, and every one is >= pro so the ladder cannot invert.
  operator: fromCloud('operator', {
    team_members: 10,
    workspaces: 10,
    api_keys: 20,
    private_agents: true,
    white_label: true,
    custom_branding: true,
    reseller_features: false,
  }),
  business: fromCloud('business', {
    team_members: 50,
    workspaces: 25,
    api_keys: 50,
    private_agents: true,
    white_label: true,
    custom_branding: true,
    reseller_features: true,
  }),
  enterprise: fromCloud('enterprise', {
    team_members: -1,
    workspaces: -1,
    api_keys: -1,
    private_agents: true,
    white_label: true,
    custom_branding: true,
    reseller_features: true,
  }),
};

// Marketing catalog seeded into Plan rows (scripts/subs-setup.ts). Mirrors CLOUD_PLAN_CATALOG
// verbatim — priceUsdCents is the source the PayPal plans are minted from; the browser never
// supplies prices. contactSales tiers (enterprise) never mint a PayPal plan and are never
// checkout-ready: they route through POST /api/cloud/v1/enterprise instead.
export const PLAN_CATALOG: Array<{
  key: PlanKey;
  name: string;
  tagline: string;
  icon: string;
  priceUsdCents: number;
  featured: boolean;
  sortOrder: number;
  contactSales: boolean;
  features: string[];
}> = CLOUD_PLAN_CATALOG.map((p) => ({
  key: p.key,
  name: p.name,
  tagline: p.tagline,
  icon: p.icon,
  priceUsdCents: p.priceUsdCents,
  featured: p.featured,
  sortOrder: p.sortOrder,
  contactSales: p.contactSales,
  features: p.features,
}));

export function isContactSalesPlan(key: string): boolean {
  return PLAN_CATALOG.some((p) => p.key === key && p.contactSales);
}

// Merge DB overrides (Plan.limitsJson) over the code defaults. Unknown keys and wrong-typed
// values are ignored (fail-safe: bad admin JSON can never widen a boolean into garbage), and
// UNGATED worker features ignore overrides entirely — un-gating workers was a product
// decision, not an admin-tunable limit.
export function limitsFor(key: string, overridesJson?: string | null): PlanLimits {
  const base = PLAN_DEFAULTS[isPlanKey(key) ? key : 'free'];
  const out: PlanLimits = { ...base };
  if (overridesJson) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(overridesJson);
    } catch {
      parsed = null;
    }
    if (parsed && typeof parsed === 'object') {
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (!(k in base)) continue;
        const feature = k as PlanFeature;
        if (UNGATED.has(feature)) continue;
        const cur = base[feature];
        if (typeof cur === 'boolean' && typeof v === 'boolean') (out as any)[feature] = v;
        if (typeof cur === 'number' && typeof v === 'number' && Number.isFinite(v)) {
          (out as any)[feature] = Math.trunc(v);
        }
      }
    }
  }
  return out;
}

// ── Subscription state → effective entitlements ──────────────────────────────

export type SubState = 'PENDING' | 'ACTIVE' | 'PAST_DUE' | 'GRACE_PERIOD' | 'SUSPENDED' | 'CANCELED';

// States that keep the PAID plan's limits (with warnings / creation blocks).
const PAID_STATES: ReadonlySet<string> = new Set(['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD']);
// States under which the subscription row still "owns" the account's plan slot at all.
export const CURRENT_STATES: readonly SubState[] = ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED'];

export interface EffectivePlanInput {
  planKey: string;
  status: string;
  currentPeriodEnd?: Date | string | null;
  graceUntil?: Date | string | null;
}

export interface EffectivePlan {
  // The plan whose limits actually apply right now.
  effectiveKey: string;
  // The plan the user is subscribed to (shown in the UI even while suspended).
  subscribedKey: string;
  state: SubState | 'FREE';
  // True while PAST_DUE/GRACE_PERIOD: keep access, warn, block NEW paid-resource creation.
  billingWarning: boolean;
  blockNewPaidResources: boolean;
}

const STALE_ACTIVE_DAYS = 7; // ACTIVE but period ended > 7d ago with no webhook => fail safe to free

function toDate(v: Date | string | null | undefined): Date | null {
  if (!v) return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isFinite(d.getTime()) ? d : null;
}

// Resolve what limits an account actually gets from its (possibly absent) subscription row.
// Pure: `now` injectable for tests. NOTE: a row on a RETIRED legacy key ('starter'/'operator')
// fails isPlanKey and resolves to free — subs-setup deactivates those Plan rows and the
// operator migrates any remaining live subscribers by hand.
export function effectivePlan(sub: EffectivePlanInput | null, now: Date = new Date()): EffectivePlan {
  if (!sub || !isPlanKey(sub.planKey) || sub.planKey === 'free') {
    return { effectiveKey: 'free', subscribedKey: 'free', state: 'FREE', billingWarning: false, blockNewPaidResources: false };
  }
  const status = String(sub.status) as SubState;
  if (status === 'PENDING' || status === 'CANCELED') {
    // CANCELED keeps paid access until the already-paid period ends, then falls to free.
    if (status === 'CANCELED') {
      const end = toDate(sub.currentPeriodEnd);
      if (end && end.getTime() > now.getTime()) {
        return { effectiveKey: sub.planKey, subscribedKey: sub.planKey, state: 'CANCELED', billingWarning: true, blockNewPaidResources: true };
      }
    }
    return { effectiveKey: 'free', subscribedKey: 'free', state: 'FREE', billingWarning: false, blockNewPaidResources: false };
  }
  if (status === 'SUSPENDED') {
    return { effectiveKey: 'free', subscribedKey: sub.planKey, state: 'SUSPENDED', billingWarning: true, blockNewPaidResources: true };
  }
  if (PAID_STATES.has(status)) {
    // Webhooks can go missing; don't let a dead subscription stay ACTIVE forever.
    const end = toDate(sub.currentPeriodEnd);
    if (status === 'ACTIVE' && end && now.getTime() - end.getTime() > STALE_ACTIVE_DAYS * 86_400_000) {
      return { effectiveKey: 'free', subscribedKey: sub.planKey, state: 'SUSPENDED', billingWarning: true, blockNewPaidResources: true };
    }
    const warning = status !== 'ACTIVE';
    return {
      effectiveKey: sub.planKey,
      subscribedKey: sub.planKey,
      state: status,
      billingWarning: warning,
      blockNewPaidResources: warning,
    };
  }
  return { effectiveKey: 'free', subscribedKey: 'free', state: 'FREE', billingWarning: false, blockNewPaidResources: false };
}

// ── Webhook event → subscription patch (pure; unit-tested) ───────────────────

export const SUBS_WEBHOOK_EVENTS = [
  'BILLING.SUBSCRIPTION.ACTIVATED',
  'BILLING.SUBSCRIPTION.UPDATED',
  'BILLING.SUBSCRIPTION.SUSPENDED',
  'BILLING.SUBSCRIPTION.CANCELLED',
  'BILLING.SUBSCRIPTION.EXPIRED',
  'BILLING.SUBSCRIPTION.PAYMENT.FAILED',
  'PAYMENT.SALE.COMPLETED',
  'PAYMENT.SALE.REFUNDED',
  'PAYMENT.SALE.REVERSED',
  'CUSTOMER.DISPUTE.CREATED',
  'CUSTOMER.DISPUTE.UPDATED',
] as const;

export interface SubPatch {
  status?: SubState;
  graceUntil?: Date | null;
  failedPayments?: 'increment' | 'reset';
  canceledAt?: Date;
  needsAdminEmail?: boolean;
  note?: string;
}

// A CANCELED row is terminal — replayed events can never resurrect it (hire's TERMINAL rule).
export function computeSubPatch(
  eventType: string,
  currentStatus: string,
  now: Date = new Date(),
  graceDays = 7,
): SubPatch | null {
  if (currentStatus === 'CANCELED' && eventType !== 'BILLING.SUBSCRIPTION.CANCELLED') return null;
  switch (eventType) {
    case 'BILLING.SUBSCRIPTION.ACTIVATED':
      return { status: 'ACTIVE', graceUntil: null, failedPayments: 'reset' };
    case 'PAYMENT.SALE.COMPLETED':
      // Heals dunning: a successful charge always restores ACTIVE.
      return { status: 'ACTIVE', graceUntil: null, failedPayments: 'reset' };
    case 'BILLING.SUBSCRIPTION.PAYMENT.FAILED': {
      // A failed payment must never IMPROVE a suspended row. SUSPENDED is a clawback or an
      // operator hold (free limits); moving it to PAST_DUE would restore paid limits and hand
      // back a grace window — on the strength of a payment that did not happen. Only a real
      // charge (SALE.COMPLETED) or ACTIVATED heals a suspension.
      if (currentStatus === 'SUSPENDED') {
        return { failedPayments: 'increment', needsAdminEmail: true, note: 'payment failed while suspended' };
      }
      const grace = new Date(now.getTime() + graceDays * 86_400_000);
      // First failure => PAST_DUE with a grace window; already dunning => GRACE_PERIOD.
      const next: SubState = currentStatus === 'PAST_DUE' || currentStatus === 'GRACE_PERIOD' ? 'GRACE_PERIOD' : 'PAST_DUE';
      return { status: next, graceUntil: grace, failedPayments: 'increment', needsAdminEmail: true };
    }
    case 'BILLING.SUBSCRIPTION.SUSPENDED':
      return { status: 'SUSPENDED', needsAdminEmail: false };
    case 'BILLING.SUBSCRIPTION.CANCELLED':
    case 'BILLING.SUBSCRIPTION.EXPIRED':
      return { status: 'CANCELED', canceledAt: now };
    case 'PAYMENT.SALE.REFUNDED':
    case 'PAYMENT.SALE.REVERSED':
    case 'CUSTOMER.DISPUTE.CREATED':
    case 'CUSTOMER.DISPUTE.UPDATED':
      // Clawback: suspend entitlements, flag for the operator to review.
      return { status: 'SUSPENDED', needsAdminEmail: true, note: `clawback: ${eventType}` };
    case 'BILLING.SUBSCRIPTION.UPDATED':
      return {}; // metadata refresh only (plan revision lands via confirm; period end via SALE)
    default:
      return null; // unknown event: ack + ignore
  }
}

// ── Money assertion for cents-priced plans ───────────────────────────────────

export function centsToUsdString(cents: number): string {
  return (Math.round(cents) / 100).toFixed(2);
}

// Cents-exact twin of lib/paypal.ts assertSubscriptionMoney (which compares whole-USD
// Numbers). Guards PayPal's inline plan-override: the browser builds the create call and may
// override stored plan pricing while keeping the plan_id — always re-assert effective money.
export function assertSubscriptionMoneyCents(
  sub: any,
  expectedMonthlyCents: number,
): { ok: true } | { ok: false; reason: string } {
  if (sub?.plan_overridden === true) return { ok: false, reason: 'plan prices were overridden' };
  const plan = sub?.plan;
  if (!plan) return { ok: true }; // no inline snapshot => stored plan pricing applies
  const setup = plan?.payment_preferences?.setup_fee;
  if (setup && Number(setup.value) !== 0) return { ok: false, reason: `unexpected setup fee ${setup.value}` };
  const cycles = Array.isArray(plan?.billing_cycles) ? plan.billing_cycles : [];
  for (const c of cycles) {
    const price = c?.pricing_scheme?.fixed_price;
    if (!price) continue;
    if (String(price.currency_code) !== 'USD') return { ok: false, reason: 'billing currency is not USD' };
    if (String(c?.tenure_type).toUpperCase() === 'REGULAR') {
      const got = Math.round(Number(price.value) * 100);
      if (!Number.isFinite(got) || got !== Math.round(expectedMonthlyCents)) {
        return { ok: false, reason: `monthly price ${price.value} != ${centsToUsdString(expectedMonthlyCents)}` };
      }
    }
  }
  return { ok: true };
}

// ── PAYMENT.SALE.COMPLETED → auditable BillingPayment draft (pure) ───────────

export interface SalePaymentDraft {
  paypalCaptureId: string; // PayPal sale/capture id — the @unique idempotency anchor
  amountCents: number;
  currency: string;
  occurredAt: Date;
}

// Extract the auditable USD-revenue facts from a verified PAYMENT.SALE.COMPLETED resource.
// Pure and total: returns null when the resource can't prove a positive charge (no id, bad
// amount) so the webhook records nothing rather than a garbage row. Deterministic on the sale
// id — a redelivered/duplicated event maps to the SAME paypalCaptureId, so the DB @unique
// constraint (insert with skipDuplicates) makes revenue rows exactly-once.
export function salePaymentFromResource(resource: any): SalePaymentDraft | null {
  const id = typeof resource?.id === 'string' ? resource.id.trim() : '';
  if (!id) return null;
  const total = Number(resource?.amount?.total);
  if (!Number.isFinite(total) || total <= 0) return null;
  const currency = typeof resource?.amount?.currency === 'string' ? resource.amount.currency : '';
  if (!currency) return null;
  const when = toDate(resource?.create_time) ?? toDate(resource?.update_time) ?? new Date();
  return {
    paypalCaptureId: id,
    amountCents: Math.round(total * 100),
    currency,
    occurredAt: when,
  };
}

// ── Usage periods ────────────────────────────────────────────────────────────

// UTC calendar-month bucket: quotas reset on the 1st, 00:00 UTC (predictable, documented).
export function periodKeyFor(d: Date = new Date()): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

// ── Worker scheduling (pure helpers used by routes + runner) ─────────────────

export function clampIntervalMinutes(requested: number, limits: PlanLimits): number {
  const min = Math.max(1, limits.worker_min_interval_minutes || 1);
  const r = Math.trunc(Number.isFinite(requested) ? requested : min);
  return Math.max(min, Math.min(r, 60 * 24 * 31)); // cap: monthly
}

export function nextRunAfter(intervalMinutes: number, from: Date = new Date()): Date {
  return new Date(from.getTime() + Math.max(1, intervalMinutes) * 60_000);
}

// Platform-wide safety caps (infrastructure limits — separate from plan limits; env-tunable,
// apply to every tier including Enterprise). Read at call time so tests can vary env.
export function safetyCaps() {
  const int = (v: string | undefined, dflt: number) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? Math.trunc(n) : dflt;
  };
  return {
    maxRunSeconds: int(process.env.WORKER_MAX_RUN_SECONDS, 300),
    globalConcurrency: int(process.env.WORKERS_GLOBAL_CONCURRENCY, 4),
    maxPerTick: int(process.env.WORKERS_MAX_PER_TICK, 25),
    maxAttempts: int(process.env.WORKER_MAX_ATTEMPTS, 2),
    graceDays: int(process.env.SUBS_GRACE_DAYS, 7),
  };
}
