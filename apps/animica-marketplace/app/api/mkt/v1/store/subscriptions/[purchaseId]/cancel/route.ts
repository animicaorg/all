import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/subscriptions/[purchaseId]/cancel -> stop auto-renewal. Access
// stays until the paid period's expiresAt (no refund; that's the admin refund route). The
// renewal worker creates a NEW Purchase per period (parentPurchaseId chain), so we walk to
// the NEWEST purchase in the chain before flipping autoRenew — cancelling via any historic
// renewal id still cancels the live subscription. Idempotent.
export async function POST(req: NextRequest, { params }: { params: { purchaseId: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');

    const root = await prisma.purchase.findUnique({ where: { id: params.purchaseId } });
    // 404 (not 403) for foreign purchases — don't leak row existence.
    if (!root || root.buyerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'purchase not found');
    if (root.priceModel !== 'SUBSCRIPTION') throw new ApiError(400, 'not_subscription', 'not a subscription purchase');

    // Walk forward to the newest link of the renewal chain (bounded — chains are periodic).
    let purchase: typeof root = root;
    for (let hops = 0; hops < 200; hops++) {
      const next: typeof root | null = await prisma.purchase.findFirst({
        where: { parentPurchaseId: purchase.id, buyerId: ctx.accountId },
        orderBy: { createdAt: 'desc' },
      });
      if (!next) break;
      purchase = next;
    }

    if (!purchase.autoRenew) {
      return ok({
        cancelled: false, alreadyCancelled: true,
        purchase: jsonSafe({ id: purchase.id, status: purchase.status, autoRenew: false, expiresAt: purchase.expiresAt }),
        activeUntil: purchase.expiresAt,
      });
    }

    const updated = await prisma.purchase.update({
      where: { id: purchase.id },
      data: { autoRenew: false, graceUntil: null },
    });
    return ok({
      cancelled: true,
      purchase: jsonSafe({ id: updated.id, status: updated.status, autoRenew: updated.autoRenew, expiresAt: updated.expiresAt }),
      activeUntil: updated.expiresAt,
    });
  } catch (e) {
    return err(e);
  }
}
