import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError, requireScope } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nextRunAfter, periodKeyFor, safetyCaps } from '@/lib/planConfig';
import { requireWorkspaceRole } from '@/lib/workspaces';
import {
  sanitizeWorkerPatch,
  assertToolWiring,
  parseTools,
  workerDto,
  MIN_RUN_SECONDS,
  WORKERS_FREE_LIMITS,
  clampFreeInterval,
} from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// UTC start of the current usage period (periodKeyFor is "YYYY-MM"; quotas are gone but the
// executions meter still reports per-calendar-month activity so the number means something).
function periodStart(now: Date = new Date()): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
}

// GET /api/mkt/v1/workers -> the caller's workers (owned + any in workspaces they belong to)
// plus live meters. Workers are free: both meters are straight DB counts (no plan, no usage
// counters), the workers limit is the flat free constant, and executions are unmetered
// (limit -1). `limits` carries the free-tier constants the create/edit form needs.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const memberships = await prisma.workspaceMember.findMany({
      where: { accountId: ctx.accountId, status: 'active' },
      select: { workspaceId: true },
    });
    const wsIds = memberships.map((m) => m.workspaceId);
    const workers = await prisma.worker.findMany({
      where: wsIds.length
        ? { OR: [{ accountId: ctx.accountId }, { workspaceId: { in: wsIds } }] }
        : { accountId: ctx.accountId },
      include: { runs: { orderBy: { createdAt: 'desc' }, take: 1 } }, // lastRun badge
      orderBy: { createdAt: 'desc' },
      take: 200,
    });

    const [owned, execThisMonth, engine] = await Promise.all([
      prisma.worker.count({ where: { accountId: ctx.accountId } }),
      prisma.workerRun.count({ where: { accountId: ctx.accountId, createdAt: { gte: periodStart() } } }),
      prisma.workerEngineState.findUnique({ where: { id: 'workers' } }),
    ]);

    return ok({
      // `owned` distinguishes workers that count against THIS account's free slots from
      // workspace-shared ones (which count against their owner's).
      workers: workers.map((w) => ({ ...workerDto(w, w.runs[0]), owned: w.accountId === ctx.accountId })),
      meters: {
        workers: { used: owned, limit: WORKERS_FREE_LIMITS.maxWorkersPerAccount },
        executions: { used: execThisMonth, limit: -1, period: periodKeyFor() },
      },
      limits: {
        minIntervalMinutes: WORKERS_FREE_LIMITS.minIntervalMinutes,
        maxWorkersPerAccount: WORKERS_FREE_LIMITS.maxWorkersPerAccount,
      },
      enginePaused: engine?.paused ?? false,
    });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/workers -> create a Worker. Free for every account; the only creation
// gate is the flat per-account row cap (abuse control, not a plan). This route owns the
// config validation and the resource-ownership checks (anmName, workspace role).
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');

    // Count-then-create without a transaction is racy in theory, but the cap is an abuse
    // guard, not an entitlement — one worker of drift under a deliberate race is harmless.
    const owned = await prisma.worker.count({ where: { accountId: ctx.accountId } });
    if (owned >= WORKERS_FREE_LIMITS.maxWorkersPerAccount) {
      throw new ApiError(
        409,
        'worker_limit',
        `each account can have up to ${WORKERS_FREE_LIMITS.maxWorkersPerAccount} Workers — delete one you no longer need first`,
      );
    }

    const body = await req.json().catch(() => ({}));
    const patch = sanitizeWorkerPatch(body);
    if (!patch.name) throw new ApiError(400, 'bad_request', 'name required');
    if (!patch.taskPrompt) throw new ApiError(400, 'bad_request', 'taskPrompt required');

    const tools = parseTools(patch.toolsJson ?? '[]');
    assertToolWiring(tools, patch.webhookUrl ?? null, patch.anmName ?? null);

    // anm_publish may only touch a name this account already controls (any status — a
    // SUSPENDED name is still theirs; the runner refuses to publish to non-ACTIVE ones).
    if (patch.anmName) {
      const domain = await prisma.anmDomain.findUnique({ where: { name: patch.anmName } });
      if (!domain || domain.ownerId !== ctx.accountId) {
        throw new ApiError(400, 'bad_request', `you do not own ${patch.anmName}.anm`);
      }
    }
    // Workspace attachment needs ADMIN there — membership row is the only thing that grants
    // access; knowing a workspace id grants nothing.
    let workspaceId: string | null = null;
    if (typeof body.workspaceId === 'string' && body.workspaceId) {
      await requireWorkspaceRole(body.workspaceId, ctx.accountId, 'ADMIN');
      workspaceId = body.workspaceId;
    }

    const scheduleKind = patch.scheduleKind ?? 'manual';
    let intervalMinutes: number | null = null;
    if (scheduleKind === 'interval') {
      if (patch.intervalMinutes === undefined) throw new ApiError(400, 'bad_request', 'intervalMinutes required for interval schedules');
      intervalMinutes = clampFreeInterval(patch.intervalMinutes);
    }

    const worker = await prisma.worker.create({
      data: {
        accountId: ctx.accountId,
        workspaceId,
        name: patch.name,
        purpose: patch.purpose ?? '',
        systemPrompt: patch.systemPrompt ?? '',
        taskPrompt: patch.taskPrompt,
        ...(patch.model !== undefined ? { model: patch.model } : {}),
        ...(patch.temperature !== undefined ? { temperature: patch.temperature } : {}),
        toolsJson: JSON.stringify(tools),
        webhookUrl: patch.webhookUrl ?? null,
        anmName: patch.anmName ?? null,
        scheduleKind,
        intervalMinutes,
        // New workers are born ACTIVE; interval schedules start ticking immediately.
        nextRunAt: scheduleKind === 'interval' && intervalMinutes ? nextRunAfter(intervalMinutes) : null,
        maxRunSeconds: patch.maxRunSeconds ?? Math.min(120, Math.max(MIN_RUN_SECONDS, safetyCaps().maxRunSeconds)),
      },
    });
    return ok({ worker: workerDto(worker) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
