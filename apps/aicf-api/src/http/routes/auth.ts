import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createAuthRouter(service: PlatformService) {
  const router = Router();

  const signupSchema = z.object({
    email: z.string().email(),
    password: z.string().min(8),
    role: z.enum(['developer', 'provider']).optional()
  });

  const loginSchema = z.object({
    email: z.string().email(),
    password: z.string().min(8)
  });

  router.post('/signup', (req, res) => {
    try {
      const body = signupSchema.parse(req.body ?? {});
      const result = service.signup(body);
      res.status(201).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/login', (req, res) => {
    try {
      const body = loginSchema.parse(req.body ?? {});
      const result = service.login(body);
      res.status(200).json(result);
    } catch (error) {
      res.status(401).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/oauth/:provider/start', (req, res) => {
    try {
      const provider = String(req.params.provider ?? '').trim();
      if (!provider) {
        throw new Error('Missing OAuth provider');
      }
      const payload = service.oauthStart(provider);
      res.status(200).json(payload);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/oauth/:provider/callback', (req, res) => {
    try {
      const provider = String(req.params.provider ?? '').trim();
      const body = z
        .object({
          email: z.string().email(),
          oauthSubject: z.string().min(3)
        })
        .parse(req.body ?? {});
      const payload = service.oauthCallback({
        provider,
        email: body.email,
        oauthSubject: body.oauthSubject
      });
      res.status(200).json(payload);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/me', sessionAuth(service), (req, res) => {
    if (!req.ctx.user) {
      res.status(401).json({ error: { message: 'No authenticated user' } });
      return;
    }
    res.status(200).json({
      user: {
        id: req.ctx.user.id,
        email: req.ctx.user.email,
        role: req.ctx.user.role,
        wallet: req.ctx.user.wallet
      }
    });
  });

  return router;
}
