import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { USAGE_WORKER_EXECUTIONS } from '@/lib/plan';
import { periodKeyFor } from '@/lib/planConfig';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Revenue-recognizing states: ACTIVE + the dunning states still on paid limits (a PAST_DUE
// subscriber is still expected revenue until the grace window lapses to SUSPENDED).
const ENTITLED = ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD'] as const;

// Full funnel vocabulary — kinds with zero events still appear so the dashboard rows are stable.
const FUNNEL_KINDS = [
  'pricing_view', 'checkout_start', 'checkout_success', 'checkout_error', 'checkout_abandoned',
  'upgrade', 'downgrade', 'cancel', 'payment_failed', 'reactivation', 'plan_limit_hit',
] as const;

// GET /api/mkt/v1/billing/admin/analytics -> the /admin/billing Analytics tab. Admin only.
// Read-only aggregates: MRR/ARPU + tier mix from live subscription rows, the last-30d funnel
// from BillingAnalyticsEvent (PII-light: no emails/IPs), Worker adoption, production-key and
// seller counts. All grouped queries — nothing here scans row-by-row.
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const now = new Date();
    const since = new Date(now.getTime() - 30 * 86_400_000);
    const period = periodKeyFor(now);

    const [mrr, byPlan, entitledAccounts, funnel, paths, workersByAccount, runsAgg, productionKeys, paidPrices, revenue30d, refunds30d] =
      await Promise.all([
        prisma.planSubscription.aggregate({
          where: { status: { in: [...ENTITLED] } },
          _sum: { priceUsdCents: true },
          _count: { _all: true },
        }),
        prisma.planSubscription.groupBy({
          by: ['planKey'],
          where: { status: { in: [...ENTITLED] } },
          _count: { _all: true },
        }),
        // Distinct accounts (an account should hold one entitled row, but never assume).
        prisma.planSubscription.groupBy({ by: ['accountId'], where: { status: { in: [...ENTITLED] } } }),
        prisma.billingAnalyticsEvent.groupBy({
          by: ['kind'],
          where: { createdAt: { gte: since } },
          _count: { _all: true },
        }),
        prisma.billingAnalyticsEvent.groupBy({
          by: ['kind', 'fromPlanKey', 'planKey'],
          where: { kind: { in: ['upgrade', 'downgrade'] }, createdAt: { gte: since } },
          _count: { _all: true },
        }),
        prisma.worker.groupBy({ by: ['accountId'], _count: { _all: true } }),
        prisma.usageCounter.aggregate({
          where: { feature: USAGE_WORKER_EXECUTIONS, period },
          _sum: { used: true },
        }),
        prisma.apiKey.count({ where: { kind: 'production', status: 'ACTIVE' } }),
        // Seller approximation: listings carrying an ACTIVE non-FREE price (grouped, so a
        // listing with several paid prices counts once).
        prisma.price.groupBy({ by: ['listingId'], where: { active: true, model: { not: 'FREE' } } }),
        // COLLECTED revenue (BillingPayment = verified PayPal sales), distinct from MRR
        // which is only the sum of live contracted prices. Refunds are flipped to REFUNDED
        // by the webhook, so COMPLETED here is money that actually stayed.
        prisma.billingPayment.aggregate({
          where: { status: 'COMPLETED', occurredAt: { gte: since } },
          _sum: { amountCents: true },
          _count: { _all: true },
        }),
        prisma.billingPayment.aggregate({
          where: { status: 'REFUNDED', occurredAt: { gte: since } },
          _sum: { amountCents: true },
          _count: { _all: true },
        }),
      ]);

    // Fold paid listings up to distinct owners = "sellers".
    const sellerRows = paidPrices.length
      ? await prisma.listing.findMany({
          where: { id: { in: paidPrices.map((p) => p.listingId) } },
          distinct: ['ownerId'],
          select: { ownerId: true },
        })
      : [];

    const mrrCents = mrr._sum.priceUsdCents ?? 0;
    const subscribers = mrr._count._all;
    const kinds: Record<string, number> = Object.fromEntries(FUNNEL_KINDS.map((k) => [k, 0]));
    for (const f of funnel) if (f.kind in kinds) kinds[f.kind] = f._count._all;
    const totalWorkers = workersByAccount.reduce((n, r) => n + r._count._all, 0);
    const subAccounts = entitledAccounts.length;

    return ok({
      mrrCents,
      subscribers,
      revenue30d: { cents: revenue30d._sum.amountCents ?? 0, payments: revenue30d._count._all },
      refunds30d: { cents: refunds30d._sum.amountCents ?? 0, payments: refunds30d._count._all },
      arpuCents: subscribers ? Math.round(mrrCents / subscribers) : 0,
      activeByPlan: Object.fromEntries(byPlan.map((r) => [r.planKey, r._count._all])),
      funnel30d: kinds,
      paths30d: paths
        .map((p) => ({ kind: p.kind, fromPlanKey: p.fromPlanKey, planKey: p.planKey, count: p._count._all }))
        .sort((a, b) => b.count - a.count),
      workerAdoption: {
        accountsWithWorkers: workersByAccount.length,
        totalWorkers,
        workersPerSubscriber: subAccounts ? +(totalWorkers / subAccounts).toFixed(2) : 0,
        runsThisMonth: runsAgg._sum.used ?? 0,
      },
      productionKeys,
      sellers: sellerRows.length,
      conversionRate: kinds.pricing_view > 0 ? +(kinds.checkout_success / kinds.pricing_view).toFixed(4) : 0,
      period,
    });
  } catch (e) {
    return err(e);
  }
}
