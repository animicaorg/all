import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { cleanStr, newOrderId, orderCustomerDto } from '@/lib/hire';
import { requireCustomer } from '@/lib/hireAuth';
import { clientIp } from '@/lib/animalApi';
import { PAYPAL_CLIENT_ID, paypalEnv } from '@/lib/paypal';
import { notifyAdminNewOrder, confirmCustomerQuote } from '@/lib/hireMail';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// List the signed-in customer's orders (dashboard).
export async function GET(_req: NextRequest) {
  try {
    const cust = await requireCustomer();
    // Abandoned checkouts (draft, never paid) stay hidden, but a draft that reached PayPal —
    // i.e. one the webhook has since linked to a subscription — must be visible, otherwise a
    // customer whose browser died mid-confirm sees "No services yet" after being charged.
    const orders = await prisma.servicesOrder.findMany({
      where: {
        userId: cust.id,
        OR: [{ NOT: { paymentStatus: 'draft' } }, { paypalSubscriptionId: { not: null } }],
      },
      orderBy: { createdAt: 'desc' },
      take: 100,
    });
    return ok({ orders: orders.map(orderCustomerDto) });
  } catch (e) {
    return err(e);
  }
}

// Place a service request. Subscription services => a draft order awaiting PayPal approval
// (the client then renders PayPal buttons and calls /orders/confirm). Quote services => a
// quote_requested order, notified to the operator + confirmed to the customer immediately.
export async function POST(req: NextRequest) {
  try {
    const cust = await requireCustomer();

    let body: any = {};
    try { body = await req.json(); } catch {}
    const serviceSlug = cleanStr(body?.service, 80, 'service', true)!;
    const customerName = cleanStr(body?.fullName, 120, 'full name', true)!;
    const company = cleanStr(body?.company, 160, 'company');
    const domain = cleanStr(body?.domain, 255, 'domain');
    const discordUsername = cleanStr(body?.discord, 80, 'discord username');
    const notes = cleanStr(body?.notes, 4000, 'notes');

    const service = await prisma.hireService.findUnique({ where: { slug: serviceSlug } });
    if (!service || !service.active) throw new ApiError(400, 'invalid', 'unknown service');
    const isQuote = service.kind === 'quote';
    if (!isQuote && !service.paypalPlanId) {
      throw new ApiError(503, 'unavailable', 'checkout for this service is not configured yet — please try again soon');
    }

    // Per-customer flood guard (drafts included so abandoned checkouts count too).
    const dayAgo = new Date(Date.now() - 24 * 3600 * 1000);
    const recent = await prisma.servicesOrder.count({
      where: { userId: cust.id, createdAt: { gte: dayAgo } },
    });
    if (recent >= 20) throw new ApiError(429, 'rate_limited', 'too many requests today — please contact support');

    const order = await prisma.servicesOrder.create({
      data: {
        orderId: newOrderId(),
        userId: cust.id,
        serviceId: service.id,
        serviceType: service.slug,
        serviceName: service.name,
        monthlyPriceUsd: isQuote ? null : service.priceUsdMonthly,
        setupFeeUsd: isQuote ? 0 : service.setupFeeUsd,
        customerName,
        email: cust.email,
        company,
        domain,
        discordUsername,
        notes,
        paypalPlanId: isQuote ? null : service.paypalPlanId,
        paymentStatus: isQuote ? 'quote' : 'draft',
        deploymentStatus: isQuote ? 'quote_requested' : 'awaiting_payment',
        customerIp: clientIp(req),
        userAgent: (req.headers.get('user-agent') || '').slice(0, 500),
      },
    });

    if (isQuote) {
      // Fire-and-forget: mail failures must never fail the request.
      notifyAdminNewOrder(order, 'quote').catch(() => {});
      confirmCustomerQuote(order).catch(() => {});
      return ok({ orderId: order.orderId, quote: true });
    }

    return ok({
      orderId: order.orderId,
      quote: false,
      paypal: {
        planId: service.paypalPlanId,
        clientId: PAYPAL_CLIENT_ID,
        env: paypalEnv,
        monthlyUsd: service.priceUsdMonthly,
        setupFeeUsd: service.setupFeeUsd,
      },
    });
  } catch (e) {
    return err(e);
  }
}
