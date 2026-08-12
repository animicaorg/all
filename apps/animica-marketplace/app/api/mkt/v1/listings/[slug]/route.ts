import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/listings/[slug] -> full public detail.
export async function GET(_req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      include: {
        owner: { select: { address: true, displayName: true, isAgent: true } },
        prices: { where: { active: true } },
        sources: { select: { id: true, title: true, kind: true, status: true } },
        versions: { orderBy: { version: 'desc' }, take: 20, select: { version: true, notes: true, createdAt: true } },
        reviews: { orderBy: { createdAt: 'desc' }, take: 20, select: { rating: true, text: true, createdAt: true } },
      },
    });
    if (!listing || listing.status === 'DRAFT') throw new ApiError(404, 'not_found', 'listing not found');
    const avgRating = listing.ratingCount ? listing.ratingSum / listing.ratingCount : 0;
    return ok({ listing: jsonSafe({ ...listing, avgRating }) });
  } catch (e) {
    return err(e);
  }
}

// PATCH /api/mkt/v1/listings/[slug] -> owner edits (scope: publish).
export async function PATCH(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const listing = await prisma.listing.findUnique({ where: { slug: params.slug } });
    if (!listing) throw new ApiError(404, 'not_found', 'listing not found');
    if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    const body = await req.json();
    const data: any = {};
    for (const f of ['name', 'tagline', 'description', 'systemPrompt', 'model', 'category', 'coverUrl']) {
      if (typeof body[f] === 'string') data[f] = body[f];
    }
    if (typeof body.temperature === 'number') data.temperature = body.temperature;
    const updated = await prisma.listing.update({ where: { id: listing.id }, data });
    return ok({ listing: jsonSafe(updated) });
  } catch (e) {
    return err(e);
  }
}
