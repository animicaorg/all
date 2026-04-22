import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const LinkWalletSchema = z.object({
  walletAddress: z.string().min(3),
  chainId: z.number().int().positive(),
  message: z.string().min(4),
  signature: z.string().min(8),
  isPrimary: z.boolean().optional()
});

export function createWalletRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/wallet/links', async (req: RequestWithContext, res, next) => {
    try {
      const links = await ctx.store.getWalletLinks(req.user!.userId);
      res.json({ links });
    } catch (error) {
      next(error);
    }
  });

  router.post('/wallet/link', async (req: RequestWithContext, res, next) => {
    try {
      const input = LinkWalletSchema.parse(req.body);
      const link = await ctx.services.walletBinding.bindWallet({
        userId: req.user!.userId,
        walletAddress: input.walletAddress,
        chainId: input.chainId,
        message: input.message,
        signature: input.signature,
        isPrimary: input.isPrimary
      });
      res.status(201).json({ link });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
