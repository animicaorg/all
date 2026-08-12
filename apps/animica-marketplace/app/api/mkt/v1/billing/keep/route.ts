import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate, requireScope } from '@/lib/api';
import { getAccountPlan } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// Over-limit selection after a downgrade (spec: the user chooses which resources stay
// active; the rest are shelved, never deleted). kind: workers | deployments | api_keys.
// Everything not in keepIds is moved to its shelved state; kept items are restored.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');
    requireScope(ctx, 'buy');
    let body: any = {};
    try { body = await req.json(); } catch {}
    const kind = String(body?.kind || '');
    let keepIds: string[] = Array.isArray(body?.keepIds) ? body.keepIds.map(String).slice(0, 500) : [];
    const plan = await getAccountPlan(ctx.accountId);

    if (kind === 'workers') {
      const limit = plan.limits.workers;
      if (limit !== -1 && keepIds.length > limit) {
        throw new ApiError(400, 'invalid', `your plan allows ${limit} active Workers`);
      }
      const mine = await prisma.worker.findMany({ where: { accountId: ctx.accountId }, select: { id: true, status: true } });
      const mineIds = new Set(mine.map((w) => w.id));
      // Workspace-shared workers are visible in the list but belong to (and count against)
      // their owner's plan — silently ignore them instead of 403-ing the whole batch.
      keepIds = keepIds.filter((id) => mineIds.has(id));
      // Shelve everything not kept; restore kept ones that were shelved (to PAUSED — the
      // user explicitly starts them again).
      await prisma.worker.updateMany({
        where: { accountId: ctx.accountId, id: { notIn: keepIds }, status: { not: 'PLAN_LIMIT' } },
        data: { status: 'PLAN_LIMIT', statusReason: 'over plan limit — pick active workers in /settings/billing' },
      });
      await prisma.worker.updateMany({
        where: { accountId: ctx.accountId, id: { in: keepIds }, status: 'PLAN_LIMIT' },
        data: { status: 'PAUSED', statusReason: null },
      });
      return ok({ kept: keepIds.length });
    }

    if (kind === 'deployments') {
      const limit = plan.limits.anm_deployments;
      if (limit !== -1 && keepIds.length > limit) {
        throw new ApiError(400, 'invalid', `your plan allows ${limit} active deployments`);
      }
      const mine = await prisma.anmDomain.findMany({
        where: { ownerId: ctx.accountId, status: { in: ['ACTIVE', 'SUSPENDED'] } },
        select: { id: true },
      });
      const mineIds = new Set(mine.map((d) => d.id));
      if (!keepIds.every((id) => mineIds.has(id))) throw new ApiError(403, 'forbidden', 'not your deployment');
      await prisma.anmDomain.updateMany({
        where: { ownerId: ctx.accountId, id: { notIn: keepIds }, status: 'ACTIVE' },
        data: { status: 'SUSPENDED' },
      });
      await prisma.anmDomain.updateMany({
        where: { ownerId: ctx.accountId, id: { in: keepIds }, status: 'SUSPENDED' },
        data: { status: 'ACTIVE' },
      });
      return ok({ kept: keepIds.length });
    }

    if (kind === 'api_keys') {
      const limit = plan.limits.api_keys;
      if (limit !== -1 && keepIds.length > limit) {
        throw new ApiError(400, 'invalid', `your plan allows ${limit} production API keys`);
      }
      const mine = await prisma.apiKey.findMany({
        where: { accountId: ctx.accountId, kind: 'production', status: { in: ['ACTIVE', 'SUSPENDED'] } },
        select: { id: true },
      });
      const mineIds = new Set(mine.map((k) => k.id));
      if (!keepIds.every((id) => mineIds.has(id))) throw new ApiError(403, 'forbidden', 'not your key');
      await prisma.apiKey.updateMany({
        where: { accountId: ctx.accountId, kind: 'production', id: { notIn: keepIds }, status: 'ACTIVE' },
        data: { status: 'SUSPENDED' },
      });
      await prisma.apiKey.updateMany({
        where: { accountId: ctx.accountId, kind: 'production', id: { in: keepIds }, status: 'SUSPENDED' },
        data: { status: 'ACTIVE' },
      });
      return ok({ kept: keepIds.length });
    }

    throw new ApiError(400, 'invalid', 'kind must be workers | deployments | api_keys');
  } catch (e) {
    return err(e);
  }
}
