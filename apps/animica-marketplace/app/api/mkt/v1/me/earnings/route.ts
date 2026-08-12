import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/earnings[?byListing=1] -> creator dashboard data: listings + sales + lifetime
// earned. With byListing, adds a per-listing revenue breakdown (used by the Developer Center
// earnings page). Purchase SALE_CREDIT entries carry ref = listingId (see lib/ledger.ts
// settlePurchaseInTx), so per-listing revenue is a group-by-ref over the owner's listing ids.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const byListing = ['1', 'true', 'yes'].includes((req.nextUrl.searchParams.get('byListing') ?? '').toLowerCase());

    const listings = await prisma.listing.findMany({
      where: { ownerId: ctx.accountId },
      orderBy: { createdAt: 'desc' },
      select: { id: true, slug: true, name: true, status: true, type: true, appCategory: true, usersCount: true, usageCount: true, ratingSum: true, ratingCount: true },
    });

    const credits = await prisma.ledgerEntry.aggregate({
      where: { accountId: ctx.accountId, kind: { in: ['SALE_CREDIT', 'FORK_ROYALTY'] } },
      _sum: { deltaNanm: true },
      _count: true,
    });
    const earned = credits._sum.deltaNanm ?? 0n;

    const data: any = {
      listings: jsonSafe(listings.map(({ id, ...rest }) => rest)),
      summary: { earnedNanm: earned.toString(), earnedAnm: nanmToAnm(earned), sales: credits._count },
    };

    if (byListing) {
      const ids = listings.map((l) => l.id);
      const grouped = ids.length
        ? await prisma.ledgerEntry.groupBy({
            by: ['ref'],
            where: { accountId: ctx.accountId, kind: { in: ['SALE_CREDIT', 'FORK_ROYALTY'] }, ref: { in: ids } },
            _sum: { deltaNanm: true },
            _count: true,
          })
        : [];
      const byRef = new Map(grouped.map((g) => [g.ref!, g]));
      let attributed = 0n;
      const perListing = listings.map((l) => {
        const g = byRef.get(l.id);
        const e = g?._sum.deltaNanm ?? 0n;
        attributed += e;
        return {
          slug: l.slug, name: l.name, type: l.type, status: l.status, category: l.appCategory,
          earnedNanm: e.toString(), earnedAnm: nanmToAnm(e), sales: g?._count ?? 0,
        };
      });
      const unattributed = earned - attributed;
      data.byListing = perListing;
      // Credits not tied to a listing id (e.g. per-call USAGE sales, agent offers, task payouts).
      data.unattributedNanm = unattributed.toString();
      data.unattributedAnm = nanmToAnm(unattributed);
    }

    return ok(data);
  } catch (e) {
    return err(e);
  }
}
