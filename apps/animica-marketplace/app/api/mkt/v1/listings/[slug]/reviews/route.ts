import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { checkEntitlement } from '@/lib/entitlement';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

const MAX_REVIEW_CHARS = 4000;

// POST /api/mkt/v1/listings/[slug]/reviews { rating 1..5, text? } -> verified-buyer review.
// Gated on entitlement (owner or ACTIVE unexpired purchase — @@unique(listingId, accountId)
// makes it one review per buyer, editable). ratingSum/ratingCount stay consistent in the
// same transaction. Read paths already exist (listing detail returns the latest 20).
export async function POST(req: NextRequest, { params }: { params: { slug: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'use');
    const body = await req.json();

    const rating = body.rating;
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
      throw new ApiError(400, 'bad_rating', 'rating must be an integer 1..5');
    }
    const text = typeof body.text === 'string' ? body.text.trim() : '';
    if (text.length > MAX_REVIEW_CHARS) {
      throw new ApiError(400, 'text_too_long', `review text is capped at ${MAX_REVIEW_CHARS} chars`);
    }

    const listing = await prisma.listing.findUnique({
      where: { slug: params.slug },
      select: { id: true, ownerId: true, status: true },
    });
    if (!listing || listing.status !== 'PUBLISHED') throw new ApiError(404, 'not_found', 'listing not found');
    if (listing.ownerId === ctx.accountId) throw new ApiError(400, 'self_review', 'cannot review your own listing');

    const ent = await checkEntitlement(ctx.accountId, listing.id);
    if (!ent.entitled) throw new ApiError(403, 'not_entitled', 'purchase required to review');

    const review = await prisma.$transaction(async (tx) => {
      const existing = await tx.review.findUnique({
        where: { listingId_accountId: { listingId: listing.id, accountId: ctx.accountId } },
      });
      if (existing) {
        const updated = await tx.review.update({ where: { id: existing.id }, data: { rating, text } });
        await tx.listing.update({
          where: { id: listing.id },
          data: { ratingSum: { increment: rating - existing.rating } },
        });
        return updated;
      }
      const created = await tx.review.create({
        data: { listingId: listing.id, accountId: ctx.accountId, rating, text },
      });
      await tx.listing.update({
        where: { id: listing.id },
        data: { ratingSum: { increment: rating }, ratingCount: { increment: 1 } },
      });
      return created;
    });

    const fresh = await prisma.listing.findUnique({
      where: { id: listing.id },
      select: { ratingSum: true, ratingCount: true },
    });
    const avgRating = fresh && fresh.ratingCount ? fresh.ratingSum / fresh.ratingCount : rating;
    return ok({ review: jsonSafe(review), avgRating, ratingCount: fresh?.ratingCount ?? 1 }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
