import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/db';
import { verifyWebhookSignature, HIRE_PAYPAL_WEBHOOK_ID, getSubscription, assertSubscriptionMoney } from '@/lib/paypal';
import { notifyAdminNewOrder, sendMailSafe } from '@/lib/hireMail';
import { HIRE_NOTIFY_EMAIL } from '@/lib/hire';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// PayPal webhook for hire subscriptions. Signature-verified via PayPal's API, FAIL CLOSED when
// HIRE_PAYPAL_WEBHOOK_ID is unset (503 => PayPal retries later, nothing is trusted meanwhile).
//
// Order resolution is deliberately strict. custom_id is chosen by whoever created the
// subscription (the buyer's browser), so an order is only ever matched through it after the
// referenced subscription has been re-fetched from PayPal and proven to belong to that order:
// right plan, right custom_id, right money. Matching by our own stored paypalSubscriptionId
// needs no such proof — we wrote it ourselves after the same checks.

// Terminal locally-decided states are never re-opened by a late/replayed event.
const TERMINAL = new Set(['cancelled']);

async function resolveOrder(resource: any) {
  const rawId = typeof resource?.id === 'string' ? resource.id : '';
  const subId: string = rawId.startsWith('I-') ? rawId : (resource?.billing_agreement_id || '');
  if (subId) {
    const bySub = await prisma.servicesOrder.findUnique({ where: { paypalSubscriptionId: subId } });
    if (bySub) return { order: bySub, subId, trusted: true as const };
  }

  const customId: string = resource?.custom_id || resource?.custom || '';
  if (!customId || !subId) return null;
  const byCustom = await prisma.servicesOrder.findUnique({ where: { orderId: customId } });
  if (!byCustom) return null;

  // Untrusted path: prove this subscription really is this order's before writing anything.
  const sub = await getSubscription(subId);
  if (!sub) return null;
  if (!byCustom.paypalPlanId || sub.plan_id !== byCustom.paypalPlanId) return null;
  if ((sub.custom_id || '') !== byCustom.orderId) return null;
  const money = assertSubscriptionMoney(sub, byCustom.monthlyPriceUsd ?? 0, byCustom.setupFeeUsd ?? 0);
  if (!money.ok) {
    console.warn(`[hire:webhook] rejecting ${byCustom.orderId}: ${money.reason}`);
    return null;
  }
  // Another order already owns this subscription id => never cross-write.
  const owner = await prisma.servicesOrder.findUnique({ where: { paypalSubscriptionId: subId } });
  if (owner && owner.id !== byCustom.id) return null;
  return { order: byCustom, subId, trusted: false as const };
}

export async function POST(req: NextRequest) {
  let event: any = null;
  try {
    event = await req.json();
  } catch {
    return NextResponse.json({ error: 'bad json' }, { status: 400 });
  }

  if (!HIRE_PAYPAL_WEBHOOK_ID) {
    return NextResponse.json({ error: 'webhook not configured' }, { status: 503 });
  }
  const sig = await verifyWebhookSignature(req.headers, event);
  if (!sig.valid) {
    console.warn('[hire:webhook] rejected delivery:', sig.error || 'signature invalid');
    return NextResponse.json({ error: 'signature verification failed' }, { status: 400 });
  }

  const type = String(event?.event_type || '');
  const resource = event?.resource ?? {};
  try {
    const resolved = await resolveOrder(resource);
    if (!resolved) {
      // Not one of our orders (or an unprovable claim) — ack so PayPal stops retrying.
      return NextResponse.json({ ok: true, ignored: true });
    }
    const { order, subId } = resolved;

    const patch: Record<string, unknown> = {};
    const wasUnpaid = order.paymentStatus === 'draft' || order.paymentStatus === 'pending';

    switch (type) {
      case 'BILLING.SUBSCRIPTION.ACTIVATED':
        // A replayed ACTIVATED must not resurrect an order the operator already cancelled.
        if (TERMINAL.has(order.paymentStatus)) break;
        patch.paymentStatus = 'active';
        if (order.deploymentStatus === 'awaiting_payment') patch.deploymentStatus = 'pending_setup';
        if (!order.paypalSubscriptionId && subId) patch.paypalSubscriptionId = subId;
        break;
      case 'BILLING.SUBSCRIPTION.SUSPENDED':
        if (TERMINAL.has(order.paymentStatus)) break;
        patch.paymentStatus = 'suspended';
        break;
      case 'BILLING.SUBSCRIPTION.CANCELLED':
      case 'BILLING.SUBSCRIPTION.EXPIRED':
        patch.paymentStatus = 'cancelled';
        break;
      case 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
        if (TERMINAL.has(order.paymentStatus)) break;
        patch.paymentStatus = 'failed';
        sendMailSafe({
          to: HIRE_NOTIFY_EMAIL,
          subject: `Hire Me: payment FAILED for ${order.orderId} (${order.serviceName})`,
          text: `PayPal reports a failed subscription payment.\n\nOrder: ${order.orderId}\nCustomer: ${order.customerName} <${order.email}>\nSubscription: ${order.paypalSubscriptionId || subId}\n\nAdmin: https://animica.dev/admin/hire`,
        }).catch(() => {});
        break;
      case 'PAYMENT.SALE.COMPLETED':
        if (TERMINAL.has(order.paymentStatus)) break;
        if (!order.paypalTransactionId && typeof resource?.id === 'string') patch.paypalTransactionId = resource.id;
        if (!order.paypalSubscriptionId && subId) patch.paypalSubscriptionId = subId;
        // A successful charge clears an earlier failure too (PayPal retries dunning).
        if (order.paymentStatus !== 'active') patch.paymentStatus = 'active';
        if (order.deploymentStatus === 'awaiting_payment') patch.deploymentStatus = 'pending_setup';
        break;
      case 'PAYMENT.SALE.REFUNDED':
      case 'PAYMENT.SALE.REVERSED':
      case 'CUSTOMER.DISPUTE.CREATED':
      case 'CUSTOMER.DISPUTE.UPDATED':
        // Money can be clawed back after provisioning — never silently keep the order "paid".
        patch.paymentStatus = 'failed';
        sendMailSafe({
          to: HIRE_NOTIFY_EMAIL,
          subject: `Hire Me: ${type} on ${order.orderId} (${order.serviceName})`,
          text: `PayPal reports ${type}.\n\nOrder: ${order.orderId}\nCustomer: ${order.customerName} <${order.email}>\nSubscription: ${order.paypalSubscriptionId || subId}\nDeployment status: ${order.deploymentStatus}\n\nReview whether the service should stay up: https://animica.dev/admin/hire`,
        }).catch(() => {});
        break;
      default:
        return NextResponse.json({ ok: true, ignored: true });
    }

    if (!Object.keys(patch).length) return NextResponse.json({ ok: true, noop: true });
    const updated = await prisma.servicesOrder.update({ where: { id: order.id }, data: patch });

    // First time we learn this order is actually paid (e.g. the browser died before /confirm).
    if (wasUnpaid && patch.paymentStatus === 'active') {
      notifyAdminNewOrder(updated, 'order').catch(() => {});
    }
    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error('[hire:webhook] processing error:', (e as Error).message);
    // 500 => PayPal retries; safe because every mutation above is idempotent.
    return NextResponse.json({ error: 'processing failed' }, { status: 500 });
  }
}
