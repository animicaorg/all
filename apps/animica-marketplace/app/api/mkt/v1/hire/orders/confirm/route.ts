import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr } from '@/lib/hire';
import { requireCustomer } from '@/lib/hireAuth';
import { getSubscription, subscriptionTransactions, assertSubscriptionMoney } from '@/lib/paypal';
import { notifyAdminNewOrder, confirmCustomerOrder } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Called after PayPal onApprove. NEVER trusts the client: the subscription is fetched from
// PayPal's API and must (a) exist, (b) reference the exact plan the order was created for,
// (c) carry our order id as custom_id, (d) charge the exact amounts we quoted (PayPal lets the
// creator override a plan's prices inline — assertSubscriptionMoney rejects that), and (e) be
// ACTIVE (or APPROVED, i.e. buyer-authorised but not yet charged). A merely-created
// APPROVAL_PENDING subscription means the buyer never approved anything and is rejected.
// Only an ACTIVE subscription queues operator work; APPROVED waits for the webhook.
export async function POST(req: NextRequest) {
  try {
    const cust = await requireCustomer();
    let body: any = {};
    try { body = await req.json(); } catch {}
    const orderId = cleanStr(body?.orderId, 40, 'orderId', true)!;
    const subscriptionId = cleanStr(body?.subscriptionId, 60, 'subscriptionId', true)!;

    const order = await prisma.servicesOrder.findUnique({ where: { orderId } });
    if (!order || order.userId !== cust.id) throw new ApiError(404, 'not_found', 'order not found');

    // Idempotent replay, and the webhook-won-the-race case: the order is already linked to this
    // very subscription, so report its current state instead of 409-ing a real payment.
    if (order.paypalSubscriptionId === subscriptionId) {
      return ok({ orderId: order.orderId, paymentStatus: order.paymentStatus, deploymentStatus: order.deploymentStatus });
    }
    if (order.paymentStatus !== 'draft' && order.paymentStatus !== 'pending') {
      throw new ApiError(409, 'conflict', 'order is not awaiting payment');
    }

    const sub = await getSubscription(subscriptionId);
    if (!sub) throw new ApiError(400, 'invalid', 'subscription not found at PayPal');
    if (!order.paypalPlanId || sub.plan_id !== order.paypalPlanId) {
      throw new ApiError(400, 'invalid', 'subscription plan mismatch');
    }
    if ((sub.custom_id || '') !== order.orderId) {
      throw new ApiError(400, 'invalid', 'subscription is not linked to this order');
    }
    // Money must match the quote exactly (blocks inline plan overrides / currency swaps).
    const money = assertSubscriptionMoney(sub, order.monthlyPriceUsd ?? 0, order.setupFeeUsd ?? 0);
    if (!money.ok) throw new ApiError(400, 'invalid', `subscription pricing mismatch: ${money.reason}`);

    const status = String(sub.status || '').toUpperCase();
    const active = status === 'ACTIVE';
    const approved = status === 'APPROVED';
    if (!active && !approved) throw new ApiError(400, 'invalid', `subscription is ${status}`);

    // Grab the first transaction id if it has landed already (best effort, display only).
    let txnId: string | null = null;
    try {
      const txns = await subscriptionTransactions(subscriptionId, Date.now() - 24 * 3600 * 1000);
      txnId = txns.find((t: any) => t?.id)?.id ?? null;
    } catch {}

    let updated;
    try {
      updated = await prisma.servicesOrder.update({
        where: { orderId },
        data: {
          paypalSubscriptionId: subscriptionId,
          paypalOrderId: cleanStr(body?.paypalOrderId, 60, 'paypalOrderId') ?? order.paypalOrderId,
          paypalTransactionId: txnId ?? order.paypalTransactionId,
          paymentStatus: active ? 'active' : 'pending',
          // Only a live subscription enters the operator work queue. APPROVED stays
          // awaiting_payment until BILLING.SUBSCRIPTION.ACTIVATED / PAYMENT.SALE.COMPLETED.
          deploymentStatus: active ? 'pending_setup' : 'awaiting_payment',
        },
      });
    } catch (e: any) {
      if (e?.code === 'P2002') throw new ApiError(409, 'conflict', 'this subscription is already linked to another order');
      throw e;
    }

    // Notifications are fire-and-forget — payment confirmation must not depend on SMTP.
    // The operator is only told about money that actually moved.
    if (active) notifyAdminNewOrder(updated, 'order').catch(() => {});
    confirmCustomerOrder(updated).catch(() => {});

    return ok({ orderId: updated.orderId, paymentStatus: updated.paymentStatus, deploymentStatus: updated.deploymentStatus });
  } catch (e) {
    return err(e);
  }
}
