import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const CreateTicketSchema = z.object({
  subject: z.string().min(4),
  message: z.string().min(4),
  priority: z.enum(['LOW', 'MEDIUM', 'HIGH', 'URGENT']).default('MEDIUM')
});

export function createSupportRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/support/tickets', async (req: RequestWithContext, res, next) => {
    try {
      const tickets = await ctx.services.support.listTickets(req.user!.userId);
      res.json({ tickets });
    } catch (error) {
      next(error);
    }
  });

  router.post('/support/tickets', async (req: RequestWithContext, res, next) => {
    try {
      const input = CreateTicketSchema.parse(req.body);
      const ticket = await ctx.services.support.createTicket({
        userId: req.user!.userId,
        subject: input.subject,
        message: input.message,
        priority: input.priority
      });
      res.status(201).json({ ticket });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
