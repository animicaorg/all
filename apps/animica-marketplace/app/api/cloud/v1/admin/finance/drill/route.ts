import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { isRangeKey, rangeFor } from '@/lib/cloud/finance';
import { pageParams } from '../../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/admin/finance/drill?kind=payments|purchases|subscriptions|charges|credits&range=
//
// Drill-down for the profitability dashboard (§82: every figure must reach its underlying
// rows). Execution figures drill through the richer /admin/cloud executions browser; this
// route serves the non-execution row types.
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const kind = url.searchParams.get('kind') ?? '';
    const rangeParam = url.searchParams.get('range') ?? '30d';
    if (!isRangeKey(rangeParam)) throw new ApiError(400, 'bad_request', 'invalid range');
    const range = rangeFor(rangeParam);
    const { take, skip } = pageParams(req);
    const created = range.start ? { gte: range.start, lt: range.end } : { lt: range.end };

    if (kind === 'payments') {
      const [rows, total] = await Promise.all([
        prisma.billingPayment.findMany({
          where: { occurredAt: created },
          orderBy: { occurredAt: 'desc' },
          take,
          skip,
          include: { account: { select: { address: true, displayName: true, handle: true } } },
        }),
        prisma.billingPayment.count({ where: { occurredAt: created } }),
      ]);
      return ok({ kind, rows, total });
    }

    if (kind === 'purchases') {
      const [rows, total] = await Promise.all([
        prisma.cloudAppPurchase.findMany({
          where: { createdAt: created },
          orderBy: { createdAt: 'desc' },
          take,
          skip,
          include: {
            app: { select: { slug: true, name: true } },
            account: { select: { address: true, displayName: true, handle: true } },
          },
        }),
        prisma.cloudAppPurchase.count({ where: { createdAt: created } }),
      ]);
      return ok({ kind, rows, total });
    }

    if (kind === 'subscriptions') {
      const where = { priceUsdCents: { gt: 0 }, status: { not: 'PENDING' as const }, createdAt: { lt: range.end } };
      const [rows, total] = await Promise.all([
        prisma.planSubscription.findMany({
          where,
          orderBy: { updatedAt: 'desc' },
          take,
          skip,
          include: { account: { select: { address: true, displayName: true, handle: true } } },
        }),
        prisma.planSubscription.count({ where }),
      ]);
      return ok({ kind, rows, total });
    }

    if (kind === 'charges') {
      const [rows, total] = await Promise.all([
        prisma.usageCharge.findMany({
          where: { createdAt: created },
          orderBy: { updatedAt: 'desc' },
          take,
          skip,
          include: { account: { select: { address: true, displayName: true, handle: true } } },
        }),
        prisma.usageCharge.count({ where: { createdAt: created } }),
      ]);
      return ok({ kind, rows, total });
    }

    if (kind === 'credits') {
      const [rows, total] = await Promise.all([
        prisma.cloudCredit.findMany({
          where: { createdAt: created },
          orderBy: { createdAt: 'desc' },
          take,
          skip,
          include: { account: { select: { address: true, displayName: true, handle: true } } },
        }),
        prisma.cloudCredit.count({ where: { createdAt: created } }),
      ]);
      return ok({ kind, rows, total });
    }

    throw new ApiError(400, 'bad_request', `unknown drill kind '${kind}'`);
  } catch (e) {
    return err(e);
  }
}
