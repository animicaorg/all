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
  // Legacy sessions carry ['*']; purpose-scoped sessions (v2 challenge login) carry only the
  // scopes their challenge purpose grants — see SESSION_PURPOSE_SCOPES in lib/session.ts.
  if (sess) return { accountId: sess.accountId, scopes: sess.scopes, via: 'session' };
  return null;
}

export function requireScope(ctx: AuthContext, scope: ApiScope) {
  if (ctx.scopes.includes('*')) return;
  if (!ctx.scopes.includes(scope)) throw new ApiError(403, 'forbidden', `missing scope: ${scope}`);
}

export class ApiError extends Error {
  status: number;
  code: string;
  // Optional structured payload surfaced in the error envelope (e.g. plan_limit errors carry
  // {feature, limit, used, requiredPlan, upgradeUrl} so clients render contextual upgrade CTAs).
  details?: Record<string, unknown>;
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

// ── Credentialed cross-origin CORS (Game Lab publish from animica.io) ─────────
// The Forge Game Lab (animica.io) publishes a web game to the store AS the creator's wallet
// identity: it runs a devportal challenge login against /auth/* (which sets the anm_mkt_session
// cookie) then POSTs the listing / bundle / price cross-origin. Those calls MUST carry the
// session cookie, so — unlike PUBLIC_CORS above — they need a SPECIFIC allow-listed origin echoed
// back plus `access-control-allow-credentials: true`. NEVER `*` here: `*` is invalid combined with
// credentials, and widening a credentialed surface to any origin is a CSRF/read-leak. Applied ONLY
// to the auth + store-publish routes, and only when the caller is an allow-listed origin.
export const CREDENTIALED_ORIGINS: ReadonlySet<string> = new Set(
  (process.env.STORE_CREDENTIALED_ORIGINS ?? 'https://animica.io')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
);

// The request's Origin iff it is an allow-listed credentialed origin, else null.
export function credentialedOrigin(req: NextRequest): string | null {
  const origin = req.headers.get('origin');
  return origin && CREDENTIALED_ORIGINS.has(origin) ? origin : null;
}

function mergeVary(existing: string | null, add: string): string {
  if (!existing) return add;
  const parts = existing.split(',').map((s) => s.trim().toLowerCase());
  return parts.includes(add.toLowerCase()) ? existing : `${existing}, ${add}`;
}

// Merge credentialed-CORS headers onto a response when the caller is an allow-listed origin.
// No-op on the ACAO/ACAC headers for same-origin / non-allow-listed callers, so same-origin
// behaviour is byte-unchanged; `vary: origin` is always added so a cache never serves a
// cross-origin body to the wrong origin.
export function withCredentialedCors(req: NextRequest, res: NextResponse): NextResponse {
  res.headers.set('vary', mergeVary(res.headers.get('vary'), 'origin'));
  const origin = credentialedOrigin(req);
  if (origin) {
    res.headers.set('access-control-allow-origin', origin);
    res.headers.set('access-control-allow-credentials', 'true');
  }
  return res;
}

// OPTIONS preflight for a credentialed route. Allow-listed origin -> echo it + allow credentials;
// anyone else -> a bare 204 with no ACAO (the browser fails the preflight closed).
export function credentialedPreflight(
  req: NextRequest,
  methods = 'GET, POST, PATCH, PUT, OPTIONS',
): NextResponse {
  const origin = credentialedOrigin(req);
  const headers: Record<string, string> = { vary: 'origin' };
  if (origin) {
    headers['access-control-allow-origin'] = origin;
    headers['access-control-allow-credentials'] = 'true';
    headers['access-control-allow-methods'] = methods;
    headers['access-control-allow-headers'] = 'content-type';
    headers['access-control-max-age'] = '600';
  }
  return new NextResponse(null, { status: 204, headers });
}

// OPTIONS for a route that serves BOTH a public read (wildcard, no creds) and a credentialed
// write (allow-listed origin). Allow-listed origin -> credentialed preflight; everyone else ->
// the public wildcard preflight, so the public catalog/detail reads keep working unchanged.
export function mixedPreflight(req: NextRequest, methods?: string): NextResponse {
  return credentialedOrigin(req) ? credentialedPreflight(req, methods) : publicPreflight();
}

export function err(e: unknown) {
  if (e instanceof ApiError) {
    return NextResponse.json(
      { error: { code: e.code, message: e.message, ...(e.details ? { details: jsonSafe(e.details) } : {}) } },
      { status: e.status },
    );
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
  // Idempotency records are meant to dedup a client's short retry window, not to freeze a
  // response forever. Records never expired, so an A→B→A sequence (revert to a previously-seen
  // body) would replay the STALE cached response and silently skip the DB write. Treat records
  // older than the TTL as absent, and re-run the handler (upserting the fresh response).
  const TTL_MS = 24 * 60 * 60 * 1000;
  const existing = await prisma.idempotencyRecord.findUnique({
    where: { apiKeyId_key: { apiKeyId: ctx.key.keyId, key: idemKey } },
  });
  const fresh = existing && (Date.now() - new Date(existing.createdAt).getTime() < TTL_MS);
  if (existing && fresh) {
    if (existing.bodyHash !== bodyHash) throw new ApiError(422, 'idempotency_mismatch', 'body differs for reused key');
    return ok(JSON.parse(existing.responseJson), { status: existing.statusCode });
  }
  const r = await handler();
  await prisma.idempotencyRecord.upsert({
    where: { apiKeyId_key: { apiKeyId: ctx.key.keyId, key: idemKey } },
    create: {
      apiKeyId: ctx.key.keyId, key: idemKey, bodyHash,
      statusCode: r.status, responseJson: JSON.stringify(jsonSafe(r.data)),
    },
    update: {
      bodyHash, statusCode: r.status,
      responseJson: JSON.stringify(jsonSafe(r.data)), createdAt: new Date(),
    },
  }).catch(() => {});
  return ok(r.data, { status: r.status });
}
