// PayPal subscription routes.
//   POST /api/paypal/create-subscription  -> {approveUrl}
//   POST /api/webhooks/paypal              -> applies state from PayPal
//   GET  /api/billing/status               -> see routes/billing.ts

import { Router, raw } from 'express';
import { z } from 'zod';
import { env } from '../env';
import { prisma } from '../prisma';
import * as paypal from '../services/paypalClient';
import { requireAuth } from '../middleware/auth';
import { webhookRateLimit } from '../middleware/rateLimit';
import { SubscriptionStatus } from '@prisma/client';

export const paypalRouter = Router();

const CreateSchema = z.object({
  planCode: z.string().default('pro'),
  returnUrl: z.string().url().optional(),
  cancelUrl: z.string().url().optional(),
});

paypalRouter.post('/create-subscription', requireAuth, async (req, res) => {
  const parsed = CreateSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const plan = await prisma.subscriptionPlan.findUnique({ where: { code: parsed.data.planCode } });
  if (!plan || !plan.paypalPlanId) {
    res.status(400).json({ error: 'plan_unavailable' });
    return;
  }

  const sub = await paypal.createSubscription({
    planId: plan.paypalPlanId,
    customId: req.user!.id,
    returnUrl:
      parsed.data.returnUrl ||
      `${env.PUBLIC_BASE_URL.replace(/\/+$/, '')}/billing?status=success`,
    cancelUrl:
      parsed.data.cancelUrl ||
      `${env.PUBLIC_BASE_URL.replace(/\/+$/, '')}/billing?status=cancelled`,
  });

  // Stage a pending UserSubscription so the webhook can resolve it.
  const userSub = await prisma.userSubscription.upsert({
    where: { userId: req.user!.id },
    create: {
      userId: req.user!.id,
      planId: plan.id,
      status: SubscriptionStatus.PENDING,
    },
    update: { planId: plan.id, status: SubscriptionStatus.PENDING },
  });
  await prisma.payPalSubscription.upsert({
    where: { userSubscriptionId: userSub.id },
    create: {
      userSubscriptionId: userSub.id,
      paypalSubscriptionId: sub.id,
      paypalStatus: sub.status,
    },
    update: {
      paypalSubscriptionId: sub.id,
      paypalStatus: sub.status,
    },
  });

  res.json({ approveUrl: sub.approveUrl, subscriptionId: sub.id });
});

// PayPal webhooks. We use express.raw() upstream so the signature check
// can hash the unmodified body; do NOT use express.json() for this path.
export const paypalWebhookRouter = Router();

paypalWebhookRouter.post(
  '/',
  webhookRateLimit,
  raw({ type: '*/*', limit: '1mb' }),
  async (req, res) => {
    const rawBody = (req.body as Buffer)?.toString('utf8') || '{}';
    let event: any;
    try {
      event = JSON.parse(rawBody);
    } catch {
      res.status(400).json({ error: 'bad_json' });
      return;
    }

    let signatureOk = true;
    if (env.PAYPAL_VERIFY_WEBHOOKS) {
      signatureOk = await paypal.verifyWebhook({
        headers: req.headers as Record<string, string | string[] | undefined>,
        rawBody,
      });
      if (!signatureOk) {
        await prisma.paymentEvent
          .create({
            data: {
              externalId: String(event.id ?? `unsigned-${Date.now()}`),
              eventType: String(event.event_type ?? 'unknown'),
              rawPayload: event,
              signatureOk: false,
            },
          })
          .catch(() => undefined);
        res.status(400).json({ error: 'bad_signature' });
        return;
      }
    }

    const externalId: string = String(event.id ?? '');
    if (!externalId) {
      res.status(400).json({ error: 'missing_event_id' });
      return;
    }

    // Idempotent: dedupe by externalId; webhook redeliveries are no-ops.
    const exists = await prisma.paymentEvent.findUnique({ where: { externalId } });
    if (exists) {
      res.json({ ok: true, dedup: true });
      return;
    }
    const paypalSubscriptionId: string | undefined = event?.resource?.id;
    const userIdFromCustom: string | undefined = event?.resource?.custom_id;

    await prisma.paymentEvent.create({
      data: {
        externalId,
        eventType: String(event.event_type || 'unknown'),
        userId: userIdFromCustom,
        paypalSubscriptionId,
        rawPayload: event,
        signatureOk,
        processedAt: new Date(),
      },
    });

    // Reconcile UserSubscription based on the event type.
    if (paypalSubscriptionId) {
      const linked = await prisma.payPalSubscription.findUnique({
        where: { paypalSubscriptionId },
        include: { userSubscription: { include: { plan: true } } },
      });
      if (linked) {
        const userSubId = linked.userSubscription.id;
        switch (event.event_type) {
          case 'BILLING.SUBSCRIPTION.ACTIVATED':
          case 'PAYMENT.SALE.COMPLETED': {
            const periodStart = new Date();
            const periodEnd = new Date(periodStart.getTime() + 31 * 24 * 60 * 60_000);
            await prisma.userSubscription.update({
              where: { id: userSubId },
              data: {
                status: SubscriptionStatus.ACTIVE,
                currentPeriodStart: periodStart,
                currentPeriodEnd: periodEnd,
                messagesUsedThisPeriod: 0,
              },
            });
            await prisma.payPalSubscription.update({
              where: { id: linked.id },
              data: { paypalStatus: 'ACTIVE' },
            });
            break;
          }
          case 'BILLING.SUBSCRIPTION.CANCELLED':
          case 'BILLING.SUBSCRIPTION.EXPIRED': {
            await prisma.userSubscription.update({
              where: { id: userSubId },
              data: {
                status: SubscriptionStatus.CANCELED,
                canceledAt: new Date(),
              },
            });
            break;
          }
          case 'BILLING.SUBSCRIPTION.SUSPENDED': {
            await prisma.userSubscription.update({
              where: { id: userSubId },
              data: { status: SubscriptionStatus.SUSPENDED },
            });
            break;
          }
          case 'PAYMENT.SALE.DENIED':
          case 'BILLING.SUBSCRIPTION.PAYMENT.FAILED': {
            await prisma.userSubscription.update({
              where: { id: userSubId },
              data: { status: SubscriptionStatus.PAST_DUE },
            });
            break;
          }
          default:
            // Unhandled — recorded for forensics but no state change.
            break;
        }
      }
    }

    res.json({ ok: true });
  },
);
