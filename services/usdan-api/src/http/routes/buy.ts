import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const CreateBuyIntentSchema = z.object({
  amountUsd: z.number().positive(),
  bankAccountId: z.string().uuid(),
  walletAddress: z.string().min(3).optional()
});

const MarkSettledSchema = z.object({
  settlementReference: z.string().min(3)
});

const MarkMintSchema = z.object({
  txHash: z.string().min(8)
});

export function createBuyRouter(ctx: HttpContext): Router {
  const router = Router();

  router.post('/buy/intents', async (req: RequestWithContext, res, next) => {
    try {
      const input = CreateBuyIntentSchema.parse(req.body);
      const intent = await ctx.services.purchase.createIntent({
        userId: req.user!.userId,
        walletAddress: input.walletAddress ?? req.user!.walletAddress ?? '',
        bankAccountId: input.bankAccountId,
        amountUsd: input.amountUsd
      });
      res.status(201).json({ intent });
    } catch (error) {
      next(error);
    }
  });

  router.get('/buy/intents', async (req: RequestWithContext, res, next) => {
    try {
      const intents = await ctx.store.listPurchaseIntents(req.user!.userId);
      res.json({ intents });
    } catch (error) {
      next(error);
    }
  });

  router.post('/buy/intents/:id/settled', async (req: RequestWithContext, res, next) => {
    try {
      const input = MarkSettledSchema.parse(req.body);
      const intent = await ctx.services.purchase.markFundsSettled(req.params.id, input.settlementReference);
      res.json({ intent });
    } catch (error) {
      next(error);
    }
  });

  router.post('/buy/intents/:id/mint-submitted', async (req: RequestWithContext, res, next) => {
    try {
      const input = MarkMintSchema.parse(req.body);
      const intent = await ctx.services.purchase.markMintSubmitted(req.params.id, input.txHash);
      res.json({ intent });
    } catch (error) {
      next(error);
    }
  });

  router.post('/buy/intents/:id/mint-confirmed', async (req: RequestWithContext, res, next) => {
    try {
      const input = MarkMintSchema.parse(req.body);
      const intent = await ctx.services.purchase.markMintConfirmed(req.params.id, input.txHash);
      ctx.services.chain.setSupply((Number(intent.amountUsdan) + Number((await ctx.services.chain.getUsdanTotalSupply()))).toFixed(2));
      res.json({ intent });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
