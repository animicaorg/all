import { Router } from 'express';
import { prisma } from '../prisma';
import { requireAuth } from '../middleware/auth';
import * as paypal from '../services/paypalClient';
import { SubscriptionStatus } from '@prisma/client';

export const billingRouter = Router();

billingRouter.get('/status', requireAuth, async (req, res) => {
  const sub = await prisma.userSubscription.findUnique({
    where: { userId: req.user!.id },
    include: { plan: true, paypal: true },
  });
  if (!sub) {
    res.json({ subscription: null });
    return;
  }
  res.json({
    subscription: {
      status: sub.status,
      planCode: sub.plan.code,
      planName: sub.plan.name,
      priceUsdCents: sub.plan.priceUsdCents,
      currentPeriodStart: sub.currentPeriodStart,
      currentPeriodEnd: sub.currentPeriodEnd,
      cancelAt: sub.cancelAt,
      canceledAt: sub.canceledAt,
      messagesUsedThisPeriod: sub.messagesUsedThisPeriod,
      weeklyMessages: sub.plan.weeklyMessages,
      currentWeekStart: sub.currentWeekStart,
      paypalStatus: sub.paypal?.paypalStatus,
      paypalSubscriptionId: sub.paypal?.paypalSubscriptionId,
    },
  });
});

billingRouter.post('/cancel', requireAuth, async (req, res) => {
  const sub = await prisma.userSubscription.findUnique({
    where: { userId: req.user!.id },
    include: { paypal: true },
  });
  if (!sub || !sub.paypal) {
    res.status(404).json({ error: 'no_subscription' });
    return;
  }
  try {
    await paypal.cancelSubscription(sub.paypal.paypalSubscriptionId, 'user requested via app');
  } catch (err) {
    res.status(502).json({ error: 'paypal_cancel_failed', message: String(err) });
    return;
  }
  await prisma.userSubscription.update({
    where: { id: sub.id },
    data: { status: SubscriptionStatus.CANCELED, canceledAt: new Date() },
  });
  res.json({ ok: true });
});

billingRouter.get('/plans', async (_req, res) => {
  const plans = await prisma.subscriptionPlan.findMany({
    where: { active: true },
    orderBy: { priceUsdCents: 'asc' },
  });
  res.json({ plans });
});
