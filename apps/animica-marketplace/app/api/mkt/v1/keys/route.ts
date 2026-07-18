import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { createApiKey } from '@/lib/apikey';
import { prisma } from '@/lib/db';
import { API_SCOPES, type ApiScope } from '@/lib/config';

export const dynamic = 'force-dynamic';

// GET  /api/mkt/v1/keys        -> list this account's keys (no secrets)
// POST /api/mkt/v1/keys {name, scopes} -> mint a new scoped key (raw shown once).
// An owner uses this to provision scoped keys for the agents it runs.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const keys = await prisma.apiKey.findMany({
      where: { accountId: ctx.accountId, status: 'ACTIVE' },
      select: { id: true, name: true, prefix: true, scopes: true, rateLimitPerMin: true, lastUsedAt: true, createdAt: true },
      orderBy: { createdAt: 'desc' },
    });
    return ok({ keys });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const body = await req.json().catch(() => ({}));
    const scopes: ApiScope[] = Array.isArray(body.scopes)
      ? body.scopes.filter((s: string) => (API_SCOPES as readonly string[]).includes(s))
      : ['read', 'use'];
    // A key cannot grant scopes the caller itself lacks (except session = full owner).
    if (!ctx.scopes.includes('*')) {
      for (const s of scopes) if (!ctx.scopes.includes(s)) throw new ApiError(403, 'scope_escalation', `cannot grant ${s}`);
    }
    const { raw, key } = await createApiKey(ctx.accountId, { name: body.name ?? 'key', scopes });
    return ok({ apiKey: raw, keyId: key.id, scopes: key.scopes, note: 'shown once' }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
