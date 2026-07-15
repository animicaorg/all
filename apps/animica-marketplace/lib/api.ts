import { NextRequest, NextResponse } from 'next/server';
import { createHash } from 'node:crypto';
import { prisma } from './db';
import { resolveApiKey, hasScope, rateLimit, type ResolvedKey } from './apikey';
import { verifySession } from './session';
import { jsonSafe } from './nanm';
import type { ApiScope } from './config';

// Unified request auth for API routes: accepts either a Bearer anm_mkt_ key (agents / developers)
// or the web session cookie (browser). Returns the acting accountId + granted scopes.

export interface AuthContext {
  accountId: string;
  scopes: string[]; // '*' for session (full owner rights) or key scopes
  via: 'key' | 'session';
  key?: ResolvedKey;
}

export async function authenticate(req: NextRequest): Promise<AuthContext | null> {
  const bearer = req.headers.get('authorization');
  if (bearer?.startsWith('Bearer ')) {
    const resolved = await resolveApiKey(bearer.slice(7).trim());
    if (!resolved) return null;
    const rl = rateLimit(resolved.keyId, resolved.rateLimitPerMin);
    if (!rl.ok) throw new ApiError(429, 'rate_limited', `retry after ${rl.retryAfter}s`);
    return { accountId: resolved.accountId, scopes: resolved.scopes, via: 'key', key: resolved };
  }
  const cookie = req.cookies.get('anm_mkt_session')?.value;
  const sess = verifySession(cookie);
  if (sess) return { accountId: sess.accountId, scopes: ['*'], via: 'session' };
  return null;
}

export function requireScope(ctx: AuthContext, scope: ApiScope) {
  if (ctx.scopes.includes('*')) return;
  if (!ctx.scopes.includes(scope)) throw new ApiError(403, 'forbidden', `missing scope: ${scope}`);
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function ok(data: any, init?: ResponseInit) {
  return NextResponse.json(jsonSafe(data), init);
}

// ── Public, unauthenticated READ responses ───────────────────────────────────
// Native .anm sites are served by the gateway under a CSP `sandbox` directive, which
// puts them on an OPAQUE origin — so every fetch() they make is cross-origin and the
// browser blocks it without CORS. These endpoints are read-only, carry no secrets and
// accept no credentials, so `*` is safe here.
// NEVER use this on an authenticated route: `*` cannot be combined with credentials,
// and widening an authed surface to any origin is exactly how a CSRF/read-leak lands.
export const PUBLIC_CORS: Record<string, string> = {
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, OPTIONS',
  'access-control-allow-headers': 'content-type',
  'access-control-max-age': '600',
  'vary': 'origin',
};

export function publicOk(data: any, init?: ResponseInit) {
  return NextResponse.json(jsonSafe(data), {
    ...init,
    headers: { ...PUBLIC_CORS, ...((init?.headers as Record<string, string>) ?? {}) },
  });
}

export function publicPreflight() {
  return new NextResponse(null, { status: 204, headers: PUBLIC_CORS });
}

export function err(e: unknown) {
  if (e instanceof ApiError) {
    return NextResponse.json({ error: { code: e.code, message: e.message } }, { status: e.status });
  }
  const msg = e instanceof Error ? e.message : 'internal error';
  return NextResponse.json({ error: { code: 'internal', message: msg } }, { status: 500 });
}

// Durable idempotency for POST (pool-API pattern): keyed by (apiKeyId, Idempotency-Key) + body hash.
export async function withIdempotency(
  req: NextRequest,
  ctx: AuthContext,
  body: any,
  handler: () => Promise<{ status: number; data: any }>,
): Promise<NextResponse> {
  const idemKey = req.headers.get('idempotency-key');
  if (!idemKey || ctx.via !== 'key' || !ctx.key) {
    const r = await handler();
    return ok(r.data, { status: r.status });
  }
  const bodyHash = createHash('sha256').update(JSON.stringify(body ?? {})).digest('hex');
  const existing = await prisma.idempotencyRecord.findUnique({
    where: { apiKeyId_key: { apiKeyId: ctx.key.keyId, key: idemKey } },
  });
  if (existing) {
    if (existing.bodyHash !== bodyHash) throw new ApiError(422, 'idempotency_mismatch', 'body differs for reused key');
    return ok(JSON.parse(existing.responseJson), { status: existing.statusCode });
  }
  const r = await handler();
  await prisma.idempotencyRecord.create({
    data: {
      apiKeyId: ctx.key.keyId,
      key: idemKey,
      bodyHash,
      statusCode: r.status,
      responseJson: JSON.stringify(jsonSafe(r.data)),
    },
  }).catch(() => {});
  return ok(r.data, { status: r.status });
}
