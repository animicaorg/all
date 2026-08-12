import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { createApiKey } from '@/lib/apikey';
import { requireCanCreateApiKey } from '@/lib/plan';
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
    // SUSPENDED keys stay listed (plan-limit shelving after a downgrade) so /settings/billing
    // can offer the keep/restore picker; only REVOKED keys disappear.
    const keys = await prisma.apiKey.findMany({
      where: { accountId: ctx.accountId, status: { in: ['ACTIVE', 'SUSPENDED'] } },
      select: { id: true, name: true, prefix: true, scopes: true, kind: true, status: true, rateLimitPerMin: true, lastUsedAt: true, createdAt: true },
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
    // kind 'production' (Pro+): counted against the plan's api_keys limit and minted at the
    // plan's api_rate_limit. Basic keys (default) are untouched — never plan-gated.
    const kind = body.kind === 'production' ? 'production' : 'basic';
    let rateLimitPerMin: number | undefined;
    if (kind === 'production') {
      const plan = await requireCanCreateApiKey(ctx.accountId);
      rateLimitPerMin = plan.limits.api_rate_limit;
    }
    const { raw, key } = await createApiKey(ctx.accountId, { name: body.name ?? 'key', scopes, kind, rateLimitPerMin });
    return ok({ apiKey: raw, keyId: key.id, scopes: key.scopes, kind: key.kind, note: 'shown once' }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/keys?id=<keyId> -> revoke one of the caller's own keys (no secret exposed).
// Revoked keys stop resolving immediately (resolveApiKey rejects status !== ACTIVE / revokedAt set).
export async function DELETE(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const id = req.nextUrl.searchParams.get('id')?.trim() || '';
    if (!id) throw new ApiError(400, 'bad_request', 'id required');
    const key = await prisma.apiKey.findUnique({ where: { id }, select: { id: true, accountId: true, status: true } });
    if (!key || key.accountId !== ctx.accountId) throw new ApiError(404, 'not_found', 'key not found');
    if (key.status !== 'REVOKED') {
      await prisma.apiKey.update({ where: { id }, data: { status: 'REVOKED', revokedAt: new Date() } });
    }
    return ok({ revoked: true });
  } catch (e) {
    return err(e);
  }
}
