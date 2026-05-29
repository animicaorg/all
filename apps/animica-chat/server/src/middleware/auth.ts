// Auth middleware: load the session cookie, look up the user, and
// hang it off req.user. requireAuth + requireAdmin + the subscription
// gate are composed on each protected route.

import { NextFunction, Request, Response } from 'express';
import { SubscriptionStatus, UserRole } from '@prisma/client';
import { prisma } from '../prisma';
import { hashToken } from '../lib/tokens';

const COOKIE_NAME = 'animica_chat_session';

export function getSessionCookieName(): string {
  return COOKIE_NAME;
}

export async function attachUser(req: Request, _res: Response, next: NextFunction): Promise<void> {
  const raw = req.cookies?.[COOKIE_NAME];
  if (!raw) return next();
  const tokenHash = hashToken(raw);
  const session = await prisma.session.findUnique({
    where: { tokenHash },
    include: { user: true },
  });
  if (!session) return next();
  if (session.expiresAt < new Date()) {
    await prisma.session.delete({ where: { id: session.id } }).catch(() => undefined);
    return next();
  }
  if (session.user.role === UserRole.BANNED) return next();
  // Touch last_seen on a coarse interval to avoid write amplification.
  if (Date.now() - session.lastSeenAt.getTime() > 60_000) {
    await prisma.session
      .update({ where: { id: session.id }, data: { lastSeenAt: new Date() } })
      .catch(() => undefined);
  }
  req.user = session.user;
  req.session = { id: session.id, expiresAt: session.expiresAt };
  next();
}

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  if (!req.user) {
    res.status(401).json({ error: 'auth_required' });
    return;
  }
  next();
}

export function requireAdmin(req: Request, res: Response, next: NextFunction): void {
  if (!req.user || req.user.role !== UserRole.ADMIN) {
    res.status(403).json({ error: 'admin_required' });
    return;
  }
  next();
}

export async function loadSubscription(req: Request, _res: Response, next: NextFunction): Promise<void> {
  if (!req.user) return next();
  req.activeSubscription = (await prisma.userSubscription.findUnique({
    where: { userId: req.user.id },
    include: { plan: true },
  })) ?? undefined;
  next();
}

export function requireActiveSubscription(req: Request, res: Response, next: NextFunction): void {
  const sub = req.activeSubscription;
  if (!sub) {
    res.status(402).json({ error: 'subscription_required' });
    return;
  }
  if (sub.status !== SubscriptionStatus.ACTIVE) {
    res.status(402).json({ error: 'subscription_inactive', status: sub.status });
    return;
  }
  next();
}
