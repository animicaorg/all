import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { getSubscription, cancelSubscription } from '@/lib/paypal';
import { assertSubscriptionMoneyCents, planRank, CURRENT_STATES } from '@/lib/planConfig';
import { trackBillingEvent } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Called after PayPal onApprove — for BOTH fresh subscribes ({id, subscriptionId}) and plan
// revisions ({subscriptionId} only). NEVER trusts the client (hire pattern): the subscription
// is re-fetched from PayPal and must (a) exist, (b) carry our row id as custom_id, (c) point at
// a plan WE minted, (d) charge exactly that plan's price (assertSubscriptionMoneyCents rejects
// PayPal's inline plan-override), (e) be ACTIVE or APPROVED. paypalSubscriptionId is @unique:
// one PayPal subscription can never activate two rows (P2002 => 409).
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');
    requireScope(ctx, 'buy');

    let body: any = {};
    try { body = await req.json(); } catch {}
    const subscriptionId = String(body?.subscriptionId || '').slice(0, 60);
    const draftId = body?.id ? String(body.id).slice(0, 40) : null;
    if (!subscriptionId) throw new ApiError(400, 'invalid', 'subscriptionId required');

    // Resolve our row: by draft id (fresh subscribe) or by the linked PayPal sub (revise).
    let row = draftId
      ? await prisma.planSubscription.findUnique({ where: { id: draftId } })
      : await prisma.planSubscription.findUnique({ where: { paypalSubscriptionId: subscriptionId } });
    if (!row || row.accountId !== ctx.accountId) throw new ApiError(404, 'not_found', 'subscription not found');
    const prevKey = row.planKey;
    const isRevise = !draftId;

    const sub = await getSubscription(subscriptionId);
    if (!sub) throw new ApiError(400, 'invalid', 'subscription not found at PayPal');
    if ((sub.custom_id || '') !== row.id) {
      throw new ApiError(400, 'invalid', 'subscription is not linked to this checkout');
    }

    // The effective plan is whatever PayPal says the sub is on — but it must be a plan WE
    // minted (active catalog row). Fresh subscribes must land on the exact draft plan.
    const planRow = await prisma.plan.findFirst({
      where: { paypalPlanId: String(sub.plan_id || ''), active: true },
    });
    if (!planRow) throw new ApiError(400, 'invalid', 'subscription plan is not an Animica plan');
    if (!isRevise && planRow.paypalPlanId !== row.paypalPlanId) {
      throw new ApiError(400, 'invalid', 'subscription plan mismatch');
    }
    const money = assertSubscriptionMoneyCents(sub, planRow.priceUsdCents);
    if (!money.ok) throw new ApiError(400, 'invalid', `subscription pricing mismatch: ${money.reason}`);

    const status = String(sub.status || '').toUpperCase();
    const active = status === 'ACTIVE';
    const approved = status === 'APPROVED';
    if (!active && !approved) throw new ApiError(400, 'invalid', `subscription is ${status}`);

    // Idempotent replay: already linked + already reflecting this plan => report state.
    if (row.paypalSubscriptionId === subscriptionId && row.planKey === planRow.key && row.status !== 'PENDING') {
      return ok({ id: row.id, planKey: row.planKey, status: row.status });
    }

    // A confirm call may only resume a row that is still in play. CANCELED is terminal (the
    // webhook's rule too) and SUSPENDED is a clawback/operator hold — neither may be flipped
    // back to ACTIVE by a browser-supplied approval; only a real charge
    // (PAYMENT.SALE.COMPLETED) or an admin reactivate clears those.
    const RESUMABLE = new Set(['PENDING', 'ACTIVE', 'PAST_DUE', 'GRACE_PERIOD']);
    if (!RESUMABLE.has(row.status)) {
      throw new ApiError(409, 'conflict', `subscription is ${row.status.toLowerCase()} — contact support`);
    }

    // Defense in depth for the no-proration rule enforced in /billing/change: never ADOPT a
    // higher-ranked plan on an existing subscription unless a payment at that plan's price has
    // actually landed. Upgrades are routed through a fresh subscription (which charges up
    // front); a revise that raises the tier without a charge is refused here as well.
    if (isRevise && planRank(planRow.key) > planRank(prevKey)) {
      const paidCents = Math.round(Number(sub?.billing_info?.last_payment?.amount?.value ?? 0) * 100);
      if (!Number.isFinite(paidCents) || paidCents < planRow.priceUsdCents) {
        throw new ApiError(
          409,
          'conflict',
          'upgrades are charged up front — start the upgrade from /pricing so the new plan is billed immediately',
        );
      }
    }

    const nextBilling = sub?.billing_info?.next_billing_time
      ? new Date(sub.billing_info.next_billing_time)
      : null;
    const payerEmail =
      typeof sub?.subscriber?.email_address === 'string' ? sub.subscriber.email_address.slice(0, 200) : null;

    let updated;
    try {
      updated = await prisma.planSubscription.update({
        where: { id: row.id },
        data: {
          paypalSubscriptionId: subscriptionId,
          planKey: planRow.key,
          priceUsdCents: planRow.priceUsdCents,
          paypalPlanId: planRow.paypalPlanId,
          // APPROVED = buyer authorized but first charge pending: stays PENDING (no
          // entitlements) until the webhook's ACTIVATED/SALE.COMPLETED lands.
          status: active ? 'ACTIVE' : 'PENDING',
          payerEmail: payerEmail ?? row.payerEmail,
          currentPeriodEnd: nextBilling ?? row.currentPeriodEnd,
          graceUntil: active ? null : row.graceUntil,
          lastError: null,
        },
      });
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'conflict', 'this PayPal subscription is already linked to another account');
      throw e;
    }

    // Supersede any OTHER live rows for this account (old subscription from a previous
    // upgrade path): cancel at PayPal (soft-fail) and mark CANCELED locally.
    if (active) {
      const others = await prisma.planSubscription.findMany({
        where: { accountId: ctx.accountId, id: { not: updated.id }, status: { in: CURRENT_STATES as any } },
      });
      for (const o of others) {
        if (o.paypalSubscriptionId && o.paypalSubscriptionId !== subscriptionId) {
          await cancelSubscription(o.paypalSubscriptionId, 'Superseded by a new Animica plan').catch(() => {});
        }
        await prisma.planSubscription.update({
          where: { id: o.id },
          data: { status: 'CANCELED', canceledAt: new Date(), lastError: 'superseded by new subscription' },
        });
      }
    }

    if (active) {
      if (isRevise && prevKey !== planRow.key) {
        const kind = planRank(planRow.key) > planRank(prevKey) ? 'upgrade' : 'downgrade';
        trackBillingEvent(kind, { accountId: ctx.accountId, planKey: planRow.key, fromPlanKey: prevKey }).catch(() => {});
      } else {
        trackBillingEvent('checkout_success', { accountId: ctx.accountId, planKey: planRow.key }).catch(() => {});
      }
    }

    return ok({ id: updated.id, planKey: updated.planKey, status: updated.status });
  } catch (e) {
    return err(e);
  }
}
