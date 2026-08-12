import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError, requireScope } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nextRunAfter } from '@/lib/planConfig';
import { requireWorkspaceRole } from '@/lib/workspaces';
import { sanitizeWorkerPatch, assertToolWiring, parseTools, workerDto, requireWorkerAccess, clampFreeInterval } from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/workers/[id] — owner or any active workspace member.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const worker = await requireWorkerAccess(params.id, ctx.accountId, 'read');
    const lastRun = await prisma.workerRun.findFirst({ where: { workerId: worker.id }, orderBy: { createdAt: 'desc' } });
    return ok({ worker: workerDto(worker, lastRun ?? undefined, { config: true }) });
  } catch (e) {
    return err(e);
  }
}

// PATCH /api/mkt/v1/workers/[id] — owner or workspace ADMIN. Status changes go through
// /actions only (start/pause/stop have side effects a bare field write would skip).
export async function PATCH(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    const worker = await requireWorkerAccess(params.id, ctx.accountId, 'admin');
    const body = await req.json().catch(() => ({}));
    const patch = sanitizeWorkerPatch(body);

    // Validate the MERGED config, not the patch in isolation — enabling a tool and wiring
    // it may arrive in separate requests.
    const tools = patch.toolsJson !== undefined ? parseTools(patch.toolsJson) : parseTools(worker.toolsJson);
    const webhookUrl = patch.webhookUrl !== undefined ? patch.webhookUrl : worker.webhookUrl;
    const anmName = patch.anmName !== undefined ? patch.anmName : worker.anmName;
    assertToolWiring(tools, webhookUrl, anmName);
    if (patch.anmName) {
      // Ownership is checked against the WORKER's account (a workspace admin editing the
      // config cannot point the worker at a name only the admin owns).
      const domain = await prisma.anmDomain.findUnique({ where: { name: patch.anmName } });
      if (!domain || domain.ownerId !== worker.accountId) {
        throw new ApiError(400, 'bad_request', `the worker's account does not own ${patch.anmName}.anm`);
      }
    }
    // Moving a worker between workspaces changes who can see/run it — owner only.
    let workspaceChange: { workspaceId: string | null } | null = null;
    if ('workspaceId' in body) {
      if (worker.accountId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'only the owner can move a worker between workspaces');
      if (body.workspaceId == null || body.workspaceId === '') workspaceChange = { workspaceId: null };
      else if (typeof body.workspaceId === 'string') {
        await requireWorkspaceRole(body.workspaceId, ctx.accountId, 'ADMIN');
        workspaceChange = { workspaceId: body.workspaceId };
      } else throw new ApiError(400, 'bad_request', 'workspaceId must be a string or null');
    }

    // Schedule recompute: interval changes re-anchor nextRunAt from now (the free-tier
    // minimum clamps the interval); switching to manual freezes the schedule.
    const scheduleKind = patch.scheduleKind ?? worker.scheduleKind;
    const schedule: { scheduleKind: string; intervalMinutes: number | null; nextRunAt: Date | null } = {
      scheduleKind,
      intervalMinutes: worker.intervalMinutes,
      nextRunAt: worker.nextRunAt,
    };
    if (scheduleKind === 'interval') {
      const requested = patch.intervalMinutes ?? worker.intervalMinutes;
      if (requested == null) throw new ApiError(400, 'bad_request', 'intervalMinutes required for interval schedules');
      const clamped = clampFreeInterval(requested);
      schedule.intervalMinutes = clamped;
      if (clamped !== worker.intervalMinutes || worker.scheduleKind !== 'interval') {
        schedule.nextRunAt = worker.status === 'ACTIVE' ? nextRunAfter(clamped) : null;
      }
    } else if (worker.scheduleKind !== 'manual') {
      schedule.intervalMinutes = null;
      schedule.nextRunAt = null;
    }

    const { toolsJson: _tj, scheduleKind: _sk, intervalMinutes: _im, ...fields } = patch;
    const updated = await prisma.worker.update({
      where: { id: worker.id },
      data: { ...fields, toolsJson: JSON.stringify(tools), ...schedule, ...(workspaceChange ?? {}) },
    });
    return ok({ worker: workerDto(updated, undefined, { config: true }) });
  } catch (e) {
    return err(e);
  }
}

// DELETE /api/mkt/v1/workers/[id] — owning account only (workspace roles never grant
// destruction). Runs cascade-delete with the worker.
export async function DELETE(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    const worker = await requireWorkerAccess(params.id, ctx.accountId, 'owner');
    await prisma.worker.delete({ where: { id: worker.id } });
    return ok({ deleted: true });
  } catch (e) {
    return err(e);
  }
}
