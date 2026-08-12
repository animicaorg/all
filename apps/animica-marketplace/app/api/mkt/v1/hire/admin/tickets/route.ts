import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { requireHireAdmin } from '@/lib/hireAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Admin: ticket queue. ?tab=open|closed|all — "open" includes every non-closed state.
export async function GET(req: NextRequest) {
  try {
    await requireHireAdmin(req);
    const tab = req.nextUrl.searchParams.get('tab') || 'open';
    const where =
      tab === 'closed' ? { status: 'closed' } :
      tab === 'all' ? {} :
      { NOT: { status: 'closed' } };
    const [tickets, openCount, closedCount] = await Promise.all([
      prisma.hireTicket.findMany({
        where,
        orderBy: { updatedAt: 'desc' },
        take: 200,
        include: {
          user: { select: { email: true, name: true } },
          order: { select: { orderId: true, serviceName: true } },
          _count: { select: { messages: true } },
        },
      }),
      prisma.hireTicket.count({ where: { NOT: { status: 'closed' } } }),
      prisma.hireTicket.count({ where: { status: 'closed' } }),
    ]);
    return ok({ tickets, counts: { open: openCount, closed: closedCount } });
  } catch (e) {
    return err(e);
  }
}
