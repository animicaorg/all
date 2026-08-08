// Animica Python Cloud — the ONE place a plan is turned into a yes/no (§72).
//
// Rule: no route, worker or component may ever branch on a plan key. They ask this module a
// question about a CAPABILITY ("may this account create another function?", "how many
// executions are left?") and get an answer. That keeps pricing changes to config.ts and the
// PricingPolicy/Plan tables instead of a grep across the codebase.
//
// Backend enforcement is authoritative. Anything the frontend does with these numbers is UX.

import { prisma } from '../db';
import { ApiError } from '../api';
import { effectivePlan, periodKeyFor, CURRENT_STATES } from '../planConfig';
import {
  PLAN_ENTITLEMENTS,
  CLOUD_PLAN_KEYS,
  type CloudPlanKey,
  type Entitlements,
  founding as foundingCfg,
  limits as hardLimits,
} from './config';

export type { Entitlements };
export type EntitlementFeature = keyof Entitlements;

// Ordered by PRICE, which is what "higher plan" has to mean when comparing what a
// subscriber is owed. `starter` and `operator` were absent, so every comparison
// involving them fell through to whatever the caller did with `undefined`.
const RANK: Record<CloudPlanKey, number> = {
  free: 0,
  starter: 1,          // $4.99
  developer: 1,        // legacy name for the same rung
  pro: 2,              // $14.99
  operator: 3,         // $39.99
  business: 4,         // $99.99
  enterprise: 5,       // custom
};

export function isCloudPlanKey(v: unknown): v is CloudPlanKey {
  return typeof v === 'string' && (CLOUD_PLAN_KEYS as readonly string[]).includes(v);
}

export function planRank(key: string): number {
  return isCloudPlanKey(key) ? RANK[key] : -1;
}

export interface ResolvedPlan {
  key: CloudPlanKey;
  /** Where the entitlement came from — shown in the UI and the admin console. */
  source: 'none' | 'subscription' | 'founding';
  limits: Entitlements;
  /** True while a payment is late: existing resources keep running, new ones are blocked. */
  blockNewPaidResources: boolean;
  founding: { seq: number | null; feeBps: number; feeUntil: Date | null; featured: boolean } | null;
  subscription: { id: string; status: string; currentPeriodEnd: Date | null } | null;
}

const FREE: ResolvedPlan = {
  key: 'free',
  source: 'none',
  limits: PLAN_ENTITLEMENTS.free,
  blockNewPaidResources: false,
  founding: null,
  subscription: null,
};

/** Merge admin overrides (Plan.limitsJson) over the coded defaults, key by key. */
export function limitsFor(key: CloudPlanKey, overridesJson?: string | null): Entitlements {
  const base = PLAN_ENTITLEMENTS[key] ?? PLAN_ENTITLEMENTS.free;
  if (!overridesJson) return base;
  try {
    const parsed = JSON.parse(overridesJson);
    if (!parsed || typeof parsed !== 'object') return base;
    const out: Entitlements = { ...base };
    for (const [k, v] of Object.entries(parsed)) {
      if (!(k in base)) continue;
      const cur = (base as any)[k];
      if (typeof cur === 'boolean' && typeof v === 'boolean') (out as any)[k] = v;
      else if (typeof cur === 'number' && typeof v === 'number' && Number.isFinite(v)) (out as any)[k] = v;
      else if (typeof cur === 'string' && typeof v === 'string') (out as any)[k] = v;
    }
    return out;
  } catch {
    return base;
  }
}

/**
 * Resolve an account's Python Cloud entitlements.
 *
 * Two grant sources, highest wins:
 *   1. a paid PlanSubscription (the existing, PayPal-verified state machine), and
 *   2. the Founding Developer program's time-boxed Pro grant.
 */
