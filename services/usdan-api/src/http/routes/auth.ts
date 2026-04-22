import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';

const CreateSessionSchema = z.object({
  userId: z.string().min(3),
  email: z.string().email().optional(),
  walletAddress: z.string().min(3),
  chainId: z.number().int().positive(),
  message: z.string().min(4),
  signature: z.string().min(8)
});

export function createAuthRouter(ctx: HttpContext): Router {
  const router = Router();

  router.post('/auth/wallet/session', async (req, res, next) => {
    try {
      const input = CreateSessionSchema.parse(req.body);

      const existing = await ctx.store.getUserById(input.userId);
      if (!existing) {
        await ctx.store.createUser({
          id: input.userId,
          email: input.email,
          role: 'USER'
        });
      }

      await ctx.services.walletBinding.bindWallet({
        userId: input.userId,
        walletAddress: input.walletAddress,
        chainId: input.chainId,
        message: input.message,
        signature: input.signature,
        isPrimary: true
      });

      const token = ctx.services.walletBinding.issueSessionToken(input.userId, input.walletAddress);
      res.json({ token, userId: input.userId, walletAddress: input.walletAddress });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
