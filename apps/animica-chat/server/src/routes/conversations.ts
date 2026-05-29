import { Router } from 'express';
import { z } from 'zod';
import { prisma } from '../prisma';
import { requireAuth } from '../middleware/auth';

export const conversationsRouter = Router();

conversationsRouter.use(requireAuth);

conversationsRouter.get('/', async (req, res) => {
  const rows = await prisma.conversation.findMany({
    where: { userId: req.user!.id, archived: false },
    orderBy: { updatedAt: 'desc' },
    take: 200,
    select: {
      id: true,
      title: true,
      assistantMode: true,
      updatedAt: true,
      createdAt: true,
    },
  });
  res.json({ conversations: rows });
});

conversationsRouter.get('/:id', async (req, res) => {
  const conv = await prisma.conversation.findFirst({
    where: { id: req.params.id, userId: req.user!.id },
    include: {
      messages: {
        orderBy: { createdAt: 'asc' },
        include: { toolCalls: { orderBy: { startedAt: 'asc' } } },
      },
    },
  });
  if (!conv) {
    res.status(404).json({ error: 'not_found' });
    return;
  }
  res.json({ conversation: conv });
});

const PatchSchema = z.object({
  title: z.string().max(120).optional(),
  archived: z.boolean().optional(),
  assistantMode: z.string().optional(),
});

conversationsRouter.patch('/:id', async (req, res) => {
  const parsed = PatchSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const conv = await prisma.conversation.findFirst({
    where: { id: req.params.id, userId: req.user!.id },
  });
  if (!conv) {
    res.status(404).json({ error: 'not_found' });
    return;
  }
  const updated = await prisma.conversation.update({
    where: { id: conv.id },
    data: parsed.data,
  });
  res.json({ conversation: updated });
});

conversationsRouter.delete('/:id', async (req, res) => {
  const conv = await prisma.conversation.findFirst({
    where: { id: req.params.id, userId: req.user!.id },
  });
  if (!conv) {
    res.status(404).json({ error: 'not_found' });
    return;
  }
  await prisma.conversation.delete({ where: { id: conv.id } });
  res.json({ ok: true });
});
