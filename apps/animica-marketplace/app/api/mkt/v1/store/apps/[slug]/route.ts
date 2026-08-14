import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, publicOk, mixedPreflight, withCredentialedCors } from '@/lib/api';
import { prisma } from '@/lib/db';
import { updateAppListing } from '@/lib/appStore';
import { STORE_TYPES, LATEST_BUILD_SELECT, assetUrl, asApiError } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/apps/[slug] -> full public store detail (screenshots, prices,
// latest APPROVED build incl. permissions, publisher .anm, reviews).
export async function GET(_req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      include: {
        owner: { select: { address: true, displayName: true, isAgent: true } },
        anmDomain: { select: { name: true } },
        prices: { where: { active: true } },
        storeAssets: { orderBy: [{ kind: 'asc' }, { sortOrder: 'asc' }] },
        builds: {
          where: { status: 'APPROVED', channel: 'stable' },
          orderBy: { versionCode: 'desc' },
          take: 1,
          select: { ...LATEST_BUILD_SELECT, permissionsJson: true },
        },
        reviews: { orderBy: { createdAt: 'desc' }, take: 20, select: { rating: true, text: true, createdAt: true } },
      },
    });
    if (!listing || listing.status === 'DRAFT' || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'app not found');
    }
    const latest = listing.builds[0] ?? null;
    // Web-game social badges (cosmetic, never tied to ANM): playCount is the denormalized column
    // already loaded; topScore is one cheap aggregate, only for a web game (bundleCid present).
    const topScore = listing.bundleCid
      ? (await prisma.gameScore.aggregate({ where: { listingId: listing.id }, _max: { score: true } }))._max.score ?? null
      : null;
    return publicOk({
      app: jsonSafe({
        slug: listing.slug, name: listing.name, tagline: listing.tagline, description: listing.description,
        type: listing.type, category: listing.appCategory, packageName: listing.packageName,
        // Game Lab web game: CID of the self-contained play bundle (null = not a web game). FREE
        // games can be played straight from /api/mkt/v1/content/[bundleCid]; PAID games must go
        // through the license-gated play-token — clients decide from the price set.
        bundleCid: listing.bundleCid,
        coverUrl: listing.coverUrl, verified: listing.verified, status: listing.status,
        usersCount: listing.usersCount,
        // Cosmetic web-game social badges (null topScore = no scores yet / not a game).
        playCount: listing.playCount, topScore,
        avgRating: listing.ratingCount ? listing.ratingSum / listing.ratingCount : 0,
        ratingCount: listing.ratingCount, publishedAt: listing.publishedAt,
        publisher: {
          address: listing.owner.address, displayName: listing.owner.displayName,
          anm: listing.anmDomain ? `${listing.anmDomain.name}.anm` : null,
        },
        assets: listing.storeAssets.map((a) => ({
          kind: a.kind, cid: a.cid, url: assetUrl(a.cid), width: a.width, height: a.height, sortOrder: a.sortOrder,
        })),
        prices: listing.prices,
        latestBuild: latest ? { ...latest, permissions: latest.permissionsJson, permissionsJson: undefined } : null,
        reviews: listing.reviews,
      }),
    });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight. Public detail read (wildcard) for the wallet Store tab + .anm sites; credentialed
// (allow-listed origin) for the Game Lab cross-origin publish PATCH from animica.io.
export async function OPTIONS(req: NextRequest) {
  return mixedPreflight(req, 'GET, PATCH, OPTIONS');
}

// PATCH /api/mkt/v1/store/apps/[slug] -> owner edits metadata / visibility / status /
// .anm link (scope: publish). Policy (publish-requires-approved-build, .anm ownership,
// packageName/cert immutability) lives in lib/appStore.ts updateAppListing.
export async function PATCH(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json();

    const patch: any = {};
    for (const f of ['name', 'tagline', 'description', 'coverUrl', 'visibility', 'status'] as const) {
      if (typeof body[f] === 'string') patch[f] = body[f];
    }
    if (typeof body.category === 'string') patch.appCategory = body.category;
    if (body.anmDomain === null || typeof body.anmDomain === 'string') patch.anmDomain = body.anmDomain;

    const updated = await updateAppListing(ctx.accountId, params.slug, patch);
    return withCredentialedCors(req, ok({ listing: jsonSafe(updated) }));
  } catch (e) {
    return withCredentialedCors(req, err(asApiError(e)));
  }
}
