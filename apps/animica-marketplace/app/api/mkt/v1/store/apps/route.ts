import { NextRequest } from 'next/server';
import { authenticate, requireScope, err, ApiError, withIdempotency, publicOk, mixedPreflight, withCredentialedCors } from '@/lib/api';
import { prisma } from '@/lib/db';
import { createAppListing, normalizeAppCategory } from '@/lib/appStore';
import { STORE_TYPES, LATEST_BUILD_SELECT, assetUrl, asApiError } from '@/lib/storeCatalog';
import { topScoresByListing } from '@/lib/gameSocial';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// App Store catalog + listing creation. Store listings are ordinary Listing rows with
// type APP | DIGITAL_GOOD; APK releases live in AppBuild, imagery in StoreAsset. All
// creation/policy logic is lib/appStore.ts — this route is transport + auth only.

// GET /api/mkt/v1/store/apps?category=&q=&type=&sort=top|new|trending&limit=&offset= -> public catalog.
// Published store listings only; APP listings must have at least one APPROVED build.
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const q = (sp.get('q') ?? sp.get('search'))?.trim() ?? '';
    const category = normalizeAppCategory(sp.get('category'));
    const type = sp.get('type')?.trim().toUpperCase() ?? '';
    const sort = sp.get('sort') ?? 'top';
    const take = Math.min(Number(sp.get('limit') ?? 40), 100);
    const skip = Math.max(Number(sp.get('offset') ?? 0), 0);

    const where: any = {
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      type: (STORE_TYPES as readonly string[]).includes(type) ? type : { in: [...STORE_TYPES] },
      // Never list an installable app that has nothing approved to install.
      AND: [{ OR: [{ type: 'DIGITAL_GOOD' }, { builds: { some: { status: 'APPROVED' } } }] }],
    };
    if (category) where.appCategory = category;
    if (q) {
      where.AND.push({
        OR: [
          { name: { contains: q, mode: 'insensitive' } },
          { tagline: { contains: q, mode: 'insensitive' } },
          { description: { contains: q, mode: 'insensitive' } },
          { packageName: { contains: q, mode: 'insensitive' } },
        ],
      });
    }
    const orderBy =
      sort === 'new' ? { publishedAt: 'desc' as const } :
      sort === 'trending' ? { usageCount: 'desc' as const } :
      { ratingCount: 'desc' as const };

    const listings = await prisma.listing.findMany({
      where, orderBy, take, skip,
      select: {
        id: true,
        slug: true, name: true, tagline: true, type: true, appCategory: true, packageName: true,
        coverUrl: true, verified: true, usersCount: true, playCount: true, ratingSum: true, ratingCount: true, publishedAt: true,
        owner: { select: { address: true, displayName: true } },
        anmDomain: { select: { name: true } },
        prices: { where: { active: true }, select: { id: true, model: true, amountNanm: true, perCallNanm: true, periodDays: true, label: true } },
        storeAssets: { where: { kind: 'ICON' }, orderBy: { sortOrder: 'asc' }, take: 1, select: { cid: true } },
        builds: { where: { status: 'APPROVED', channel: 'stable' }, orderBy: { versionCode: 'desc' }, take: 1, select: LATEST_BUILD_SELECT },
      },
    });
    // Cosmetic game-card badges: top score per game in ONE grouped query (no N+1). playCount is the
    // denormalized column already selected above. Both are "for fun" — never tied to ANM.
    const topScores = await topScoresByListing(listings.map((l) => l.id));
    const apps = listings.map((l) => ({
      slug: l.slug, name: l.name, tagline: l.tagline, type: l.type, category: l.appCategory,
      packageName: l.packageName, coverUrl: l.coverUrl, verified: l.verified,
      usersCount: l.usersCount,
      playCount: l.playCount, topScore: topScores.get(l.id) ?? null,
      avgRating: l.ratingCount ? l.ratingSum / l.ratingCount : 0, ratingCount: l.ratingCount,
      publishedAt: l.publishedAt,
      publisher: { address: l.owner.address, displayName: l.owner.displayName, anm: l.anmDomain ? `${l.anmDomain.name}.anm` : null },
      iconUrl: l.storeAssets[0] ? assetUrl(l.storeAssets[0].cid) : null,
      prices: l.prices,
      latestBuild: l.builds[0] ?? null,
    }));
    // publicOk: the wallet Store tab + native .anm sites read this cross-origin.
    return publicOk({ apps: jsonSafe(apps) });
  } catch (e) {
    return err(asApiError(e));
  }
}

// CORS preflight. Public catalog read (wildcard) for everyone; credentialed (allow-listed origin)
// for the Game Lab cross-origin publish POST from animica.io.
export async function OPTIONS(req: NextRequest) {
  return mixedPreflight(req, 'GET, POST, OPTIONS');
}

// POST /api/mkt/v1/store/apps -> create a DRAFT store listing (scope: publish).
// {name, slug?, type?, category?, packageName?, description?, tagline?, anmDomain?, prices?}
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json();
    if (!body.name || typeof body.name !== 'string') throw new ApiError(400, 'bad_request', 'name required');

    return withCredentialedCors(req, await withIdempotency(req, ctx, body, async () => {
      const listing = await createAppListing({
        ownerId: ctx.accountId,
        name: body.name,
        slug: typeof body.slug === 'string' ? body.slug : undefined,
        type: body.type === 'DIGITAL_GOOD' ? 'DIGITAL_GOOD' : 'APP',
        appCategory: typeof body.category === 'string' ? body.category : undefined,
        packageName: typeof body.packageName === 'string' ? body.packageName : undefined,
        description: typeof body.description === 'string' ? body.description : undefined,
        tagline: typeof body.tagline === 'string' ? body.tagline : undefined,
        anmDomain: typeof body.anmDomain === 'string' ? body.anmDomain : undefined,
      });
      // Optional prices on create (same shape as the AI-listings route).
      if (Array.isArray(body.prices)) {
        for (const p of body.prices.slice(0, 6)) {
          const model = ['FREE', 'ONE_TIME', 'SUBSCRIPTION'].includes(p.model) ? p.model : 'FREE';
          await prisma.price.create({
            data: {
              listingId: listing.id,
              model,
              amountNanm: BigInt(p.amountNanm ?? 0),
              periodDays: Number(p.periodDays ?? 30),
              label: p.label ?? null,
            },
          });
        }
      }
      return { status: 201, data: { listing: jsonSafe(listing) } };
    }));
  } catch (e) {
    return withCredentialedCors(req, err(asApiError(e)));
  }
}
