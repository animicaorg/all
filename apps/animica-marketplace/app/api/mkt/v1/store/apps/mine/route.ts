import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { STORE_TYPES, assetUrl, asApiError } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// Developer Center — the owner's OWN store listings (drafts included), which the public
// catalog route deliberately hides. Powers /dev (my-apps list + build-status chips) and the
// /dev editor's header. Scope 'publish' gates it so the dev console is reachable only from a
// devportal session (or a publish-scoped key / full session), never a buyer 'store' session.
//
//   GET /api/mkt/v1/store/apps/mine              -> { address, apps: [summary + latestBuild + buildCounts] }
//   GET /api/mkt/v1/store/apps/mine?slug=<slug>  -> { address, app: {full owner detail incl. prices} }

const PRICE_SELECT = { id: true, model: true, amountNanm: true, perCallNanm: true, periodDays: true, label: true, active: true } as const;
const LATEST_BUILD_SELECT = { status: true, versionName: true, versionCode: true, channel: true, createdAt: true } as const;

type BuildCounts = Record<string, number>;

// One grouped query -> per-listing status histogram (APPROVED / PENDING_REVIEW / …).
async function buildCountsFor(listingIds: string[]): Promise<Map<string, BuildCounts>> {
  const map = new Map<string, BuildCounts>();
  if (listingIds.length === 0) return map;
  const grouped = await prisma.appBuild.groupBy({
    by: ['listingId', 'status'],
    where: { listingId: { in: listingIds } },
    _count: { _all: true },
  });
  for (const g of grouped) {
    const c = map.get(g.listingId) ?? {};
    c[g.status] = g._count._all;
    map.set(g.listingId, c);
  }
  return map;
}

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');

    const acct = await prisma.account.findUnique({ where: { id: ctx.accountId }, select: { address: true } });
    const address = acct?.address ?? null;

    const slug = req.nextUrl.searchParams.get('slug')?.trim();

    // ── single-app owner detail (editor header) ─────────────────────────────────
    if (slug) {
      const l = await prisma.listing.findUnique({
        where: { slug },
        select: {
          id: true, slug: true, name: true, tagline: true, description: true, type: true,
          appCategory: true, status: true, visibility: true, verified: true, packageName: true,
          pinnedCertSha256: true, coverUrl: true, usersCount: true, ratingSum: true, ratingCount: true,
          createdAt: true, publishedAt: true, ownerId: true,
          prices: { orderBy: { createdAt: 'asc' }, select: PRICE_SELECT },
          storeAssets: { orderBy: [{ kind: 'asc' }, { sortOrder: 'asc' }], select: { id: true, kind: true, cid: true, width: true, height: true, sortOrder: true } },
          builds: { orderBy: { createdAt: 'desc' }, take: 1, select: LATEST_BUILD_SELECT },
          anmDomain: { select: { name: true } },
        },
      });
      if (!l || !(STORE_TYPES as readonly string[]).includes(l.type)) throw new ApiError(404, 'not_found', 'store listing not found');
      if (l.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not your listing');
      const counts = (await buildCountsFor([l.id])).get(l.id) ?? {};
      const { ownerId, storeAssets, ...rest } = l;
      return ok({
        address,
        app: jsonSafe({
          ...rest,
          category: l.appCategory,
          anmDomain: l.anmDomain ? `${l.anmDomain.name}.anm` : null,
          avgRating: l.ratingCount ? l.ratingSum / l.ratingCount : 0,
          assets: storeAssets.map((a) => ({ ...a, url: assetUrl(a.cid) })),
          latestBuild: l.builds[0] ?? null,
          buildCounts: counts,
        }),
      });
    }

    // ── list mode (my-apps) ─────────────────────────────────────────────────────
    const listings = await prisma.listing.findMany({
      where: { ownerId: ctx.accountId, type: { in: [...STORE_TYPES] } },
      orderBy: { createdAt: 'desc' },
      select: {
        id: true, slug: true, name: true, tagline: true, type: true, appCategory: true,
        status: true, visibility: true, verified: true, packageName: true, coverUrl: true,
        usersCount: true, ratingSum: true, ratingCount: true, createdAt: true, publishedAt: true,
        prices: { where: { active: true }, orderBy: { createdAt: 'asc' }, select: PRICE_SELECT },
        storeAssets: { where: { kind: 'ICON' }, take: 1, orderBy: { sortOrder: 'asc' }, select: { cid: true } },
        builds: { orderBy: { createdAt: 'desc' }, take: 1, select: LATEST_BUILD_SELECT },
      },
    });
    const counts = await buildCountsFor(listings.map((l) => l.id));
    const apps = listings.map((l) => ({
      slug: l.slug, name: l.name, tagline: l.tagline, type: l.type, category: l.appCategory,
      status: l.status, visibility: l.visibility, verified: l.verified, packageName: l.packageName,
      iconUrl: l.storeAssets[0] ? assetUrl(l.storeAssets[0].cid) : null,
      usersCount: l.usersCount, avgRating: l.ratingCount ? l.ratingSum / l.ratingCount : 0, ratingCount: l.ratingCount,
      createdAt: l.createdAt, publishedAt: l.publishedAt,
      prices: l.prices,
      latestBuild: l.builds[0] ?? null,
      buildCounts: counts.get(l.id) ?? {},
    }));
    return ok({ address, apps: jsonSafe(apps) });
  } catch (e) {
    return err(asApiError(e));
  }
}
