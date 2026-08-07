import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { USAGE_WORKER_EXECUTIONS } from '@/lib/plan';
import { periodKeyFor } from '@/lib/planConfig';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const SUB_STATUSES = ['PENDING', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD', 'SUSPENDED', 'CANCELED'];

// GET /api/mkt/v1/billing/admin/subscriptions?q=&plan=&status=&take=&skip=
// The /admin/billing subscriber ledger. Admin only (MKT_ADMIN_TOKEN header or ADMIN account).
// q matches payerEmail (contains, case-insensitive), paypalSubscriptionId (exact) or the
// account address (exact) — the three handles a support request arrives with. Every row
// carries live usage counts, batched over the page's accountIds with grouped queries so the
// page costs a fixed number of queries regardless of `take` (never N+1 per row).
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const sp = req.nextUrl.searchParams;
    const q = (sp.get('q') ?? '').trim();
    const plan = (sp.get('plan') ?? '').trim();
    const status = (sp.get('status') ?? '').trim();
    const take = Math.min(100, Math.max(1, Number(sp.get('take')) || 50));
    const skip = Math.max(0, Math.trunc(Number(sp.get('skip')) || 0));
    if (status && !SUB_STATUSES.includes(status)) {
      throw new ApiError(400, 'bad_request', `status must be one of ${SUB_STATUSES.join(' | ')}`);
    }

    // plan is NOT validated against PLAN_KEYS: Plan rows are extensible (enterprise) and an
    // unknown key simply matches nothing.
    const where: any = {};
    if (plan) where.planKey = plan;
    if (status) where.status = status;
    if (q) {
      where.OR = [
        { payerEmail: { contains: q, mode: 'insensitive' } },
        { paypalSubscriptionId: q },
        { account: { address: q } },
      ];
    }

    const [rows, count] = await Promise.all([
      prisma.planSubscription.findMany({
        where,
        orderBy: { updatedAt: 'desc' }, // latest billing activity first — the triage order
        take,
        skip,
        include: { account: { select: { id: true, address: true, displayName: true } } },
      }),
      prisma.planSubscription.count({ where }),
    ]);
    if (rows.length === 0) return ok({ rows: [], totals: { count }, take, skip });

    const accountIds = [...new Set(rows.map((r) => r.accountId))];
    const period = periodKeyFor();
    const [workers, execs, domains, keys, workspaces] = await Promise.all([
      // Slot-occupying workers (status != PLAN_LIMIT) — same semantics as the user-facing meter.
      prisma.worker.groupBy({
        by: ['accountId'],
        where: { accountId: { in: accountIds }, status: { not: 'PLAN_LIMIT' } },
        _count: { _all: true },
      }),
      prisma.usageCounter.findMany({
        where: { accountId: { in: accountIds }, feature: USAGE_WORKER_EXECUTIONS, period },
        select: { accountId: true, used: true },
      }),
      prisma.anmDomain.groupBy({
        by: ['ownerId'],
        where: { ownerId: { in: accountIds }, status: 'ACTIVE' },
        _count: { _all: true },
      }),
      prisma.apiKey.groupBy({
        by: ['accountId'],
        where: { accountId: { in: accountIds }, kind: 'production', status: 'ACTIVE' },
        _count: { _all: true },
      }),
      prisma.workspace.findMany({
        where: { ownerId: { in: accountIds } },
        select: { id: true, ownerId: true },
      }),
    ]);

    // Team members can't be grouped by workspace OWNER directly — group by workspace, then
    // fold up to the owning account (mirrors billingSummary: every non-OWNER membership row).
    const ownerByWs = new Map(workspaces.map((w) => [w.id, w.ownerId]));
    const members = workspaces.length
      ? await prisma.workspaceMember.groupBy({
          by: ['workspaceId'],
          where: { workspaceId: { in: workspaces.map((w) => w.id) }, role: { not: 'OWNER' } },
          _count: { _all: true },
        })
      : [];
    const teamBy = new Map<string, number>();
    for (const m of members) {
      const owner = ownerByWs.get(m.workspaceId);
      if (owner) teamBy.set(owner, (teamBy.get(owner) ?? 0) + m._count._all);
    }
    const workersBy = new Map(workers.map((r) => [r.accountId, r._count._all]));
    const execBy = new Map(execs.map((r) => [r.accountId, r.used]));
    const domainsBy = new Map(domains.map((r) => [r.ownerId, r._count._all]));
    const keysBy = new Map(keys.map((r) => [r.accountId, r._count._all]));

    return ok({
      rows: rows.map((r) => ({
        id: r.id,
        account: r.account,
        payerEmail: r.payerEmail,
        planKey: r.planKey,
        status: r.status,
        priceUsdCents: r.priceUsdCents,
        currentPeriodEnd: r.currentPeriodEnd,
        graceUntil: r.graceUntil,
        lastPaymentAt: r.lastPaymentAt,
        lastPaymentAmountCents: r.lastPaymentAmountCents,
        failedPayments: r.failedPayments,
        lastWebhookAt: r.lastWebhookAt,
        lastWebhookType: r.lastWebhookType,
        lastError: r.lastError,
        paypalSubscriptionId: r.paypalSubscriptionId,
        createdAt: r.createdAt,
        usage: {
          workers: workersBy.get(r.accountId) ?? 0,
          runsThisPeriod: execBy.get(r.accountId) ?? 0,
          deployments: domainsBy.get(r.accountId) ?? 0,
          productionKeys: keysBy.get(r.accountId) ?? 0,
          teamMembers: teamBy.get(r.accountId) ?? 0,
        },
      })),
      totals: { count },
      take,
      skip,
    });
  } catch (e) {
    return err(e);
  }
}
