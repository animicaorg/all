import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const UpsertBankSchema = z.object({
  bankAccountHash: z.string().min(8),
  status: z.enum(['PENDING_VERIFICATION', 'VERIFIED', 'REJECTED', 'DISABLED']).default('PENDING_VERIFICATION')
});

export function createKycRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/kyc/status', async (req: RequestWithContext, res, next) => {
    try {
      const status = await ctx.services.kyc.getStatus(req.user!.userId);
      const bankAccounts = await ctx.store.listBankAccounts(req.user!.userId);
      res.json({ status, bankAccounts });
    } catch (error) {
      next(error);
    }
  });

  router.post('/kyc/bank-accounts', async (req: RequestWithContext, res, next) => {
    try {
      const input = UpsertBankSchema.parse(req.body);
      const bank = await ctx.store.upsertBankAccount({
        userId: req.user!.userId,
        bankAccountHash: input.bankAccountHash,
        status: input.status
      });
      res.status(201).json({ bankAccount: bank });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
