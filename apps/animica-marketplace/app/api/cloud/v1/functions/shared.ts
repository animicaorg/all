// Animica Python Cloud REST API — shared route helpers.
//
// Route-local by design (this is not lib/): every /api/cloud/v1 route that touches a
// CloudFunction validates input, resolves ownership and enforces plan quotas through these
// helpers so the rules cannot drift between endpoints. All enforcement is against LIVE
// database state — counts are real COUNT(*) queries, never cached counters (§92).

import { prisma } from '@/lib/db';
import { ApiError } from '@/lib/api';
import { limits, isCapability, type Capability } from '@/lib/cloud/config';
import {
  consumeQuota,
  entitlementError,
  requireEntitlement,
  requireSlot,
  resolvePlan,
  USAGE,
  type ResolvedPlan,
} from '@/lib/cloud/entitlements';

// ---------------------------------------------------------------------------
// Input validation
// ---------------------------------------------------------------------------

// Lowercase DNS-label style: this string becomes part of the public execution URL
// (/api/cloud/v1/fn/{owner}/{slug}) and the fn route lowercases on resolve, so it is
// stored lowercase and immutable after creation.
export const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
export const ENTRYPOINT_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

export const VISIBILITIES = ['PUBLIC', 'UNLISTED', 'PRIVATE'] as const;
export const PRICE_MODELS = ['FREE', 'PAY_PER_USE', 'ONE_TIME', 'SUBSCRIPTION'] as const;

export function requireSlug(v: unknown): string {
  const slug = String(v ?? '')
    .trim()
    .toLowerCase();
  if (slug.length < 2 || slug.length > 63 || !SLUG_RE.test(slug)) {
    throw new ApiError(
      400,
      'bad_slug',
      'slug must be 2-63 characters of a-z, 0-9 and hyphens, with no leading/trailing hyphen',
    );
  }
  return slug;
}

export function requireEntrypoint(v: unknown): string {
  const ep = String(v ?? '').trim();
  if (!ENTRYPOINT_RE.test(ep)) {
    throw new ApiError(400, 'bad_request', 'entrypoint must be a valid Python identifier (max 64 chars)');
  }
  return ep;
}

export function clampInt(v: unknown, min: number, max: number, fallback: number): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.trunc(n), min), max);
}

/** Parse a nANM amount from JSON (string | number | bigint). Integer, >= 0. Never floats. */
export function parseNanm(v: unknown, field: string): bigint {
  try {
    const b = typeof v === 'bigint' ? v : BigInt(String(v).trim());
    if (b < 0n) throw new Error('negative');
    return b;
  } catch {
    throw new ApiError(400, 'bad_request', `${field} must be a non-negative integer amount in nANM`);
  }
}

export function parseCapabilities(v: unknown): Capability[] {
  if (v == null) return [];
  if (!Array.isArray(v)) throw new ApiError(400, 'bad_request', 'capabilities must be an array');
  const out = new Set<Capability>();
  for (const raw of v) {
    const name = String(raw ?? '').trim().toUpperCase();
    if (!name) continue;
    if (!isCapability(name)) {
      throw new ApiError(400, 'bad_capability', `unknown capability "${name}"`);
    }
    out.add(name);
  }
  return [...out];
}

export function parseVisibility(v: unknown): (typeof VISIBILITIES)[number] {
  const s = String(v ?? '').trim().toUpperCase();
  if (!(VISIBILITIES as readonly string[]).includes(s)) {
    throw new ApiError(400, 'bad_request', `visibility must be one of ${VISIBILITIES.join(', ')}`);
  }
  return s as (typeof VISIBILITIES)[number];
}

export function parsePriceModel(v: unknown): (typeof PRICE_MODELS)[number] {
  const s = String(v ?? '').trim().toUpperCase();
  if (!(PRICE_MODELS as readonly string[]).includes(s)) {
    throw new ApiError(400, 'bad_request', `priceModel must be one of ${PRICE_MODELS.join(', ')}`);
  }
  return s as (typeof PRICE_MODELS)[number];
}

export function clampTimeoutMs(v: unknown, fallback = limits.defaultTimeoutMs): number {
  return clampInt(v, limits.minTimeoutMs, limits.maxTimeoutMs, fallback);
}
export function clampMemoryMb(v: unknown, fallback = limits.defaultMemoryMb): number {
  return clampInt(v, limits.minMemoryMb, limits.maxMemoryMb, fallback);
}

// ---------------------------------------------------------------------------
// Ownership + pagination
// ---------------------------------------------------------------------------

/** Load a function the caller owns. Missing OR someone else's => the same 404 (no existence leak). */
export async function loadOwnedFunction(id: string, accountId: string) {
  const fn = await prisma.cloudFunction.findUnique({
    where: { id },
    include: { owner: { select: { id: true, address: true, handle: true } } },
  });
  if (!fn || fn.ownerId !== accountId) throw new ApiError(404, 'not_found', 'function not found');
  return fn;
}

export function endpointFor(owner: { handle: string | null; address: string }, slug: string): string {
  return `/api/cloud/v1/fn/${owner.handle ?? owner.address}/${slug}`;
}

export function pageParams(sp: URLSearchParams, def = 50, max = 200): { take: number; cursor: string | null } {
  return { take: clampInt(sp.get('limit'), 1, max, def), cursor: sp.get('cursor') || null };
}

// ---------------------------------------------------------------------------
// Live per-function stats (real aggregates, not the cached counters)
// ---------------------------------------------------------------------------

export interface LiveFnStats {
  executions: number;
  developerNanm: bigint;
  lastExecutedAt: Date | null;
}