export async function resolvePlan(accountId: string, now: Date = new Date()): Promise<ResolvedPlan> {
  const [rows, fd] = await Promise.all([
    prisma.planSubscription.findMany({
      where: {
        accountId,
        OR: [{ status: { in: CURRENT_STATES as any } }, { status: 'CANCELED', currentPeriodEnd: { gt: now } }],
      },
      orderBy: { updatedAt: 'desc' },
      take: 5,
    }),
    prisma.foundingDeveloper.findUnique({ where: { accountId } }),
  ]);

  let best: ResolvedPlan = { ...FREE };

  for (const row of rows) {
    const eff = effectivePlan(row as any, now);
    if (!isCloudPlanKey(eff.effectiveKey)) continue;
    if (planRank(eff.effectiveKey) <= planRank(best.key) && best.source !== 'none') continue;
    best = {
      key: eff.effectiveKey,
      source: 'subscription',
      limits: PLAN_ENTITLEMENTS[eff.effectiveKey],
      blockNewPaidResources: eff.blockNewPaidResources,
      founding: null,
      subscription: { id: row.id, status: row.status, currentPeriodEnd: row.currentPeriodEnd },
    };
  }

  // Founding Developer: a real, time-boxed Pro grant. It never downgrades a higher paid plan.
  const foundingActive = fd && fd.status === 'ACCEPTED' && !fd.revokedAt;
  if (foundingActive && fd.proUntil && fd.proUntil > now && planRank('pro') > planRank(best.key)) {
    best = {
      key: 'pro',
      source: 'founding',
      limits: PLAN_ENTITLEMENTS.pro,
      blockNewPaidResources: false,
      founding: null,
      subscription: null,
    };
  }

  if (foundingActive) {
    const feeStillActive = !fd.feeUntil || fd.feeUntil > now;
    best.founding = {
      seq: fd.seq,
      feeBps: feeStillActive ? fd.feeBps : -1, // -1 => expired, use the standard rate
      feeUntil: fd.feeUntil,
      featured: fd.featured,
    };
  }

  // Apply admin overrides from the Plan row, if one exists for this key.
  if (best.key !== 'free') {
    const plan = await prisma.plan.findUnique({ where: { key: best.key } }).catch(() => null);
    if (plan?.limitsJson) best.limits = limitsFor(best.key, plan.limitsJson);
  }

  return best;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** Lowest plan whose defaults satisfy a feature — drives the upgrade CTA. */
export function minPlanFor(feature: EntitlementFeature, needed = 1): CloudPlanKey {
  for (const key of CLOUD_PLAN_KEYS) {
    const v = PLAN_ENTITLEMENTS[key][feature];
    if (typeof v === 'boolean' ? v : typeof v === 'number' ? v === -1 || v >= needed : true) return key;
  }
  return 'enterprise';
}

export function entitlementError(
  feature: EntitlementFeature,
  opts: { plan: string; limit: number | boolean | string; used?: number; needed?: number; message?: string },
): ApiError {
  const requiredPlan = minPlanFor(feature, opts.needed ?? (typeof opts.limit === 'number' ? opts.limit + 1 : 1));
  const e = new ApiError(
    402,
    'plan_limit',
    opts.message ?? `Your ${opts.plan} plan does not include this (${String(feature)}). Upgrade at /cloud/pricing.`,
  );
  e.details = { feature, plan: opts.plan, limit: opts.limit, used: opts.used, requiredPlan, upgradeUrl: '/cloud/pricing' };
  return e;
}

export function billingHoldError(plan: string): ApiError {
  const e = new ApiError(
    402,
    'billing_past_due',
    'Your subscription payment is past due. Existing deployments keep running during the grace period, but new paid resources are blocked until payment succeeds.',
  );
  e.details = { plan, manageUrl: '/settings/billing' };
  return e;
}

// ---------------------------------------------------------------------------
// Boolean + numeric checks
// ---------------------------------------------------------------------------

function satisfied(v: number | boolean | string, needed: number): boolean {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') return true;
  return v === -1 || v >= needed;
}

export async function requireEntitlement(
  accountId: string,
  feature: EntitlementFeature,
  needed = 1,
  plan?: ResolvedPlan,
): Promise<ResolvedPlan> {
  const p = plan ?? (await resolvePlan(accountId));
  const v = p.limits[feature];
  if (!satisfied(v, needed)) throw entitlementError(feature, { plan: p.key, limit: v, needed });
  return p;
}

/** Count-based slot check against a live COUNT — never a cached counter (§92). */
export async function requireSlot(
  accountId: string,
  feature: EntitlementFeature,
  currentCount: number,
  plan?: ResolvedPlan,
): Promise<ResolvedPlan> {
  const p = plan ?? (await resolvePlan(accountId));
  const limit = p.limits[feature];
  if (typeof limit !== 'number') return p;
  if (limit === -1) return p;
  if (currentCount >= limit) {
    throw entitlementError(feature, {
      plan: p.key,
      limit,
      used: currentCount,
      needed: currentCount + 1,
      message: `You have ${currentCount} of ${limit} allowed on the ${p.key} plan. Upgrade at /cloud/pricing.`,
    });
  }
  if (p.blockNewPaidResources) throw billingHoldError(p.key);
  return p;
}

// ---------------------------------------------------------------------------
// Metered quotas (monthly UTC buckets in UsageCounter) + overages (§73)
// ---------------------------------------------------------------------------

export const USAGE = {
  executions: 'cloud_executions',
  computeUnits: 'cloud_compute_units', // 1 unit = 1 CPU-second
  aiUnits: 'cloud_ai_units', // 1 unit = 1000 AI tokens
  deploys: 'cloud_deploys',
} as const;

export const USAGE_TO_FEATURE: Record<string, EntitlementFeature> = {
  [USAGE.executions]: 'monthly_executions',
  [USAGE.computeUnits]: 'monthly_compute_units',
  [USAGE.aiUnits]: 'monthly_ai_units',
};

export async function getUsage(accountId: string, feature: string, now = new Date()): Promise<number> {
  const row = await prisma.usageCounter.findUnique({
    where: { accountId_feature_period: { accountId, feature, period: periodKeyFor(now) } },
  });
  return row?.used ?? 0;
}

export interface QuotaResult {
  ok: boolean;
  used: number;
  limit: number;
  overage: number;
  overageAllowed: boolean;
}

/**
 * Atomically consume metered quota.
 *
 * Within the included allowance this is a conditional increment (race-safe: two concurrent
 * requests for the last slot cannot both win). Past the allowance the behavior depends on the
 * plan: `overage_allowed` accrues a UsageCharge and lets the work through (§73); otherwise the
 * caller is refused. Nothing is ever silently unlimited (§96).
 */
export async function consumeQuota(
  accountId: string,
  usageKey: string,
  amount: number,
  plan?: ResolvedPlan,
  now = new Date(),
): Promise<QuotaResult> {
  if (amount <= 0) return { ok: true, used: 0, limit: 0, overage: 0, overageAllowed: false };
  const p = plan ?? (await resolvePlan(accountId));
  const feature = USAGE_TO_FEATURE[usageKey];
  const rawLimit = feature ? p.limits[feature] : -1;
  const limit = typeof rawLimit === 'number' ? rawLimit : -1;
  const period = periodKeyFor(now);

  await prisma.usageCounter
    .upsert({
      where: { accountId_feature_period: { accountId, feature: usageKey, period } },
      create: { accountId, feature: usageKey, period, used: 0 },
      update: {},
    })
    .catch(() => {});

  if (limit === -1) {
    await prisma.usageCounter.updateMany({
      where: { accountId, feature: usageKey, period },
      data: { used: { increment: amount } },
    });
    const used = await getUsage(accountId, usageKey, now);
    return { ok: true, used, limit, overage: 0, overageAllowed: true };
  }

  const within = await prisma.usageCounter.updateMany({
    where: { accountId, feature: usageKey, period, used: { lte: limit - amount } },
    data: { used: { increment: amount } },
  });
  if (within.count === 1) {
    const used = await getUsage(accountId, usageKey, now);
    return { ok: true, used, limit, overage: 0, overageAllowed: p.limits.overage_allowed };
  }

  if (!p.limits.overage_allowed) {
    const used = await getUsage(accountId, usageKey, now);
    return { ok: false, used, limit, overage: 0, overageAllowed: false };
  }

  // Overage: increment unconditionally and accrue the charge.
  await prisma.usageCounter.updateMany({
    where: { accountId, feature: usageKey, period },
    data: { used: { increment: amount } },
  });
  const used = await getUsage(accountId, usageKey, now);
  const overage = Math.max(0, used - limit);
  await accrueOverage(accountId, usageKey, amount, period).catch(() => {});
  return { ok: true, used, limit, overage, overageAllowed: true };
}

/** Give quota back when charged work provably never ran. */
export async function refundQuota(accountId: string, usageKey: string, amount: number, at = new Date()) {
  if (amount <= 0) return;
  await prisma.usageCounter
    .updateMany({
      where: { accountId, feature: usageKey, period: periodKeyFor(at), used: { gte: amount } },
      data: { used: { decrement: amount } },
    })
    .catch(() => {});
}

// Overage unit prices, in USD cents per unit. Config, not scattered constants.
const OVERAGE_CENTS: Record<string, number> = {
  [USAGE.executions]: Number(process.env.CLOUD_OVERAGE_EXEC_CENTS_PER_1K ?? '20'), // per 1,000
  [USAGE.computeUnits]: Number(process.env.CLOUD_OVERAGE_CPU_CENTS_PER_1K ?? '150'), // per 1,000 CPU-s
  [USAGE.aiUnits]: Number(process.env.CLOUD_OVERAGE_AI_CENTS_PER_1K ?? '300'), // per 1,000 units
};

async function accrueOverage(accountId: string, usageKey: string, units: number, period: string) {
  const per1k = OVERAGE_CENTS[usageKey] ?? 0;
  if (per1k <= 0) return;
  const existing = await prisma.usageCharge.findUnique({
    where: { accountId_period_feature: { accountId, period, feature: usageKey } },
  });
  const nextUnits = (existing?.units ?? 0n) + BigInt(units);
  const amountCents = Number((nextUnits * BigInt(per1k)) / 1000n);
  await prisma.usageCharge.upsert({
    where: { accountId_period_feature: { accountId, period, feature: usageKey } },
    create: {
      accountId,
      period,
      feature: usageKey,
      units: BigInt(units),
      unitPriceCents: per1k,
      amountCents,
      asset: 'USD',
      status: 'ACCRUING',
    },
    update: { units: nextUnits, amountCents, unitPriceCents: per1k },
  });
}

// ---------------------------------------------------------------------------
// Derived operational values
// ---------------------------------------------------------------------------

/** Per-account execution concurrency: the plan's value, hard-capped by the global safety ceiling. */
export function concurrencyFor(plan: ResolvedPlan): number {
  const v = plan.limits.max_concurrency;
  const n = typeof v === 'number' && v > 0 ? v : 1;
  return Math.min(n, hardLimits.globalConcurrency);
}

/** Queue priority. Higher runs first (§25). */
export function priorityFor(plan: ResolvedPlan): number {
  const v = plan.limits.priority_class;
  return typeof v === 'number' ? v : 0;
}

export function scheduleFloorMinutes(plan: ResolvedPlan): number {
  const v = plan.limits.min_schedule_minutes;
  const n = typeof v === 'number' && v > 0 ? v : hardLimits.minScheduleMinutes;
  return Math.max(n, hardLimits.minScheduleMinutes);
}

export function logRetentionDays(plan: ResolvedPlan): number {
  const v = plan.limits.log_retention_days;
  return typeof v === 'number' && v > 0 ? v : 3;
}

/**
 * The marketplace fee rate that applies to THIS developer right now.
 *
 * Founding Developers pay the reduced rate until feeUntil; everyone else pays the policy rate.
 * The caller snapshots the returned value onto the row it writes, so a later change to either
 * the policy or the founding window never rewrites historical accounting (§88).
 */
export async function feeBpsForDeveloper(accountId: string, policyFeeBps: number, now = new Date()): Promise<number> {
  const fd = await prisma.foundingDeveloper.findUnique({ where: { accountId } }).catch(() => null);
  if (!fd || fd.status !== 'ACCEPTED' || fd.revokedAt) return policyFeeBps;
  if (fd.feeUntil && fd.feeUntil <= now) return policyFeeBps;
  return Math.min(fd.feeBps, policyFeeBps);
}

export { foundingCfg as foundingConfig };
