import { NextRequest } from 'next/server';
import { prisma } from './db';
import { config } from './config';
import { authenticate, ApiError } from './api';

// Store admin auth: the MKT_ADMIN_TOKEN header (ops scripts, same gate as /admin/grant)
// OR a session/key-authenticated account with role ADMIN. Returns the acting principal
// for audit notes. Fail-closed: no token configured + no ADMIN account => 401/404.

export async function requireAdmin(req: NextRequest): Promise<{ via: 'token' | 'account'; accountId?: string }> {
  const token = req.headers.get('x-admin-token');
  if (config.adminToken && token && token === config.adminToken) return { via: 'token' };
  const ctx = await authenticate(req);
  if (ctx) {
    const acct = await prisma.account.findUnique({ where: { id: ctx.accountId }, select: { role: true } });
    if (acct?.role === 'ADMIN') return { via: 'account', accountId: ctx.accountId };
  }
  throw new ApiError(401, 'unauthorized', 'admin required');
}
