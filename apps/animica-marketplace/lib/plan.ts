// Centralized entitlement system for the platform subscription tiers. EVERY tier/limit
// decision goes through here — routes must never compare plan strings themselves. All checks
// are DB-backed (sessions are stateless HMAC cookies and irrevocable, so entitlements are
// never cached client-side).
//
// PYTHON CLOUD ERA: the tiers are now the Cloud commercial plans (free/developer/pro/
// business/enterprise, lib/cloud/config.ts). Workers are FREE on every tier — the worker
// gates below are deliberately permissive (they keep their names and signatures because
// ~15 routes and the runner call them) while the real paid gates (deployments, production
// keys, teams, selling, branding) enforce the new ladder. Cloud-native resources (functions,
// apps, agents, schedules) are gated by lib/cloud/entitlements.ts, not here.

import { prisma } from './db';
import { ApiError } from './api';
import {
  type PlanKey,
  type PlanLimits,
  type PlanFeature,
  type EffectivePlan,
  PLAN_KEYS,
  PLAN_DEFAULTS,
  isPlanKey,
  planRank,
  limitsFor,
  effectivePlan,
  periodKeyFor,
  CURRENT_STATES,
} from './planConfig';

export interface AccountPlan extends EffectivePlan {
  limits: PlanLimits;
  subscription: {
    id: string;
    planKey: string;
    status: string;
    currentPeriodEnd: Date | null;
    graceUntil: Date | null;
    paypalSubscriptionId: string | null;
  } | null;
}

const FREE_PLAN: AccountPlan = {
  effectiveKey: 'free',
  subscribedKey: 'free',
  state: 'FREE',
  billingWarning: false,
  blockNewPaidResources: false,
  limits: PLAN_DEFAULTS.free,
  subscription: null,
};

// Resolve the account's current plan. Considers rows that still grant something: the four
// CURRENT_STATES plus CANCELED-but-period-not-ended. Among candidates, the one granting the
// highest-ranked effective plan wins (tie => most recently updated).
export async function getAccountPlan(accountId: string, now: Date = new Date()): Promise<AccountPlan> {
  const rows = await prisma.planSubscription.findMany({
    where: {
      accountId,
      OR: [
        { status: { in: CURRENT_STATES as any } },
        { status: 'CANCELED', currentPeriodEnd: { gt: now } },
      ],
    },
    orderBy: { updatedAt: 'desc' },
    take: 5,
  });
  if (rows.length === 0) return FREE_PLAN;

  let best: { row: (typeof rows)[number]; eff: EffectivePlan } | null = null;
  for (const row of rows) {
    const eff = effectivePlan(row, now);
    if (!best || planRank(eff.effectiveKey) > planRank(best.eff.effectiveKey)) best = { row, eff };
  }
  if (!best) return FREE_PLAN;

  // limitsJson overrides come from the Plan row of the SUBSCRIBED plan (admin-tunable).
  let overrides: string | null = null;
  if (isPlanKey(best.eff.effectiveKey) && best.eff.effectiveKey !== 'free') {
    const plan = await prisma.plan.findUnique({ where: { key: best.eff.effectiveKey } });
    overrides = plan?.limitsJson ?? null;
  }
  return {
    ...best.eff,
    limits: limitsFor(best.eff.effectiveKey, overrides),
    subscription: {
      id: best.row.id,
      planKey: best.row.planKey,
      status: best.row.status,
      currentPeriodEnd: best.row.currentPeriodEnd,
      graceUntil: best.row.graceUntil,
      paypalSubscriptionId: best.row.paypalSubscriptionId,
    },
  };
}

// Lowest built-in tier whose DEFAULTS satisfy the feature — the upsell hint in plan_limit errors.
export function minPlanFor(feature: PlanFeature, needed = 1): PlanKey {
  for (const key of PLAN_KEYS) {
    const v = PLAN_DEFAULTS[key][feature];
    if (typeof v === 'boolean' ? v : v === -1 || v >= needed) return key;
  }
  return 'enterprise';
}

