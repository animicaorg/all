import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { requireHireAdmin } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Admin: orders list + tab counts. ?tab=pending|active|quotes|failed|cancelled|all
const TAB_WHERE: Record<string, any> = {
  pending: { deploymentStatus: 'pending_setup' },
  active: { deploymentStatus: 'active' },
  quotes: { deploymentStatus: 'quote_requested' },
  failed: { paymentStatus: { in: ['failed', 'suspended'] } },
  // Quote requests keep paymentStatus 'quote' when cancelled, so match either side.
  cancelled: { OR: [{ paymentStatus: 'cancelled' }, { deploymentStatus: 'cancelled' }] },
  // Bookkeeping: every order with manually-entered server dates, soonest renewal first.
  renewals: { serverEndAt: { not: null } },
  all: { NOT: { paymentStatus: 'draft' } },
};

export async function GET(req: NextRequest) {
  try {
    await requireHireAdmin(req);
    const tab = req.nextUrl.searchParams.get('tab') || 'pending';
    const where = TAB_WHERE[tab] ?? TAB_WHERE.pending;

    const [orders, ...counts] = await Promise.all([
      prisma.servicesOrder.findMany({
        where,
        orderBy: tab === 'renewals' ? { serverEndAt: 'asc' } : { createdAt: 'desc' },
        take: 200,
        include: { user: { select: { email: true, name: true } } },
      }),
      ...Object.values(TAB_WHERE).map((w) => prisma.servicesOrder.count({ where: w })),
    ]);
    const tabCounts = Object.fromEntries(Object.keys(TAB_WHERE).map((k, i) => [k, counts[i]]));
    return ok({ orders, tabCounts });
  } catch (e) {
    return err(e);
  }
}
