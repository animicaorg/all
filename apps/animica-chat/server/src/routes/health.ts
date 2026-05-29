import { Router } from 'express';
import { prisma } from '../prisma';

export const healthRouter = Router();

healthRouter.get('/', async (_req, res) => {
  try {
    await prisma.$queryRaw`SELECT 1`;
    res.json({ status: 'ok', db: 'ok', uptime: process.uptime() });
  } catch (err) {
    res.status(503).json({ status: 'degraded', db: 'error', error: String(err) });
  }
});
