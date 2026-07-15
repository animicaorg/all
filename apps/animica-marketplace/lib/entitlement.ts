import { prisma } from './db';

// Does `accountId` currently have access to `listingId`? Owner always does. Otherwise an ACTIVE,
// unexpired purchase. Returns the governing price model so the caller knows whether to meter.
export async function checkEntitlement(accountId: string, listingId: string) {
  const listing = await prisma.listing.findUnique({ where: { id: listingId }, select: { ownerId: true } });
  if (listing?.ownerId === accountId) return { entitled: true, priceModel: 'OWNER' as const, purchaseId: null };

  const purchase = await prisma.purchase.findFirst({
    where: {
      buyerId: accountId,
      listingId,
      status: 'ACTIVE',
      OR: [{ expiresAt: null }, { expiresAt: { gt: new Date() } }],
    },
    orderBy: { createdAt: 'desc' },
  });
  if (!purchase) return { entitled: false, priceModel: null, purchaseId: null };
  return { entitled: true, priceModel: purchase.priceModel, purchaseId: purchase.id };
}
