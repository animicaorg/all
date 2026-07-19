import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { assetUrl } from '@/lib/storeCatalog';
import { topScoresByListing, gameOk, gameErr, gamePreflight } from '@/lib/gameSocial';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/games/trending?limit=
// Public "Trending Games" discovery surface: published GAMES web-game listings ordered by
// play-count (newest as the tie-break), each carrying its playCount + top-score badge. Reads only,
// wildcard CORS — consumed by the storefront discovery row + the wallet Store tab. Cosmetic ranking
// (play counts are client-reported), never tied to ANM.
//
// Routing note: this static `trending` segment sits beside the `[slug]` game routes but one level
// up (the [slug] routes are /games/[slug]/{score,leaderboard,stats,play}), so there is no conflict;
// a game whose slug is literally "trending" still has working sub-routes, only its bare listing URL
// is shadowed here — and there is no bare /games/[slug] route anyway.
export async function GET(req: NextRequest) {
  try {
    const limit = Math.min(Math.max(Number(req.nextUrl.searchParams.get('limit') ?? 12), 1), 50);

    const listings = await prisma.listing.findMany({
      where: {
        status: 'PUBLISHED',
        visibility: 'PUBLIC',
        type: 'DIGITAL_GOOD',
        appCategory: 'GAMES',
        bundleCid: { not: null },
      },
      orderBy: [{ playCount: 'desc' }, { publishedAt: 'desc' }],
      take: limit,
      select: {
        id: true, slug: true, name: true, tagline: true, coverUrl: true, verified: true,
        playCount: true, publishedAt: true,
        owner: { select: { address: true, displayName: true } },
        anmDomain: { select: { name: true } },
        storeAssets: { where: { kind: 'ICON' }, orderBy: { sortOrder: 'asc' }, take: 1, select: { cid: true } },
      },
    });

    const tops = await topScoresByListing(listings.map((l) => l.id));

    return gameOk({
      games: listings.map((l) => ({
        slug: l.slug,
        name: l.name,
        tagline: l.tagline,
        coverUrl: l.coverUrl,
        verified: l.verified,
        playCount: l.playCount,
        topScore: tops.get(l.id) ?? null,
        publishedAt: l.publishedAt,
        iconUrl: l.storeAssets[0] ? assetUrl(l.storeAssets[0].cid) : null,
        publisher: {
          address: l.owner.address,
          displayName: l.owner.displayName,
          anm: l.anmDomain ? `${l.anmDomain.name}.anm` : null,
        },
      })),
    });
  } catch (e) {
    return gameErr(e);
  }
}

export function OPTIONS() {
  return gamePreflight();
}
