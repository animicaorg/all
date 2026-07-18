import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { settlePurchaseFromBalance, LedgerError } from '@/lib/ledger';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/purchases { slug, priceId } -> buy access, paying from ledger balance.
// This is the AI->AI commerce path: an agent's key (scope: buy) purchases another listing; the
// ledger splits 80% creator / 20% treasury (+ optional fork royalty) atomically.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');
    const body = await req.json();

    return await withIdempotency(req, ctx, body, async () => {
      const listing = await prisma.listing.findUnique({
        where: { slug: String(body.slug ?? '') },
        include: { prices: true, forkOf: { select: { ownerId: true } } },
      });
      if (!listing || listing.status !== 'PUBLISHED') throw new ApiError(404, 'not_found', 'listing not found');
      const price = listing.prices.find((p) => p.id === body.priceId && p.active)
        ?? listing.prices.find((p) => p.active);
      if (!price) throw new ApiError(400, 'no_price', 'no active price');
      if (listing.ownerId === ctx.accountId) throw new ApiError(400, 'self_purchase', 'cannot buy your own listing');

      // FREE / USAGE => entitlement with no upfront debit (USAGE is metered per ask()).
      if (price.model === 'FREE' || price.model === 'USAGE') {
        const purchase = await prisma.purchase.create({
          data: {
            buyerId: ctx.accountId, listingId: listing.id, priceId: price.id,
            priceModel: price.model, amountNanm: 0n, status: 'ACTIVE',
          },
        });
        await bumpUsers(listing.id);
        return { status: 201, data: { purchase: jsonSafe(purchase), entitled: true } };
      }

      // ONE_TIME / SUBSCRIPTION => debit + split.
      try {
        await settlePurchaseFromBalance({
          buyerId: ctx.accountId,
          creatorId: listing.ownerId,
          amountNanm: price.amountNanm,
          listingId: listing.id,
          forkParentCreatorId: listing.forkOf?.ownerId,
          forkRoyaltyBps: listing.forkOfId ? listing.forkRoyaltyBps : undefined,
        });
      } catch (e) {
        if (e instanceof LedgerError && e.code === 'INSUFFICIENT_FUNDS') {
          throw new ApiError(402, 'insufficient_funds', 'top up your balance to purchase');
        }
        throw e;
      }
      const expiresAt = price.model === 'SUBSCRIPTION'
        ? new Date(Date.now() + price.periodDays * 86400_000) : null;
      const purchase = await prisma.purchase.create({
        data: {
          buyerId: ctx.accountId, listingId: listing.id, priceId: price.id,
          priceModel: price.model, amountNanm: price.amountNanm, status: 'ACTIVE', expiresAt,
        },
      });
      await bumpUsers(listing.id);
      return { status: 201, data: { purchase: jsonSafe(purchase), entitled: true } };
    });
  } catch (e) {
    return err(e);
  }
}

async function bumpUsers(listingId: string) {
  await prisma.listing.update({ where: { id: listingId }, data: { usersCount: { increment: 1 } } }).catch(() => {});
}
