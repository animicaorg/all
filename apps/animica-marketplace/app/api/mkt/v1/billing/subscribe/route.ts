import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { PAYPAL_CLIENT_ID, paypalEnv, paypalConfigured } from '@/lib/paypal';
import { isPlanKey, isContactSalesPlan } from '@/lib/planConfig';
import { trackBillingEvent } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Start a checkout: create a PENDING PlanSubscription draft for the SERVER-resolved plan and
// hand the browser only what PayPal Buttons needs. The browser never chooses plan ids or
// prices — it names a plan key; everything money-related is resolved from the Plan row.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'connect your wallet to subscribe');
    requireScope(ctx, 'buy');

    let body: any = {};
    try { body = await req.json(); } catch {}
    const planKey = String(body?.planKey || '');
    if (!isPlanKey(planKey) || planKey === 'free') throw new ApiError(400, 'invalid', 'unknown plan');
    // Enterprise is contact-sales only: it has no PayPal plan by design (custom quotas,
    // custom price). Never let a checkout draft exist for it.
    if (isContactSalesPlan(planKey)) {
      throw new ApiError(400, 'contact_sales', 'Enterprise is quoted per workload — start at /cloud/pricing or POST /api/cloud/v1/enterprise');
    }
    if (!paypalConfigured()) throw new ApiError(503, 'billing_unconfigured', 'PayPal is not configured');

    const plan = await prisma.plan.findUnique({ where: { key: planKey } });
    if (!plan || !plan.active || !plan.paypalPlanId || plan.priceUsdCents <= 0) {
      throw new ApiError(503, 'billing_unconfigured', 'this plan is not ready for checkout yet');
    }

    // Flood guard (hire pattern): cap draft creation per account per day.
    const since = new Date(Date.now() - 24 * 3600 * 1000);
    const drafts = await prisma.planSubscription.count({
      where: { accountId: ctx.accountId, status: 'PENDING', createdAt: { gte: since } },
    });
    if (drafts >= 20) throw new ApiError(429, 'rate_limited', 'too many checkout attempts today');

    const row = await prisma.planSubscription.create({
      data: {
        accountId: ctx.accountId,
        planKey: plan.key,
        status: 'PENDING',
        priceUsdCents: plan.priceUsdCents,
        paypalPlanId: plan.paypalPlanId,
      },
    });
    trackBillingEvent('checkout_start', { accountId: ctx.accountId, planKey: plan.key }).catch(() => {});

    return ok({
      id: row.id, // becomes PayPal custom_id — the server-side ownership proof at confirm time
      plan: { key: plan.key, name: plan.name, priceUsdCents: plan.priceUsdCents },
      paypal: { planId: plan.paypalPlanId, clientId: PAYPAL_CLIENT_ID, env: paypalEnv },
    });
  } catch (e) {
    return err(e);
  }
}
