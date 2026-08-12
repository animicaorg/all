import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError, requireScope } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nextRunAfter } from '@/lib/planConfig';
import { requireWorkerAccess, enqueueRun, mintTriggerToken, workerDto } from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/workers/[id]/actions {action} — lifecycle verbs. run_now is open to
// workspace MEMBERs (that's what a team runbook is for); everything that changes the
// worker's state or secrets needs ADMIN. Workers are free: no slot recheck on start, no
// quota on run_now, and trigger tokens are available to every account.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'workers');
    const body = await req.json().catch(() => ({}));
    const action = String(body.action ?? '');
    if (!['start', 'pause', 'stop', 'run_now', 'regen_trigger_token'].includes(action)) {
      throw new ApiError(400, 'bad_request', 'unknown action');
    }
    const worker = await requireWorkerAccess(params.id, ctx.accountId, action === 'run_now' ? 'read' : 'admin');
    let triggerToken: string | undefined;

    if (action === 'start') {
      // Start always succeeds — including for legacy PLAN_LIMIT rows the runner's fixup
      // has not swept yet (there is no slot count to recheck anymore).
      await prisma.worker.update({
        where: { id: worker.id },
        data: {
          status: 'ACTIVE',
          statusReason: null,
          consecutiveFailures: 0, // explicit restart clears the failure streak
          nextRunAt: worker.scheduleKind === 'interval' && worker.intervalMinutes ? nextRunAfter(worker.intervalMinutes) : null,
        },
      });
    } else if (action === 'pause') {
      await prisma.worker.update({
        where: { id: worker.id },
        data: { status: 'PAUSED', statusReason: null, nextRunAt: null },
      });
    } else if (action === 'stop') {
      // Emergency Stop: nothing of this worker may execute again until an explicit start —
      // the queue is drained too (the runner also refuses manual runs on DISABLED workers).
      // Executions are unmetered, so cancelling the queue is a single conditional flip.
      await prisma.worker.update({
        where: { id: worker.id },
        data: { status: 'DISABLED', statusReason: 'emergency stop', nextRunAt: null },
      });
      await prisma.workerRun.updateMany({
        where: { workerId: worker.id, status: 'PENDING' },
        data: { status: 'CANCELLED', finishedAt: new Date(), error: 'cancelled: emergency stop' },
      });
    } else if (action === 'run_now') {
      // Manual runs may execute on a PAUSED worker (that's how you test one), but a
      // DISABLED worker must stay inert; legacy PLAN_LIMIT rows need a start first (the
      // executor would cancel their manual runs, so enqueueing one would be a lie).
      if (worker.status === 'DISABLED' || worker.status === 'PLAN_LIMIT') {
        throw new ApiError(409, 'worker_disabled', `worker is ${worker.status.toLowerCase()}; start it first`);
      }
      await enqueueRun(worker, 'manual');
    } else if (action === 'regen_trigger_token') {
      const minted = mintTriggerToken();
      await prisma.worker.update({ where: { id: worker.id }, data: { triggerTokenHash: minted.hash } });
      triggerToken = minted.raw; // shown exactly once; only the sha256 is stored
    }

    const fresh = await prisma.worker.findUnique({ where: { id: worker.id } });
    return ok({ worker: workerDto(fresh), ...(triggerToken ? { triggerToken } : {}) });
  } catch (e) {
    return err(e);
  }
}
