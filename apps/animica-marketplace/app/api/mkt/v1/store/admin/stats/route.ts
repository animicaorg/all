import { NextRequest } from 'next/server';
import { ok, err } from '@/lib/api';
import { prisma } from '@/lib/db';
import { requireAdmin } from '@/lib/adminAuth';
import { PUBLISHER_QUOTA_BYTES } from '@/lib/appStore';
import { STORE_TYPES } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/admin/stats -> review-console dashboard numbers.
// Admin only (MKT_ADMIN_TOKEN header or role ADMIN account). Read-only aggregates:
//   * buildCounts  — AppBuild rows per status (queue depth at a glance);
//   * publishers   — durable build bytes per publisher vs STORE_PUBLISHER_QUOTA_BYTES
//                    (same accounting registerBuild enforces: every non-CHECKS_FAILED
//                    build across all their listings);
//   * recentPurchases — latest store-type purchases, the feed the refund action works
//                    from (there is no other admin purchase-lookup surface).
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const [statusCounts, perListing, recentPurchases] = await Promise.all([
      prisma.appBuild.groupBy({ by: ['status'], _count: { _all: true } }),
      prisma.appBuild.groupBy({
        by: ['listingId'],
        where: { status: { not: 'CHECKS_FAILED' } },
        _sum: { sizeBytes: true },
        _count: { _all: true },
      }),
      prisma.purchase.findMany({
        where: { listing: { type: { in: [...STORE_TYPES] } } },
        orderBy: { createdAt: 'desc' },
        take: 30,
        select: {
          id: true, amountNanm: true, status: true, priceModel: true, source: true, createdAt: true,
          buyer: { select: { address: true, displayName: true } },
          listing: { select: { slug: true, name: true, type: true } },
        },
      }),
    ]);

    // Fold per-listing build storage up to its publisher (owner).
    const listings = perListing.length
      ? await prisma.listing.findMany({
          where: { id: { in: perListing.map((r) => r.listingId) } },
          select: { id: true, slug: true, owner: { select: { id: true, address: true, displayName: true } } },
        })
      : [];
    const listingById = new Map(listings.map((l) => [l.id, l]));
    const byOwner = new Map<string, { address: string; displayName: string | null; bytesUsed: bigint; buildCount: number; listings: string[] }>();
    for (const row of perListing) {
      const l = listingById.get(row.listingId);
      if (!l) continue; // row orphaned mid-query; skip
      const p = byOwner.get(l.owner.id) ?? { address: l.owner.address, displayName: l.owner.displayName, bytesUsed: 0n, buildCount: 0, listings: [] };
      p.bytesUsed += row._sum.sizeBytes ?? 0n;
      p.buildCount += row._count._all;
      p.listings.push(l.slug);
      byOwner.set(l.owner.id, p);
    }
    const publishers = [...byOwner.values()].sort((a, b) => (b.bytesUsed > a.bytesUsed ? 1 : b.bytesUsed < a.bytesUsed ? -1 : 0));

    return ok(jsonSafe({
      quotaBytes: PUBLISHER_QUOTA_BYTES,
      buildCounts: Object.fromEntries(statusCounts.map((s) => [s.status, s._count._all])),
      publishers,
      recentPurchases,
    }));
  } catch (e) {
    return err(e);
  }
}
