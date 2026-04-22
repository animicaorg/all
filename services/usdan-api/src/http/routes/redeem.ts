import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const CreateRedemptionSchema = z.object({
  amountUsdan: z.number().positive(),
  bankAccountId: z.string().uuid(),
  walletAddress: z.string().min(3).optional(),
  userIntentHash: z.string().min(8)
});

const ConfirmOnchainSchema = z.object({
  txHash: z.string().min(8)
});

const SettlePayoutSchema = z.object({
  payoutReference: z.string().min(3)
});

export function createRedeemRouter(ctx: HttpContext): Router {
  const router = Router();

  router.post('/redeem/requests', async (req: RequestWithContext, res, next) => {
    try {
      const input = CreateRedemptionSchema.parse(req.body);
      const request = await ctx.services.redemption.createRequest({
        userId: req.user!.userId,
        walletAddress: input.walletAddress ?? req.user!.walletAddress ?? '',
        bankAccountId: input.bankAccountId,
        amountUsdan: input.amountUsdan,
        userIntentHash: input.userIntentHash
      });
      res.status(201).json({ request });
    } catch (error) {
      next(error);
    }
  });

  router.get('/redeem/requests', async (req: RequestWithContext, res, next) => {
    try {
      const requests = await ctx.store.listRedemptionRequests(req.user!.userId);
      res.json({ requests });
    } catch (error) {
      next(error);
    }
  });

  router.post('/redeem/requests/:id/onchain-confirmed', async (req, res, next) => {
    try {
      const input = ConfirmOnchainSchema.parse(req.body);
      const request = await ctx.services.redemption.markOnchainConfirmed(req.params.id, input.txHash);
      res.json({ request });
    } catch (error) {
      next(error);
    }
  });

  router.post('/redeem/requests/:id/payout-settled', async (req, res, next) => {
    try {
      const input = SettlePayoutSchema.parse(req.body);
      const request = await ctx.services.redemption.markPayoutSettled(req.params.id, input.payoutReference);
      res.json({ request });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
