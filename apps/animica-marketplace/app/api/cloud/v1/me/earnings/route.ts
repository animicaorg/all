import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { nanmToAnm } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/me/earnings?days=30
//
// REAL developer earnings, straight from CloudExecution rows:
//   * paid    = SUM(developerNanm) over BILLED executions — settled through the ledger and
//               already credited to the account's withdrawable balance (SALE_CREDIT).
//   * pending = executions admitted but not yet settled (billed=false, still in flight);
//               reported with their quoted reservations, never counted as income.
//   * byFunction / byDay = the same sums grouped, for the earnings dashboard.
// Nothing here is estimated or cached — every number is an aggregate over actual rows.

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const daysRaw = Number(req.nextUrl.searchParams.get('days') ?? 30);
    const days = Number.isFinite(daysRaw) ? Math.min(Math.max(Math.trunc(daysRaw), 1), 90) : 30;
    const since = new Date(Date.now() - days * 86_400_000);
    const me = ctx.accountId;

    const [paidAgg, windowAgg, pendingAgg, byFunction, byDay] = await Promise.all([
      // All-time settled earnings.
      prisma.cloudExecution.aggregate({
        where: { developerAccountId: me, billed: true },
        _sum: { developerNanm: true, priceNanm: true },
        _count: { _all: true },
      }),
      // Settled earnings inside the requested window.
      prisma.cloudExecution.aggregate({
        where: { developerAccountId: me, billed: true, createdAt: { gte: since } },
        _sum: { developerNanm: true, priceNanm: true },
        _count: { _all: true },
      }),
      // In-flight (admitted, not yet settled). quotedNanm is a reservation, not income.
      prisma.cloudExecution.aggregate({
        where: { developerAccountId: me, billed: false, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
        _sum: { quotedNanm: true },
        _count: { _all: true },
      }),
      prisma.cloudExecution.groupBy({
        by: ['functionId'],
        where: { developerAccountId: me, billed: true },
        _sum: { developerNanm: true, priceNanm: true },
        _count: { _all: true },
        orderBy: { _sum: { developerNanm: 'desc' } },
        take: 50,
      }),
      prisma.$queryRaw<Array<{ day: Date; executions: bigint; developer_nanm: bigint; gross_nanm: bigint }>>`
        SELECT date_trunc('day', "createdAt")            AS day,
               COUNT(*)::bigint                          AS executions,
               COALESCE(SUM("developerNanm"), 0)::bigint AS developer_nanm,
               COALESCE(SUM("priceNanm"), 0)::bigint     AS gross_nanm
        FROM "CloudExecution"
        WHERE "developerAccountId" = ${me} AND "billed" = true AND "createdAt" >= ${since}
        GROUP BY 1
        ORDER BY 1 DESC`,
    ]);

    // Resolve names for the grouped function ids.
    const fnIds = byFunction.map((r) => r.functionId);
    const fns = fnIds.length
      ? await prisma.cloudFunction.findMany({
          where: { id: { in: fnIds } },
          select: { id: true, slug: true, name: true, status: true },
        })
      : [];
    const fnById = new Map(fns.map((f) => [f.id, f]));

    const paidNanm = paidAgg._sum.developerNanm ?? 0n;
    const windowNanm = windowAgg._sum.developerNanm ?? 0n;

    return ok({
      windowDays: days,
      paid: {
        executions: paidAgg._count._all,
        developerNanm: paidNanm,
        developerAnm: nanmToAnm(paidNanm),
        grossNanm: paidAgg._sum.priceNanm ?? 0n,
        note: 'settled to your ledger balance via SALE_CREDIT — withdrawable on-chain any time',
      },
      window: {
        executions: windowAgg._count._all,
        developerNanm: windowNanm,
        developerAnm: nanmToAnm(windowNanm),
        grossNanm: windowAgg._sum.priceNanm ?? 0n,
      },
      pending: {
        executions: pendingAgg._count._all,
        quotedNanm: pendingAgg._sum.quotedNanm ?? 0n,
        note: 'in-flight executions not yet settled; quoted reservations, not income',
      },
      byFunction: byFunction.map((r) => ({
        functionId: r.functionId,
        slug: fnById.get(r.functionId)?.slug ?? null,
        name: fnById.get(r.functionId)?.name ?? null,
        status: fnById.get(r.functionId)?.status ?? null,
        executions: r._count._all,
        developerNanm: r._sum.developerNanm ?? 0n,
        grossNanm: r._sum.priceNanm ?? 0n,
      })),
      byDay: byDay.map((r) => ({
        day: r.day,
        executions: r.executions,
        developerNanm: r.developer_nanm,
        grossNanm: r.gross_nanm,
      })),
    });
  } catch (e) {
    return err(e);
  }
}
