import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';
import { isPlanKey, isContactSalesPlan, planRank } from '@/lib/planConfig';
import { getAccountPlan } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Decide how to move this account to `planKey`:
//  - 'revise'    — there is a live PayPal subscription: the browser runs the Buttons revise
//                  flow (actions.subscription.revise) and then hits /billing/confirm, which
//                  re-verifies plan + money server-side.
//  - 'subscribe' — no live PayPal subscription: normal /billing/subscribe checkout.
//  - 'cancel'    — target is free: client calls /billing/cancel.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');
    requireScope(ctx, 'buy');

    let body: any = {};
    try { body = await req.json(); } catch {}
    const target = String(body?.planKey || '');
    if (!isPlanKey(target)) throw new ApiError(400, 'invalid', 'unknown plan');
    if (target === 'free') return ok({ mode: 'cancel' });
    // Enterprise never self-serves: no PayPal plan exists for it — the client routes the
    // user to the enterprise inquiry form instead of a Buttons flow.
    if (isContactSalesPlan(target)) return ok({ mode: 'contact', contactUrl: '/cloud/pricing#enterprise' });

    const current = await getAccountPlan(ctx.accountId);
    if (current.subscribedKey === target && current.state === 'ACTIVE') {
      throw new ApiError(409, 'conflict', 'already on this plan');
    }

    const plan = await prisma.plan.findUnique({ where: { key: target } });
    if (!plan || !plan.active || !plan.paypalPlanId) {
      throw new ApiError(503, 'billing_unconfigured', 'this plan is not ready for checkout yet');
    }

    const sub = current.subscription;
    const isUpgrade = planRank(target) > planRank(current.subscribedKey);
    const revisable =
      !!sub?.paypalSubscriptionId && ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD'].includes(String(sub.status));

    // UPGRADES NEVER GO THROUGH REVISE. PayPal subscription revisions do not prorate and do
    // not charge: the new price is first collected at the existing next_billing_time. Revising
    // starter -> business would therefore hand over business limits for a cycle already paid at
    // $9.99 (and cancelling right after would lock that in until currentPeriodEnd). A fresh
    // subscription charges the new plan's first cycle immediately; /billing/confirm supersedes
    // and cancels the old subscription once the new one is ACTIVE.
    if (revisable && !isUpgrade) {
      return ok({
        mode: 'revise',
        subscriptionId: sub!.paypalSubscriptionId,
        direction: 'downgrade',
        plan: { key: plan.key, name: plan.name, priceUsdCents: plan.priceUsdCents },
        paypal: { planId: plan.paypalPlanId, clientId: PAYPAL_CLIENT_ID, env: paypalEnv },
      });
    }
    return ok({ mode: 'subscribe', direction: isUpgrade ? 'upgrade' : 'downgrade' });
  } catch (e) {
    return err(e);
  }
}
