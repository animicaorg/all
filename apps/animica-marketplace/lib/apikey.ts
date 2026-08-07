import { randomBytes, createHash } from 'node:crypto';
import { prisma } from './db';
import { API_SCOPES, type ApiScope } from './config';

// Developer API keys (pool-API pattern): raw shown once, only sha256 stored.
// Agents receive keys scoped by their owner. Prefix `anm_mkt_`.

export function mintRawKey(): string {
  return 'anm_mkt_' + randomBytes(24).toString('hex');
}

export function hashKey(raw: string): string {
  return createHash('sha256').update(raw).digest('hex');
}

export async function createApiKey(
  accountId: string,
  opts: { name?: string; scopes?: ApiScope[]; rateLimitPerMin?: number; kind?: string } = {},
) {
  const raw = mintRawKey();
  const scopes = (opts.scopes && opts.scopes.length ? opts.scopes : ['read', 'use']).filter((s) =>
    (API_SCOPES as readonly string[]).includes(s),
  );
  const key = await prisma.apiKey.create({
    data: {
      accountId,
      name: opts.name ?? 'default',
      keyHash: hashKey(raw),
      prefix: raw.slice(0, 16),
      scopes,
      rateLimitPerMin: opts.rateLimitPerMin ?? 120,
      // 'production' = tier-gated key (plan-counted, plan rate limit) — the keys route
      // gates minting via requireCanCreateApiKey; default 'basic' behaves as always.
      kind: opts.kind ?? 'basic',
    },
  });
  return { raw, key }; // raw returned exactly once
}

export interface ResolvedKey {
  keyId: string;
  accountId: string;
  scopes: string[];
  rateLimitPerMin: number;
}

export async function resolveApiKey(raw: string | null | undefined): Promise<ResolvedKey | null> {
  if (!raw || !raw.startsWith('anm_mkt_')) return null;
  const rec = await prisma.apiKey.findUnique({ where: { keyHash: hashKey(raw) }, include: { account: true } });
  if (!rec || rec.status !== 'ACTIVE' || rec.revokedAt) return null;
  // fire-and-forget lastUsed touch
  prisma.apiKey.update({ where: { id: rec.id }, data: { lastUsedAt: new Date() } }).catch(() => {});
  return { keyId: rec.id, accountId: rec.accountId, scopes: rec.scopes, rateLimitPerMin: rec.rateLimitPerMin };
}

export function hasScope(resolved: ResolvedKey, scope: ApiScope): boolean {
  return resolved.scopes.includes(scope);
}

// Simple in-process sliding-window limiter (single-instance app, like the pool API).
const buckets = new Map<string, { count: number; resetAt: number }>();
export function rateLimit(keyId: string, perMin: number): { ok: boolean; retryAfter: number } {
  const now = Date.now();
  const b = buckets.get(keyId);
  if (!b || now >= b.resetAt) {
    buckets.set(keyId, { count: 1, resetAt: now + 60_000 });
    return { ok: true, retryAfter: 0 };
  }
  if (b.count >= perMin) return { ok: false, retryAfter: Math.ceil((b.resetAt - now) / 1000) };
  b.count += 1;
  return { ok: true, retryAfter: 0 };
}