// Standard limit-violation error. Distinct code so clients (incl. the desktop Internet app)
// never confuse it with insufficient_funds; details drive the contextual upgrade CTA.
export function planLimitError(
  feature: PlanFeature,
  opts: { plan: string; limit: number | boolean; used?: number; needed?: number; message?: string },
): ApiError {
  const requiredPlan = minPlanFor(feature, opts.needed ?? (typeof opts.limit === 'number' ? opts.limit + 1 : 1));
  const e = new ApiError(
    402,
    'plan_limit',
    opts.message ??
      `Your ${opts.plan} plan does not include this (${feature}). Upgrade to ${requiredPlan} at /pricing.`,
  );
  e.details = {
    feature,
    plan: opts.plan,
    limit: opts.limit,
    used: opts.used,
    requiredPlan,
    upgradeUrl: '/pricing',
  };
  // Fire-and-forget funnel signal; never blocks the request.
  trackBillingEvent('plan_limit_hit', { planKey: opts.plan, meta: { feature } }).catch(() => {});
  return e;
}

export function billingHoldError(plan: string): ApiError {
  const e = new ApiError(
    402,
    'billing_past_due',
    'Your subscription payment is past due. Update your payment method to create new resources; existing resources keep running during the grace period.',
  );
  e.details = { plan, manageUrl: '/settings/billing' };
  return e;
}

// ── Generic feature checks ───────────────────────────────────────────────────

export async function hasEntitlement(accountId: string, feature: PlanFeature): Promise<boolean> {
  const p = await getAccountPlan(accountId);
  const v = p.limits[feature];
  return typeof v === 'boolean' ? v : v === -1 || v > 0;
}

export async function getPlanLimit(accountId: string, feature: PlanFeature): Promise<number | boolean> {
  const p = await getAccountPlan(accountId);
  return p.limits[feature];
}

export async function requireEntitlement(accountId: string, feature: PlanFeature): Promise<AccountPlan> {
  const p = await getAccountPlan(accountId);
  const v = p.limits[feature];
  const okay = typeof v === 'boolean' ? v : v === -1 || v > 0;
  if (!okay) throw planLimitError(feature, { plan: p.effectiveKey, limit: v });
  return p;
}

// ── Metered usage (UsageCounter, monthly UTC buckets) ────────────────────────

export const USAGE_WORKER_EXECUTIONS = 'worker_executions';

export async function getUsage(accountId: string, feature: string, now: Date = new Date()): Promise<number> {
  const row = await prisma.usageCounter.findUnique({
    where: { accountId_feature_period: { accountId, feature, period: periodKeyFor(now) } },
  });
  return row?.used ?? 0;
}

export async function remainingUsage(
  accountId: string,
  feature: string,
  limit: number,
  now: Date = new Date(),
): Promise<number> {
  if (limit === -1) return Number.MAX_SAFE_INTEGER;
  const used = await getUsage(accountId, feature, now);
  return Math.max(0, limit - used);
}

// Atomic quota consumption: upsert the bucket, then a CONDITIONAL increment
// (used <= limit - amount). Race-safe under concurrent runner ticks / requests — two
// concurrent consumes of the last slot cannot both succeed. With limit -1 (workers today)
// this degrades to a plain metered increment: usage is still counted for analytics.
export async function consumeUsage(
  accountId: string,
  feature: string,
  amount: number,
  limit: number,
  now: Date = new Date(),
): Promise<{ ok: boolean; used: number; limit: number }> {
  const period = periodKeyFor(now);
  await prisma.usageCounter
    .upsert({
      where: { accountId_feature_period: { accountId, feature, period } },
      create: { accountId, feature, period, used: 0 },
      update: {},
    })
    .catch(() => {}); // concurrent upsert race (P2002) => bucket exists, fine
  const where =
    limit === -1
      ? { accountId, feature, period }
      : { accountId, feature, period, used: { lte: limit - amount } };
  const res = await prisma.usageCounter.updateMany({ where, data: { used: { increment: amount } } });
  const used = await getUsage(accountId, feature, now);
  return { ok: res.count === 1, used, limit };
}

