import { Router } from 'express';
import { z } from 'zod';
import { prisma } from '../prisma';
import { requireAdmin, requireAuth } from '../middleware/auth';
import { SubscriptionStatus, UserRole } from '@prisma/client';

export const adminRouter = Router();

adminRouter.use(requireAuth, requireAdmin);

adminRouter.get('/users', async (_req, res) => {
  const users = await prisma.user.findMany({
    orderBy: { createdAt: 'desc' },
    take: 500,
    include: {
      subscription: { include: { plan: true } },
      _count: { select: { conversations: true } },
    },
  });
  res.json({ users });
});

const PatchUserSchema = z.object({
  role: z.nativeEnum(UserRole).optional(),
  name: z.string().nullable().optional(),
});

adminRouter.patch('/users/:id', async (req, res) => {
  const parsed = PatchUserSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const u = await prisma.user.update({ where: { id: req.params.id }, data: parsed.data });
  await prisma.adminAuditLog.create({
    data: {
      actorId: req.user!.id,
      action: 'user.update',
      targetType: 'user',
      targetId: u.id,
      metadata: parsed.data,
    },
  });
  res.json({ user: u });
});

adminRouter.get('/subscriptions', async (_req, res) => {
  const subs = await prisma.userSubscription.findMany({
    orderBy: { updatedAt: 'desc' },
    take: 500,
    include: { plan: true, paypal: true, user: { select: { email: true, role: true } } },
  });
  res.json({ subscriptions: subs });
});

const PatchSubSchema = z.object({
  status: z.nativeEnum(SubscriptionStatus).optional(),
});

adminRouter.patch('/subscriptions/:id', async (req, res) => {
  const parsed = PatchSubSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const sub = await prisma.userSubscription.update({
    where: { id: req.params.id },
    data: parsed.data,
  });
  await prisma.adminAuditLog.create({
    data: {
      actorId: req.user!.id,
      action: 'subscription.update',
      targetType: 'subscription',
      targetId: sub.id,
      metadata: parsed.data,
    },
  });
  res.json({ subscription: sub });
});

adminRouter.get('/payment-events', async (_req, res) => {
  const events = await prisma.paymentEvent.findMany({
    orderBy: { receivedAt: 'desc' },
    take: 200,
  });
  res.json({ events });
});

adminRouter.get('/usage/summary', async (_req, res) => {
  const since = new Date(Date.now() - 30 * 24 * 60 * 60_000);
  const [activeSubs, totalMrrCents, usage] = await Promise.all([
    prisma.userSubscription.count({ where: { status: SubscriptionStatus.ACTIVE } }),
    prisma.userSubscription.findMany({
      where: { status: SubscriptionStatus.ACTIVE },
      include: { plan: true },
    }).then((rows) => rows.reduce((s, r) => s + r.plan.priceUsdCents, 0)),
    prisma.usageRecord.findMany({
      where: { day: { gte: since } },
      orderBy: { day: 'asc' },
    }),
  ]);
  res.json({ activeSubs, totalMrrCents, usage });
});

const PromptSchema = z.object({
  code: z.string(),
  name: z.string(),
  prompt: z.string(),
  description: z.string().optional(),
  enabled: z.boolean().optional(),
});

adminRouter.get('/system-prompts', async (_req, res) => {
  const prompts = await prisma.systemPrompt.findMany({ orderBy: { code: 'asc' } });
  res.json({ prompts });
});

adminRouter.put('/system-prompts/:code', async (req, res) => {
  const parsed = PromptSchema.safeParse({ ...req.body, code: req.params.code });
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const row = await prisma.systemPrompt.upsert({
    where: { code: parsed.data.code },
    create: { ...parsed.data, updatedById: req.user!.id },
    update: { ...parsed.data, updatedById: req.user!.id },
  });
  await prisma.adminAuditLog.create({
    data: {
      actorId: req.user!.id,
      action: 'systemPrompt.upsert',
      targetType: 'systemPrompt',
      targetId: row.id,
      metadata: { code: parsed.data.code },
    },
  });
  res.json({ prompt: row });
});
