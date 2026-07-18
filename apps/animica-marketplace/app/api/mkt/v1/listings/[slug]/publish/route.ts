import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/listings/[slug]/publish -> DRAFT|DELISTED -> PUBLISHED, snapshot a version.
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const listing = await prisma.listing.findUnique({ where: { slug: params.slug }, include: { prices: true } });
    if (!listing) throw new ApiError(404, 'not_found', 'listing not found');
    if (listing.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    if (!listing.prices.length) throw new ApiError(400, 'no_price', 'add at least one price (FREE is fine) before publishing');

    const version = listing.version + (listing.publishedAt ? 1 : 0);
    const updated = await prisma.$transaction(async (tx) => {
      await tx.listingVersion.create({
        data: {
          listingId: listing.id,
          version,
          snapshotJson: JSON.stringify({
            name: listing.name, systemPrompt: listing.systemPrompt, model: listing.model,
            temperature: listing.temperature, mediaKind: listing.mediaKind,
          }),
          notes: (await req.json().catch(() => ({})))?.notes ?? '',
        },
      });
      return tx.listing.update({
        where: { id: listing.id },
        data: { status: 'PUBLISHED', version, publishedAt: listing.publishedAt ?? new Date() },
      });
    });
    return ok({ listing: jsonSafe(updated) });
  } catch (e) {
    return err(e);
  }
}