// Give quota back when work that was charged never runs (run CANCELLED because the engine
// was paused, the worker was deleted/shelved, or the run went stale). Charged at enqueue,
// refunded only on cancellation — a FAILED/TIMEOUT run consumed real compute and is not
// refunded. `at` must be the run's creation time so the credit lands in the bucket that was
// charged, not in whatever month the sweep happens to run.
export async function refundUsage(
  accountId: string,
  feature: string,
  amount: number,
  at: Date = new Date(),
): Promise<void> {
  if (amount <= 0) return;
  const period = periodKeyFor(at);
  await prisma.usageCounter
    .updateMany({
      where: { accountId, feature, period, used: { gte: amount } },
      data: { used: { decrement: amount } },
    })
    .catch(() => {}); // a missing bucket means nothing was charged — nothing to refund
}

// ── Worker checks (PERMISSIVE — workers are free on every tier) ──────────────
// The functions keep their old names/signatures because routes, lib/workers.ts and the
// runner call them, but none of them can refuse anymore. Real safety comes from the
// infrastructure caps (safetyCaps(), per-account worker_max_concurrency, engine kill
// switch) — not from a paywall. countWorkerSlots stays a REAL count for the meters.

export async function countWorkerSlots(accountId: string): Promise<number> {
  return prisma.worker.count({ where: { accountId, status: { not: 'PLAN_LIMIT' } } });
}

export async function canCreateWorker(accountId: string): Promise<{ ok: boolean; plan: AccountPlan; used: number }> {
  const plan = await getAccountPlan(accountId);
  const used = await countWorkerSlots(accountId);
  return { ok: true, plan, used };
}

// Workers are free: never throws — neither a slot limit (there is none) nor a billing hold
// (a payment hold only blocks new PAID resources, and a worker isn't one anymore).
export async function requireCanCreateWorker(accountId: string): Promise<AccountPlan> {
  const { plan } = await canCreateWorker(accountId);
  return plan;
}

// Can this worker execute AT ALL right now? Always — execution volume is metered (for the
// analytics dashboards) but no longer a wall.
export async function canExecuteWorker(accountId: string): Promise<{ ok: boolean; reason?: string; plan: AccountPlan }> {
  const plan = await getAccountPlan(accountId);
  return { ok: true, plan };
}

export async function canScheduleWorker(accountId: string): Promise<{ ok: boolean; plan: AccountPlan }> {
  const plan = await getAccountPlan(accountId);
  return { ok: true, plan };
}

// ── Count-based slot checks (live counts, never cached) ──────────────────────

// .anm deployments: an ACTIVE domain occupies a slot (SUSPENDED = shelved by downgrade,
// EXPIRED/GRACE = not serving).
export async function countDeployments(accountId: string): Promise<number> {
  return prisma.anmDomain.count({ where: { ownerId: accountId, status: 'ACTIVE' } });
}

export async function canCreateDeployment(accountId: string): Promise<{ ok: boolean; plan: AccountPlan; used: number }> {
  const plan = await getAccountPlan(accountId);
  const used = await countDeployments(accountId);
  const limit = plan.limits.anm_deployments;
  return { ok: limit === -1 || used < limit, plan, used };
}

export async function requireCanCreateDeployment(accountId: string): Promise<AccountPlan> {
  const { ok, plan, used } = await canCreateDeployment(accountId);
  if (!ok) {
    throw planLimitError('anm_deployments', {
      plan: plan.effectiveKey,
      limit: plan.limits.anm_deployments,
      used,
      needed: used + 1,
      message: `Your ${plan.effectiveKey} plan includes ${plan.limits.anm_deployments} .anm deployment${plan.limits.anm_deployments === 1 ? '' : 's'}. Upgrade for more.`,
    });
  }
  return plan;
}

// Production API keys (kind='production'); classic basic keys are not counted or gated.
export async function countProductionKeys(accountId: string): Promise<number> {
  return prisma.apiKey.count({ where: { accountId, kind: 'production', status: 'ACTIVE' } });
}

