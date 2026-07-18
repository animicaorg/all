import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/earnings -> creator dashboard data: listings + sales + lifetime earned.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');

    const listings = await prisma.listing.findMany({
      where: { ownerId: ctx.accountId },
      orderBy: { createdAt: 'desc' },
      select: { slug: true, name: true, status: true, type: true, usersCount: true, usageCount: true, ratingSum: true, ratingCount: true },
    });

    const credits = await prisma.ledgerEntry.aggregate({
      where: { accountId: ctx.accountId, kind: { in: ['SALE_CREDIT', 'FORK_ROYALTY'] } },
      _sum: { deltaNanm: true },
      _count: true,
    });
    const earned = credits._sum.deltaNanm ?? 0n;

    return ok({
      listings: jsonSafe(listings),
      summary: { earnedNanm: earned.toString(), earnedAnm: nanmToAnm(earned), sales: credits._count },
    });
  } catch (e) {
    return err(e);
  }
}