export async function liveFunctionStats(ids: string[]): Promise<Map<string, LiveFnStats>> {
  if (!ids.length) return new Map();
  const rows = await prisma.cloudExecution.groupBy({
    by: ['functionId'],
    where: { functionId: { in: ids } },
    _count: { _all: true },
    _sum: { developerNanm: true },
    _max: { createdAt: true },
  });
  return new Map(
    rows.map((r) => [
      r.functionId,
      { executions: r._count._all, developerNanm: r._sum.developerNanm ?? 0n, lastExecutedAt: r._max.createdAt },
    ]),
  );
}

export function serializeFunction(
  fn: {
    id: string;
    slug: string;
    name: string;
    description: string;
    status: string;
    visibility: string;
    runtime: string;
    entrypoint: string;
    timeoutMs: number;
    memoryMb: number;
    capabilities: string[];
    priceModel: string;
    perCallNanm: bigint;
    requiresAuth: boolean;
    currentVersion: number;
    appId: string | null;
    suspendedAt: Date | null;
    suspendedReason: string | null;
    createdAt: Date;
    updatedAt: Date;
    owner: { handle: string | null; address: string };
  },
  stats?: LiveFnStats,
) {
  return {
    id: fn.id,
    slug: fn.slug,
    name: fn.name,
    description: fn.description,
    status: fn.status,
    visibility: fn.visibility,
    runtime: fn.runtime,
    entrypoint: fn.entrypoint,
    timeoutMs: fn.timeoutMs,
    memoryMb: fn.memoryMb,
    capabilities: fn.capabilities,
    priceModel: fn.priceModel,
    perCallNanm: fn.perCallNanm,
    requiresAuth: fn.requiresAuth,
    currentVersion: fn.currentVersion,
    appId: fn.appId,
    endpoint: endpointFor(fn.owner, fn.slug),
    suspended: Boolean(fn.suspendedAt),
    suspendedReason: fn.suspendedAt ? fn.suspendedReason : null,
    createdAt: fn.createdAt,
    updatedAt: fn.updatedAt,
    ...(stats
      ? {
          stats: {
            executions: stats.executions,
            earnedNanm: stats.developerNanm,
            lastExecutedAt: stats.lastExecutedAt,
          },
        }
      : {}),
  };
}

// ---------------------------------------------------------------------------
// Deploy entitlements (same rules lib/cloud/deploy.ts applies inside createVersion /
// rollbackTo, for the route that deploys an EXISTING version via deployVersion()).
// ---------------------------------------------------------------------------

export async function enforceDeployEntitlements(
  fn: { id: string; ownerId: string; status: string; visibility: string },
  plan?: ResolvedPlan,
): Promise<ResolvedPlan> {
  const p = plan ?? (await resolvePlan(fn.ownerId));

  if (fn.visibility === 'PUBLIC') await requireEntitlement(fn.ownerId, 'marketplace_publishing', 1, p);

  // A deploy that would PUBLISH a not-yet-published function occupies a plan slot; redeploys
  // of an already-published function never do (existing resources keep running on downgrade).
  if (fn.status !== 'PUBLISHED') {
    const published = await prisma.cloudFunction.count({
      where: { ownerId: fn.ownerId, status: 'PUBLISHED', NOT: { id: fn.id } },
    });
    await requireSlot(fn.ownerId, 'max_functions', published, p);
  }

  const limit = p.limits.max_deployments_per_day;
  if (typeof limit === 'number' && limit !== -1) {
    const now = new Date();
    const dayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const used = await prisma.cloudDeployment.count({
      where: { createdAt: { gte: dayStart }, function: { ownerId: fn.ownerId } },
    });
    if (used >= limit) {
      throw entitlementError('max_deployments_per_day', {
        plan: p.key,
        limit,
        used,
        needed: used + 1,
        message: `You have used ${used} of ${limit} deployments today on the ${p.key} plan. The window resets at 00:00 UTC.`,
      });
    }
  }
  // Monthly cloud_deploys counter is bookkeeping (analytics/admin) — never a blocking gate here.
  await consumeQuota(fn.ownerId, USAGE.deploys, 1, p).catch(() => {});
  return p;
}

/** Parse a CloudDeployment.logsJson column into structured lines for API responses. */
export function parseDeployLogs(json: string): Array<{ ts: string; level: string; message: string }> {
  try {
    const a = JSON.parse(json || '[]');
    return Array.isArray(a) ? a : [];
  } catch {
    return [];
  }
}

export function serializeDeployment(dep: {
  id: string;
  functionId: string;
  versionId: string;
  status: string;
  daBlobId: string | null;
  anchorTxid: string | null;
  anchorHeight: number | null;
  anchorConfirms: number;
  registryName: string | null;
  deployerAddress: string | null;
  endpoint: string | null;
  error: string | null;
  logsJson: string;
  createdAt: Date;
  updatedAt: Date;
  activatedAt: Date | null;
  version?: { version: number } | null;
}) {
  return {
    id: dep.id,
    functionId: dep.functionId,
    versionId: dep.versionId,
    version: dep.version?.version ?? null,
    status: dep.status,
    // Anchoring truth: deployments are ANCHORED on-chain (source hash + artifact hash + DA
    // blob id inside a signed DEPLOY tx) and EXECUTED off-chain in a hardened container.
    anchor: {
      daBlobId: dep.daBlobId,
      txid: dep.anchorTxid,
      height: dep.anchorHeight,
      confirmations: dep.anchorConfirms,
      deployerAddress: dep.deployerAddress,
      anchored: Boolean(dep.anchorTxid),
    },
    registryName: dep.registryName,
    endpoint: dep.endpoint,
    error: dep.error,
    logs: parseDeployLogs(dep.logsJson),
    createdAt: dep.createdAt,
    updatedAt: dep.updatedAt,
    activatedAt: dep.activatedAt,
  };
}
