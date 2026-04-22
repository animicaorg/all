import type { Request } from 'express';

export interface AuthenticatedUser {
  userId: string;
  walletAddress?: string;
  scope: 'user';
}

export interface AdminPrincipal {
  actorId: string;
  scope: 'admin';
}

export type RequestWithContext = Request & {
  requestId?: string;
  idempotencyKey?: string;
  user?: AuthenticatedUser;
  admin?: AdminPrincipal;
};
