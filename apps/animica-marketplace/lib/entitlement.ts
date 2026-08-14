import { prisma } from './db';

// The single ACTIVE, unexpired purchase gate shared by every entitlement check (download AND
// play). Kept separate so the owner short-circuit and the FREE short-circuit stay at the call
// site while the Purchase lookup — and its refund/expiry semantics — live in exactly one place.
async function activePurchase(accountId: string, listingId: string) {
  return prisma.purchase.findFirst({
    where: {
      buyerId: accountId,
      listingId,
      status: 'ACTIVE',
      OR: [{ expiresAt: null }, { expiresAt: { gt: new Date() } }],
    },
    orderBy: { createdAt: 'desc' },
  });
}

// Does `accountId` currently have access to `listingId`? Owner always does. Otherwise an ACTIVE,
// unexpired purchase. Returns the governing price model so the caller knows whether to meter.
// (Used by the download-token/download pair and the AI/review/media gates — signature unchanged.)
export async function checkEntitlement(accountId: string, listingId: string) {
  const listing = await prisma.listing.findUnique({ where: { id: listingId }, select: { ownerId: true } });
  if (listing?.ownerId === accountId) return { entitled: true, priceModel: 'OWNER' as const, purchaseId: null };

  const purchase = await activePurchase(accountId, listingId);
  if (!purchase) return { entitled: false, priceModel: null, purchaseId: null };
  return { entitled: true, priceModel: purchase.priceModel, purchaseId: purchase.id };
}

// A listing is free to PLAY when it has no active paid price — a FREE-only price set (or none)
// means anyone may load the bundle; a ONE_TIME / SUBSCRIPTION price means the play surface is
// license-gated. (Mirrors the intent path, which treats only ONE_TIME/SUBSCRIPTION as payable.)
export function isFreeToPlay(prices: { model: string }[]): boolean {
  return !prices.some((p) => p.model === 'ONE_TIME' || p.model === 'SUBSCRIPTION');
}

// FREE-aware entitlement for license-gated web-game PLAY: FREE listing OR owner OR an ACTIVE,
// unexpired purchase. Same Purchase gate as checkEntitlement (via activePurchase) — the only
// addition is the FREE short-circuit, so free games play without a purchase while paid games
// stay gated. Pass the listing with its ACTIVE prices so one query covers the free check + gate.
export async function checkPlayEntitlement(
  accountId: string,
  listing: { id: string; ownerId: string; prices: { model: string }[] },
) {
  if (listing.ownerId === accountId) return { entitled: true, priceModel: 'OWNER' as const, purchaseId: null };
  if (isFreeToPlay(listing.prices)) return { entitled: true, priceModel: 'FREE' as const, purchaseId: null };

  const purchase = await activePurchase(accountId, listing.id);
  if (!purchase) return { entitled: false, priceModel: null, purchaseId: null };
  return { entitled: true, priceModel: purchase.priceModel, purchaseId: purchase.id };
}
