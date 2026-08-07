import { NextRequest } from 'next/server';
import { ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { config } from '@/lib/config';
import { requireAdmin } from '@/lib/adminAuth';
import { refundStorePurchase, LedgerError } from '@/lib/ledger';
import { STORE_TYPES } from '@/lib/storeCatalog';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/store/admin/purchases/[id]/refund { reason? } -> admin refund.
// Reverses the settle split at the SAME feeBps the sale used (store types = STORE_FEE_BPS,
// AI listings = MKT_FEE_BPS), credits the buyer's ledger with the full amount (withdrawable
// on-chain), marks the purchase REFUNDED and revokes its license — all atomically in
// lib/ledger.ts refundStorePurchase. Fails closed if the creator's balance can't cover the
// give-back. Idempotent: refunding a REFUNDED purchase is a no-op success.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const admin = await requireAdmin(req);
    const body = await req.json().catch(() => ({}));
    const reason = typeof body.reason === 'string' ? body.reason.trim().slice(0, 500) : '';

    const purchase = await prisma.purchase.findUnique({
      where: { id: params.id },
      include: {
        listing: {
          select: {
            id: true, ownerId: true, type: true, forkOfId: true, forkRoyaltyBps: true,
            forkOf: { select: { ownerId: true } },
          },
        },
      },
    });
    if (!purchase) throw new ApiError(404, 'not_found', 'purchase not found');
    if (purchase.status === 'REFUNDED') {
      return ok({ refunded: true, already: true, purchaseId: purchase.id });
    }
    if (purchase.status !== 'ACTIVE') {
      throw new ApiError(409, 'bad_state', `purchase is ${purchase.status}, only ACTIVE purchases are refundable`);
    }

    const isStore = (STORE_TYPES as readonly string[]).includes(purchase.listing.type);
    const feeBps = isStore ? config.storeFeeBps : config.feeBps;
    // Fail closed: a store refund must reverse at the configured store split, never a guess.
    if (!Number.isInteger(feeBps) || feeBps < 0 || feeBps >= 10_000) {
      throw new ApiError(503, 'store_unconfigured', 'STORE_FEE_BPS is not configured');
    }

    try {
      const r = await refundStorePurchase({
        purchaseId: purchase.id,
        buyerId: purchase.buyerId,
        creatorId: purchase.listing.ownerId,
        amountNanm: purchase.amountNanm,
        feeBps,
        forkParentCreatorId: purchase.listing.forkOf?.ownerId,
        forkRoyaltyBps: purchase.listing.forkOfId ? purchase.listing.forkRoyaltyBps : undefined,
        memo: reason ? `store refund: ${reason}` : 'store refund',
      });
      return ok(jsonSafe({
        refunded: true,
        purchaseId: purchase.id,
        amountNanm: purchase.amountNanm,
        feeBps,
        reversal: r,
        licensesRevoked: r.licensesRevoked,
        by: admin.via === 'token' ? 'admin-token' : admin.accountId,
      }));
    } catch (e) {
      if (e instanceof LedgerError && e.code === 'INSUFFICIENT_FUNDS') {
        throw new ApiError(409, 'insufficient_reversal_funds', 'creator/treasury balance cannot cover the reversal — resolve out of band');
      }
      if (e instanceof LedgerError && e.code === 'NOT_REFUNDABLE') {
        throw new ApiError(409, 'bad_state', 'purchase is no longer ACTIVE');
      }
      throw e;
    }
  } catch (e) {
    return err(e);
  }
}
