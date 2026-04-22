import { Router } from 'express';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

export function createTransactionsRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/transactions', async (req: RequestWithContext, res, next) => {
    try {
      const items = await ctx.services.transactions.listUserTransactions(req.user!.userId);
      res.json({ items });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
