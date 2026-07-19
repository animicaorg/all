import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { checkPlayEntitlement } from '@/lib/entitlement';
import { mintPlayToken } from '@/lib/playToken';
import { STORE_TYPES } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/play-token/[slug] -> entitlement-gated, short-lived (10 min) HMAC
// token + play URL for a web game's self-contained HTML bundle (Listing.bundleCid). The exact
// twin of the download-token route, for the PLAY surface instead of the download: a free game
// mints for anyone, a paid game only for the owner or an active purchase. The public content
// CID route must NEVER serve a paid bundle (immutable/ungated = paywall leak) — paid play only
// ever flows through this token + GET /api/mkt/v1/store/play/[token].
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'use');

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: {
        id: true,
        ownerId: true,
        type: true,
        status: true,
        bundleCid: true,
        prices: { where: { active: true }, select: { model: true } },
      },
    });
    if (!listing || !(STORE_TYPES as readonly string[]).includes(listing.type)) {
      throw new ApiError(404, 'not_found', 'game not found');
    }
    // DRAFT listings are visible only to their owner (who is always entitled); a DELISTED game
    // is unavailable to everyone (parity with a build no longer APPROVED).
    if (listing.status === 'DRAFT' && listing.ownerId !== ctx.accountId) {
      throw new ApiError(404, 'not_found', 'game not found');
    }
    if (listing.status === 'DELISTED') throw new ApiError(410, 'not_available', 'game is no longer available');
    if (!listing.bundleCid) throw new ApiError(404, 'no_bundle', 'no playable bundle for this listing');

    const ent = await checkPlayEntitlement(ctx.accountId, listing);
    if (!ent.entitled) throw new ApiError(403, 'not_entitled', 'purchase required');

    const { token, expiresAt } = mintPlayToken(ctx.accountId, listing.id);
    return ok(jsonSafe({
      token,
      url: `/api/mkt/v1/store/play/${token}`,
      expiresAt,
      slug: params.slug,
      listingId: listing.id,
      priceModel: ent.priceModel,
    }));
  } catch (e) {
    return err(e);
  }
}
