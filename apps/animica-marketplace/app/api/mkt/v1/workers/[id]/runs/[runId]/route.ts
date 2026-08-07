import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError, requireScope } from '@/lib/api';
import { prisma } from '@/lib/db';
import { requireWorkerAccess, runDto } from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/workers/[id]/runs/[runId] — one run incl. output, error and the step log.
export async function GET(req: NextRequest, { params }: { params: { id: string; runId: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const worker = await requireWorkerAccess(params.id, ctx.accountId, 'read');
    // Scoped to the worker from the URL — a run id alone must never cross workers.
    const run = await prisma.workerRun.findFirst({ where: { id: params.runId, workerId: worker.id } });
    if (!run) throw new ApiError(404, 'not_found', 'run not found');
    return ok({ run: runDto(run, { full: true }) });
  } catch (e) {
    return err(e);
  }
}
