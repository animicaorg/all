import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { config } from '@/lib/config';
import { bps } from '@/lib/nanm';
import { buildStoreMemoHex, newStoreNonce } from '@/lib/storeMemo';
import { STORE_TYPES } from '@/lib/storeCatalog';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/purchases/intent { slug, priceId? } -> non-custodial wallet payment.
// Creates Purchase(PENDING_PAYMENT, source='wallet') + a single-use StorePaymentIntent and
// returns { payTo, amountNanm, memoHex, expiresAt, split } for the wallet's pre-confirm
// sheet. The buyer then pays the DEDICATED store treasury on-chain with the exact ANMSTORE1
// memo bytes; the payment watcher — never this route — verifies and activates (12-conf
// finality + sender balance-delta execution proof + treasury baseline reconciliation).
// FAIL-CLOSED: 503 until STORE_TREASURY_ADDRESS + STORE_FEE_BPS are configured.

const INTENT_TTL_MS = 30 * 60_000; // 30 min

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');
    const body = await req.json();

    return await withIdempotency(req, ctx, body, async () => {
      // Fail closed: never hand out a pay-to target while the store side is unconfigured.
      if (!config.storeTreasuryAddress || !config.storeTreasuryAddress.startsWith('anim1')) {
        throw new ApiError(503, 'store_unconfigured', 'STORE_TREASURY_ADDRESS is not configured');
      }
      if (!Number.isInteger(config.storeFeeBps) || config.storeFeeBps < 0 || config.storeFeeBps >= 10_000) {
        throw new ApiError(503, 'store_unconfigured', 'STORE_FEE_BPS is not configured');
      }

      const listing = await prisma.listing.findUnique({
        where: { slug: String(body.slug ?? '') },
        include: { prices: true },
      });
      if (!listing || listing.status !== 'PUBLISHED') throw new ApiError(404, 'not_found', 'listing not found');
      if (!(STORE_TYPES as readonly string[]).includes(listing.type)) {
        throw new ApiError(400, 'not_store_listing', 'wallet payment intents are for APP / DIGITAL_GOOD listings');
      }
      if (listing.ownerId === ctx.accountId) throw new ApiError(400, 'self_purchase', 'cannot buy your own listing');

      const price = listing.prices.find((p) => p.id === body.priceId && p.active)
        ?? listing.prices.find((p) => p.active && p.model === 'ONE_TIME');
      if (!price) throw new ApiError(400, 'no_price', 'no active price');
      // Non-custodial wallet pay is ONE_TIME only: FREE needs no payment and SUBSCRIPTION
      // renewals are custodial by design (chain has no pull-payment primitive) — both go
      // through POST /api/mkt/v1/purchases instead.
      if (price.model !== 'ONE_TIME') {
        throw new ApiError(400, 'bad_price_model', 'wallet intents are ONE_TIME only; use POST /purchases for FREE/SUBSCRIPTION');
      }
      if (price.amountNanm <= 0n) throw new ApiError(400, 'bad_price', 'price must be > 0');

      const buyer = await prisma.account.findUnique({ where: { id: ctx.accountId }, select: { address: true } });
      if (!buyer) throw new ApiError(401, 'unauthorized', 'account not found');

      const now = new Date();
      const owned = await prisma.purchase.findFirst({
        where: {
          buyerId: ctx.accountId, listingId: listing.id, status: 'ACTIVE',
          OR: [{ expiresAt: null }, { expiresAt: { gt: now } }],
        },
        select: { id: true },
      });
      if (owned) throw new ApiError(409, 'already_owned', 'you already own this listing');

      // One live intent per (buyer, listing): re-requesting returns the pending intent
      // instead of minting parallel pay-to targets (each intent is single-use).
      const pending = await prisma.purchase.findFirst({
        where: {
          buyerId: ctx.accountId, listingId: listing.id, status: 'PENDING_PAYMENT',
          paymentIntent: { is: { expiresAt: { gt: now }, verifiedAt: null } },
        },
        include: { paymentIntent: true },
        orderBy: { createdAt: 'desc' },
      });
      if (pending?.paymentIntent) return { status: 200, data: shape(pending.id, pending.paymentIntent) };

      const expiresAt = new Date(Date.now() + INTENT_TTL_MS);
      const created = await prisma.$transaction(async (tx) => {
        const purchase = await tx.purchase.create({
          data: {
            buyerId: ctx.accountId, listingId: listing.id, priceId: price.id,
            priceModel: price.model, amountNanm: price.amountNanm,
            status: 'PENDING_PAYMENT', source: 'wallet',
          },
        });
        const nonce = newStoreNonce();
        const memoHex = buildStoreMemoHex({
          pid: purchase.id, b: buyer.address, l: listing.id, a: price.amountNanm, n: nonce,
        });
        const intent = await tx.storePaymentIntent.create({
          data: {
            purchaseId: purchase.id, payTo: config.storeTreasuryAddress,
            amountNanm: price.amountNanm, memoHex, nonce, expiresAt,
          },
        });
        return { purchase, intent };
      });
      return { status: 201, data: shape(created.purchase.id, created.intent) };
    });
  } catch (e) {
    return err(e);
  }
}

function shape(purchaseId: string, intent: { payTo: string; amountNanm: bigint; memoHex: string; expiresAt: Date }) {
  const fee = bps(intent.amountNanm, config.storeFeeBps);
  return {
    purchaseId,
    payTo: intent.payTo,
    amountNanm: intent.amountNanm.toString(),
    memoHex: intent.memoHex,
    expiresAt: intent.expiresAt,
    split: {
      creatorNanm: (intent.amountNanm - fee).toString(),
      treasuryNanm: fee.toString(),
      feeBps: config.storeFeeBps,
    },
  };
}
