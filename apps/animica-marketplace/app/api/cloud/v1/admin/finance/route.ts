import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { financeOverview, isRangeKey } from '@/lib/cloud/finance';
import { activePolicy } from '@/lib/cloud/pricing';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/admin/finance?range=today|24h|7d|30d|mtd|90d|all
//
// The /admin/profitability data source. Everything is computed by lib/cloud/finance.ts from
// authoritative rows (CloudExecution, CloudAppPurchase, BillingPayment, PlanSubscription) —
// never from a cached counter. Also carries the operator context the dashboard shows
// alongside the numbers: unresolved FinanceAlerts, the latest ReconciliationReport per scope,
// and the active pricing policy version.
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const rangeParam = url.searchParams.get('range') ?? '30d';
    if (!isRangeKey(rangeParam)) throw new ApiError(400, 'bad_request', 'invalid range');

    const [overview, policy, alerts, alertCount, reconRows] = await Promise.all([
      financeOverview(rangeParam),
      activePolicy(),
      prisma.financeAlert.findMany({
        where: { resolvedAt: null },
        orderBy: [{ severity: 'asc' }, { createdAt: 'desc' }],
        take: 20,
      }),
      prisma.financeAlert.count({ where: { resolvedAt: null } }),
      // Latest report per scope (small table: fetch recent rows and pick per scope).
      prisma.reconciliationReport.findMany({ orderBy: { createdAt: 'desc' }, take: 40 }),
    ]);

    const latestByScope = new Map<string, (typeof reconRows)[number]>();
    for (const r of reconRows) if (!latestByScope.has(r.scope)) latestByScope.set(r.scope, r);

    return ok({
      ...overview,
      policy: { id: policy.id, version: policy.version, platformFeeBps: policy.platformFeeBps, targetMarginBps: policy.targetMarginBps },
      alerts: { openCount: alertCount, rows: alerts },
      reconciliation: [...latestByScope.values()].map((r) => ({
        scope: r.scope,
        day: r.day,
        ok: r.ok,
        expected: r.expected,
        observed: r.observed,
        deltaAbs: r.deltaAbs,
        createdAt: r.createdAt,
      })),
    });
  } catch (e) {
    return err(e);
  }
}
