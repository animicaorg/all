import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The Workers engine kill switch: the WorkerEngineState singleton (id='workers') the runner
// re-reads at the top of EVERY tick — flipping paused here stops scheduling AND claiming
// within a minute, without touching systemd, and RUNNING executions finish their current run.
// Upsert-read so the first GET materializes the row (no seed step to forget).

function engineState() {
  return prisma.workerEngineState.upsert({ where: { id: 'workers' }, create: { id: 'workers' }, update: {} });
}

async function queueCounts() {
  const [pendingRuns, runningRuns, activeWorkers] = await Promise.all([
    prisma.workerRun.count({ where: { status: 'PENDING' } }),
    prisma.workerRun.count({ where: { status: 'RUNNING' } }),
    prisma.worker.count({ where: { status: 'ACTIVE' } }),
  ]);
  return { pendingRuns, runningRuns, activeWorkers };
}

// GET /api/mkt/v1/billing/admin/engine -> { engine, counts }. Admin only.
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const [engine, counts] = await Promise.all([engineState(), queueCounts()]);
    return ok({ engine, counts });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/billing/admin/engine {paused: boolean, note?} — flip the kill switch.
// The note is stamped with the acting principal so the state row is its own audit trail.
export async function POST(req: NextRequest) {
  try {
    const admin = await requireAdmin(req);
    const body = await req.json().catch(() => ({}));
    if (typeof body.paused !== 'boolean') throw new ApiError(400, 'bad_request', 'paused must be a boolean');
    const who = admin.via === 'token' ? 'admin-token' : admin.accountId!;
    const note =
      typeof body.note === 'string' && body.note.trim()
        ? `${body.note.trim().slice(0, 500)} — ${who}`
        : `${body.paused ? 'paused' : 'resumed'} by ${who}`;
    const engine = await prisma.workerEngineState.upsert({
      where: { id: 'workers' },
      create: { id: 'workers', paused: body.paused, note },
      update: { paused: body.paused, note },
    });
    const counts = await queueCounts();
    return ok({ engine, counts });
  } catch (e) {
    return err(e);
  }
}
