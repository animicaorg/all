import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { flags } from '@/lib/cloud/config';
import { resolvePlan, requireEntitlement, requireSlot } from '@/lib/cloud/entitlements';
import {
  requireSlug,
  requireEntrypoint,
  parseCapabilities,
  parseVisibility,
  parsePriceModel,
  parseNanm,
  clampTimeoutMs,
  clampMemoryMb,
  pageParams,
  liveFunctionStats,
  serializeFunction,
} from './shared';

export const dynamic = 'force-dynamic';

// GET  /api/cloud/v1/functions        -> list MY functions (archived excluded unless requested)
// POST /api/cloud/v1/functions        -> create a function shell (deploy source via /versions)

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const sp = req.nextUrl.searchParams;
    const { take, cursor } = pageParams(sp);
    const status = sp.get('status')?.toUpperCase() ?? null;
    const where: any = { ownerId: ctx.accountId };
    if (status) {
      if (!['DRAFT', 'PUBLISHED', 'SUSPENDED', 'ARCHIVED'].includes(status)) {
        throw new ApiError(400, 'bad_request', 'status must be DRAFT, PUBLISHED, SUSPENDED or ARCHIVED');
      }
      where.status = status;
    } else if (sp.get('all') !== '1') {
      where.status = { not: 'ARCHIVED' };
    }

    const rows = await prisma.cloudFunction.findMany({
      where,
      include: { owner: { select: { id: true, address: true, handle: true } } },
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      take,
    });
    const stats = await liveFunctionStats(rows.map((r) => r.id));
    return ok({
      functions: rows.map((fn) => serializeFunction(fn, stats.get(fn.id) ?? { executions: 0, developerNanm: 0n, lastExecutedAt: null })),
      nextCursor: rows.length === take ? rows[rows.length - 1].id : null,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Python Cloud is temporarily disabled');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });

    return await withIdempotency(req, ctx, body, async () => {
      const slug = requireSlug(body.slug);
      const name = String(body.name ?? slug).trim().slice(0, 120) || slug;
      const description = String(body.description ?? '').slice(0, 4000);
      const entrypoint = body.entrypoint != null ? requireEntrypoint(body.entrypoint) : 'main';
      const timeoutMs = clampTimeoutMs(body.timeoutMs);
      const memoryMb = clampMemoryMb(body.memoryMb);
      const capabilities = parseCapabilities(body.capabilities);
      const perCallNanm = body.perCallNanm != null ? parseNanm(body.perCallNanm, 'perCallNanm') : 0n;
      const requiresAuth = Boolean(body.requiresAuth ?? false);
      const priceModel = body.priceModel != null ? parsePriceModel(body.priceModel) : 'PAY_PER_USE';

      // Plan enforcement against LIVE counts. A function shell occupies a slot from creation so
      // an account cannot hoard unlimited drafts.
      const plan = await resolvePlan(ctx.accountId);
      const existingCount = await prisma.cloudFunction.count({
        where: { ownerId: ctx.accountId, status: { not: 'ARCHIVED' } },
      });
      await requireSlot(ctx.accountId, 'max_functions', existingCount, plan);

      // Visibility: PUBLIC requires the marketplace_publishing entitlement. When the caller
      // did not ask for a specific visibility, default to what their plan actually allows.
      let visibility: 'PUBLIC' | 'UNLISTED' | 'PRIVATE';
      if (body.visibility != null) {
        visibility = parseVisibility(body.visibility);
        if (visibility === 'PUBLIC') await requireEntitlement(ctx.accountId, 'marketplace_publishing', 1, plan);
      } else {
        visibility = plan.limits.marketplace_publishing ? 'PUBLIC' : 'UNLISTED';
      }

      // Optional app attachment — must be the caller's own app.
      let appId: string | null = null;
      if (body.appId != null) {
        const app = await prisma.cloudApp.findUnique({ where: { id: String(body.appId) } });
        if (!app || app.ownerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'app not found');
        if (app.suspendedAt) throw new ApiError(403, 'suspended', 'that app has been suspended');
        appId = app.id;
      }

      const fn = await prisma.cloudFunction
        .create({
          data: {
            ownerId: ctx.accountId,
            appId,
            slug,
            name,
            description,
            entrypoint,
            status: 'DRAFT',
            visibility,
            timeoutMs,
            memoryMb,
            capabilities,
            priceModel,
            perCallNanm,
            requiresAuth,
          },
          include: { owner: { select: { id: true, address: true, handle: true } } },
        })
        .catch((e: any) => {
          if (e?.code === 'P2002') throw new ApiError(409, 'slug_taken', `you already have a function named "${slug}"`);
          throw e;
        });

      return {
        status: 201,
        data: {
          function: serializeFunction(fn, { executions: 0, developerNanm: 0n, lastExecutedAt: null }),
          next: `POST /api/cloud/v1/functions/${fn.id}/versions with {source} to deploy code to it`,
        },
      };
    });
  } catch (e) {
    return err(e);
  }
}
