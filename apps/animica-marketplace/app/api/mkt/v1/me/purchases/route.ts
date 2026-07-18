import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/purchases -> listings the caller has access to.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const purchases = await prisma.purchase.findMany({
      where: { buyerId: ctx.accountId, status: { in: ['ACTIVE'] } },
      orderBy: { createdAt: 'desc' },
      include: { listing: { select: { slug: true, name: true, type: true } } },
    });
    return ok({ purchases: jsonSafe(purchases) });
  } catch (e) {
    return err(e);
  }
}
