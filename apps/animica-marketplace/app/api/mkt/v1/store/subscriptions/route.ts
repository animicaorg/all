import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/store/subscriptions -> the caller's subscriptions, one row per subscription
// lineage. Renewals form a Purchase chain (parentPurchaseId); the CURRENT state of a subscription
// is always the tail of its chain (the row with no child renewal — the same `renewals: none`
// invariant the renewal worker selects on), so we list tails only.
//
// Each row carries a derived `state` (active | grace | expired | cancelled | refunded), the next
// renewal date (autoRenew tails only), the price, and the listing — enough for the wallet's
// Subscriptions screen to render status + a top-up/cancel affordance. HONESTY: renewals are
// custodial (they debit the in-app balance under signed consent) — surfaced via `custodial`.
//
// Optional `?state=active` filters to currently-usable subscriptions (active or in dunning grace).
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');

    const wantActive = req.nextUrl.searchParams.get('state') === 'active';

    const tails = await prisma.purchase.findMany({
      where: { buyerId: ctx.accountId, priceModel: 'SUBSCRIPTION', renewals: { none: {} } },
      orderBy: { createdAt: 'desc' },
      take: 200,
      include: {
        listing: { select: { slug: true, name: true, type: true } },
        price: { select: { id: true, amountNanm: true, periodDays: true, label: true, active: true } },
        license: { select: { id: true, kind: true, expiresAt: true, revokedAt: true } },
      },
    });

    const now = Date.now();
    let subscriptions = tails.map((t) => {
      const state = subState(t, now);
      const willRenew = t.status === 'ACTIVE' && t.autoRenew;
      return {
        id: t.id, // pass to /subscriptions/{id}/cancel (cancel walks the chain from any link)
        listingId: t.listingId,
        listing: t.listing,
        priceId: t.priceId,
        price: { id: t.price.id, amountNanm: t.price.amountNanm, periodDays: t.price.periodDays, label: t.price.label, active: t.price.active },
        amountNanm: t.amountNanm,
        status: t.status,
        state,
        autoRenew: t.autoRenew,
        willRenew,
        nextRenewalAt: willRenew ? t.expiresAt : null,
        expiresAt: t.expiresAt,
        activeUntil: t.expiresAt,
        graceUntil: t.graceUntil,
        source: t.source,
        consentId: t.consentId,
        parentPurchaseId: t.parentPurchaseId,
        license: t.license,
        createdAt: t.createdAt,
      };
    });

    if (wantActive) subscriptions = subscriptions.filter((s) => s.state === 'active' || s.state === 'grace');

    return ok({ subscriptions: jsonSafe(subscriptions), custodial: true });
  } catch (e) {
    return err(e);
  }
}

// Derived state for the tail of a subscription chain:
//   refunded/cancelled/expired  -> terminal statuses as recorded
//   active                      -> ACTIVE and still inside the paid period
//   grace                       -> ACTIVE, period ended, in dunning grace (a renewal debit failed;
//                                  access has lapsed — grace only keeps the renewal attempt alive)
//   expired                     -> ACTIVE but the period ended with no grace (worker will flip it)
function subState(p: {
  status: string;
  autoRenew: boolean;
  expiresAt: Date | null;
  graceUntil: Date | null;
}, now: number): 'active' | 'grace' | 'expired' | 'cancelled' | 'refunded' {
  if (p.status === 'REFUNDED') return 'refunded';
  if (p.status === 'CANCELLED') return 'cancelled';
  if (p.status === 'EXPIRED') return 'expired';
  // status === 'ACTIVE' (or PENDING_PAYMENT, which never applies to a balance subscription)
  const exp = p.expiresAt ? p.expiresAt.getTime() : null;
  if (exp !== null && exp <= now) {
    if (p.autoRenew && p.graceUntil && p.graceUntil.getTime() > now) return 'grace';
    return 'expired';
  }
  return 'active';
}
