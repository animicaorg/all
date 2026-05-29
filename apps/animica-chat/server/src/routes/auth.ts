// Email + magic-link auth. The flow:
//   1. POST /api/auth/request-link  -> issue token, email it
//   2. GET  /api/auth/verify?token  -> consume token, create session
//   3. POST /api/auth/logout        -> revoke session
//   4. GET  /api/auth/me            -> session-aware user lookup

import { Router } from 'express';
import { z } from 'zod';
import { adminEmails, env, isProd } from '../env';
import { prisma } from '../prisma';
import { hashIp, hashToken, newOpaqueToken } from '../lib/tokens';
import { sendMagicLinkEmail } from '../services/mailer';
import { authRateLimit } from '../middleware/rateLimit';
import { getSessionCookieName, requireAuth } from '../middleware/auth';
import { UserRole } from '@prisma/client';

export const authRouter = Router();

const RequestLinkSchema = z.object({
  email: z.string().email(),
  // Optional return path (where to bounce after successful login).
  redirectTo: z.string().regex(/^\/[^\s]*$/).optional(),
});

authRouter.post('/request-link', authRateLimit, async (req, res) => {
  const parsed = RequestLinkSchema.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: 'bad_request', issues: parsed.error.issues });
    return;
  }
  const email = parsed.data.email.toLowerCase();
  const token = newOpaqueToken();
  const tokenHash = hashToken(token);
  const expiresAt = new Date(Date.now() + 15 * 60_000); // 15 min
  const user = await prisma.user.upsert({
    where: { email },
    create: { email, role: adminEmails.has(email) ? UserRole.ADMIN : UserRole.USER },
    update: {},
  });
  await prisma.magicLink.create({
    data: { tokenHash, email, userId: user.id, expiresAt },
  });
  const url = new URL('/api/auth/verify', env.PUBLIC_BASE_URL);
  url.searchParams.set('token', token);
  if (parsed.data.redirectTo) url.searchParams.set('redirectTo', parsed.data.redirectTo);
  try {
    await sendMagicLinkEmail({ to: email, link: url.toString() });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('mailer failed', err);
  }
  // Always return 200 to avoid email-enumeration leaks.
  res.json({ ok: true });
});

authRouter.get('/verify', async (req, res) => {
  const token = String(req.query.token || '');
  const redirectTo = String(req.query.redirectTo || '/');
  if (!token) {
    res.status(400).send('missing token');
    return;
  }
  const tokenHash = hashToken(token);
  const magic = await prisma.magicLink.findUnique({ where: { tokenHash } });
  if (!magic || magic.consumedAt || magic.expiresAt < new Date()) {
    res.status(400).send('link expired — request a new one');
    return;
  }
  await prisma.magicLink.update({ where: { id: magic.id }, data: { consumedAt: new Date() } });
  const userId = magic.userId;
  if (!userId) {
    res.status(400).send('invalid link');
    return;
  }
  const sessionToken = newOpaqueToken();
  const session = await prisma.session.create({
    data: {
      userId,
      tokenHash: hashToken(sessionToken),
      expiresAt: new Date(Date.now() + 30 * 24 * 60 * 60_000),
      userAgent: req.get('user-agent')?.slice(0, 500),
      ipHash: hashIp(req.ip),
    },
  });
  res.cookie(getSessionCookieName(), sessionToken, {
    httpOnly: true,
    secure: isProd,
    sameSite: 'lax',
    domain: env.COOKIE_DOMAIN || undefined,
    expires: session.expiresAt,
    path: '/',
  });
  // Allow only same-origin returns to avoid open-redirect.
  const safe = redirectTo.startsWith('/') ? redirectTo : '/';
  res.redirect(safe);
});

authRouter.post('/logout', async (req, res) => {
  const raw = req.cookies?.[getSessionCookieName()];
  if (raw) {
    await prisma.session.deleteMany({ where: { tokenHash: hashToken(raw) } }).catch(() => undefined);
  }
  res.clearCookie(getSessionCookieName(), {
    domain: env.COOKIE_DOMAIN || undefined,
    path: '/',
  });
  res.json({ ok: true });
});

authRouter.get('/me', requireAuth, async (req, res) => {
  const u = req.user!;
  const sub = await prisma.userSubscription.findUnique({
    where: { userId: u.id },
    include: { plan: true },
  });
  res.json({
    user: { id: u.id, email: u.email, name: u.name, role: u.role },
    subscription: sub
      ? {
          status: sub.status,
          planCode: sub.plan.code,
          currentPeriodEnd: sub.currentPeriodEnd,
          messagesUsedThisPeriod: sub.messagesUsedThisPeriod,
          weeklyMessages: sub.plan.weeklyMessages,
          currentWeekStart: sub.currentWeekStart,
        }
      : null,
  });
});