export async function canCreateApiKey(accountId: string): Promise<{ ok: boolean; plan: AccountPlan; used: number }> {
  const plan = await getAccountPlan(accountId);
  const used = await countProductionKeys(accountId);
  const limit = plan.limits.api_keys;
  return { ok: limit === -1 || used < limit, plan, used };
}

export async function requireCanCreateApiKey(accountId: string): Promise<AccountPlan> {
  const { ok, plan, used } = await canCreateApiKey(accountId);
  if (plan.blockNewPaidResources) throw billingHoldError(plan.effectiveKey);
  if (!ok) {
    throw planLimitError('api_keys', {
      plan: plan.effectiveKey,
      limit: plan.limits.api_keys,
      used,
      needed: used + 1,
      message:
        plan.limits.api_keys === 0
          ? 'Production API access is available with Animica Developer.'
          : `You’ve reached your ${plan.effectiveKey} production API key limit (${plan.limits.api_keys}).`,
    });
  }
  return plan;
}

// Marketplace selling (paid listings) — Developer+.
export async function requireSellerEntitlement(accountId: string): Promise<AccountPlan> {
  const plan = await getAccountPlan(accountId);
  if (!plan.limits.marketplace_selling) {
    throw planLimitError('marketplace_selling', {
      plan: plan.effectiveKey,
      limit: false,
      message: 'Sell what you build through the Animica Marketplace with Developer. Free listings stay free for everyone.',
    });
  }
  return plan;
}

// ── Billing summary (settings/billing + admin) ───────────────────────────────

export async function billingSummary(accountId: string, now: Date = new Date()) {
  const plan = await getAccountPlan(accountId, now);
  const [workersUsed, deployments, prodKeys, execUsed, workspaces] = await Promise.all([
    countWorkerSlots(accountId),
    countDeployments(accountId),
    countProductionKeys(accountId),
    getUsage(accountId, USAGE_WORKER_EXECUTIONS, now),
    prisma.workspace.count({ where: { ownerId: accountId } }),
  ]);
  // team_members is a PER-WORKSPACE limit (matches the members-route gate and the
  // enforcement sweep), so the meter shows the fullest workspace, not the org-wide sum —
  // a Business account with many client workspaces would otherwise always look over-limit.
  const memberGroups = await prisma.workspaceMember.groupBy({
    by: ['workspaceId'],
    where: { workspace: { ownerId: accountId }, role: { not: 'OWNER' } },
    _count: { _all: true },
  });
  const teamMembers = memberGroups.reduce((m, g) => Math.max(m, g._count._all), 0);
  return {
    plan: {
      key: plan.effectiveKey,
      subscribedKey: plan.subscribedKey,
      state: plan.state,
      billingWarning: plan.billingWarning,
      limits: plan.limits,
    },
    subscription: plan.subscription,
    usage: {
      workers: { used: workersUsed, limit: plan.limits.workers },
      scheduled_executions: { used: execUsed, limit: plan.limits.scheduled_executions_monthly },
      anm_deployments: { used: deployments, limit: plan.limits.anm_deployments },
      api_keys: { used: prodKeys, limit: plan.limits.api_keys },
      team_members: { used: teamMembers, limit: plan.limits.team_members },
      workspaces: { used: workspaces, limit: plan.limits.workspaces },
    },
    marketplace: { selling: plan.limits.marketplace_selling },
    period: periodKeyFor(now),
  };
}

// ── Analytics (PII-light funnel events) ──────────────────────────────────────

export async function trackBillingEvent(
  kind: string,
  opts: { accountId?: string; planKey?: string; fromPlanKey?: string; meta?: Record<string, unknown> } = {},
): Promise<void> {
  try {
    await prisma.billingAnalyticsEvent.create({
      data: {
        kind,
        accountId: opts.accountId,
        planKey: opts.planKey,
        fromPlanKey: opts.fromPlanKey,
        meta: JSON.stringify(opts.meta ?? {}),
      },
    });
  } catch {
    // analytics must never break a request
  }
}
