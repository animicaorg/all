import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { resolvePlan, requireEntitlement } from '@/lib/cloud/entitlements';
import {
  loadOwnedFunction,
  requireEntrypoint,
  parseCapabilities,
  parseVisibility,
  parsePriceModel,
  parseNanm,
  clampTimeoutMs,
  clampMemoryMb,
  liveFunctionStats,
  serializeFunction,
  serializeDeployment,
} from '../shared';

export const dynamic = 'force-dynamic';

// GET    /api/cloud/v1/functions/[id]  -> owner detail (live stats + latest deployment)
// PATCH  /api/cloud/v1/functions/[id]  -> update configuration (slug is immutable)
// DELETE /api/cloud/v1/functions/[id]  -> ARCHIVE. History (versions/deployments/executions)
//                                         is never deleted; the endpoint simply stops serving.

export async function GET(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);

    const [stats, latestVersion, latestDeployment, successAgg] = await Promise.all([
      liveFunctionStats([fn.id]),
      prisma.cloudFunctionVersion.findFirst({
        where: { functionId: fn.id },
        orderBy: { version: 'desc' },
        select: { id: true, version: true, sourceSha3: true, artifactSha3: true, sizeBytes: true, createdAt: true },
      }),
      prisma.cloudDeployment.findFirst({
        where: { functionId: fn.id },
        orderBy: { createdAt: 'desc' },
        include: { version: { select: { version: true } } },
      }),
      prisma.cloudExecution.groupBy({
        by: ['status'],
        where: { functionId: fn.id },
        _count: { _all: true },
      }),
    ]);

    const byStatus = Object.fromEntries(successAgg.map((r) => [r.status, r._count._all]));
    return ok({
      function: serializeFunction(fn, stats.get(fn.id) ?? { executions: 0, developerNanm: 0n, lastExecutedAt: null }),
      executionsByStatus: byStatus,
      latestVersion,
      latestDeployment: latestDeployment ? serializeDeployment(latestDeployment) : null,
    });
  } catch (e) {
    return err(e);
  }
}

export async function PATCH(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);
    if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });

    if (body.slug != null && String(body.slug).trim().toLowerCase() !== fn.slug) {
      throw new ApiError(400, 'immutable', 'slug cannot be changed — it is the function’s public endpoint identity');
    }
    if (body.status != null) {
      throw new ApiError(400, 'immutable', 'status is managed by deploy/archive, not PATCH');
    }

    const data: Record<string, unknown> = {};
    if (body.name != null) data.name = String(body.name).trim().slice(0, 120) || fn.name;
    if (body.description != null) data.description = String(body.description).slice(0, 4000);
    if (body.entrypoint != null) data.entrypoint = requireEntrypoint(body.entrypoint);
    if (body.timeoutMs != null) data.timeoutMs = clampTimeoutMs(body.timeoutMs, fn.timeoutMs);
    if (body.memoryMb != null) data.memoryMb = clampMemoryMb(body.memoryMb, fn.memoryMb);
    if (body.capabilities != null) data.capabilities = parseCapabilities(body.capabilities);
    if (body.perCallNanm != null) data.perCallNanm = parseNanm(body.perCallNanm, 'perCallNanm');
    if (body.requiresAuth != null) data.requiresAuth = Boolean(body.requiresAuth);
    if (body.priceModel != null) data.priceModel = parsePriceModel(body.priceModel);

    if (body.visibility != null) {
      const visibility = parseVisibility(body.visibility);
      if (visibility === 'PUBLIC' && fn.visibility !== 'PUBLIC') {
        const plan = await resolvePlan(ctx.accountId);
        await requireEntitlement(ctx.accountId, 'marketplace_publishing', 1, plan);
      }
      data.visibility = visibility;
    }

    if (body.appId !== undefined) {
      if (body.appId === null) {
        data.appId = null;
      } else {
        const app = await prisma.cloudApp.findUnique({ where: { id: String(body.appId) } });
        if (!app || app.ownerId !== ctx.accountId) throw new ApiError(404, 'not_found', 'app not found');
        if (app.suspendedAt) throw new ApiError(403, 'suspended', 'that app has been suspended');
        data.appId = app.id;
      }
    }

    if (!Object.keys(data).length) throw new ApiError(400, 'bad_request', 'no updatable fields in the request');

    const updated = await prisma.cloudFunction.update({
      where: { id: fn.id },
      data,
      include: { owner: { select: { id: true, address: true, handle: true } } },
    });
    const stats = await liveFunctionStats([fn.id]);
    return ok({
      function: serializeFunction(updated, stats.get(fn.id) ?? { executions: 0, developerNanm: 0n, lastExecutedAt: null }),
    });
  } catch (e) {
    return err(e);
  }
}

export async function DELETE(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);

    // Archive, never hard-delete: versions, deployments, executions and earnings history all
    // remain queryable. The public endpoint stops serving because the executor only runs
    // PUBLISHED functions. Schedules are switched off with a recorded reason.
    const [updated, schedules] = await prisma.$transaction([
      prisma.cloudFunction.update({
        where: { id: fn.id },
        data: { status: 'ARCHIVED' },
        include: { owner: { select: { id: true, address: true, handle: true } } },
      }),
      prisma.cloudSchedule.updateMany({
        where: { functionId: fn.id, enabled: true },
        data: { enabled: false, disabledReason: 'function archived by its owner' },
      }),
    ]);

    return ok({
      function: serializeFunction(updated),
      archived: true,
      schedulesDisabled: schedules.count,
      note: 'History is retained. Redeploying a version (POST /deploy) will publish it again.',
    });
  } catch (e) {
    return err(e);
  }
}
