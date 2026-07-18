import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { escrowRelease, escrowRefund } from '@/lib/ledger';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/tasks/[id] { action: accept|deliver|release|dispute|refund, ... }
// The escrowed task lifecycle. Worker accepts + delivers; hirer releases (pays worker minus fee) or
// refunds. Reputation accrues on release. This is autonomous agent-to-agent commerce with escrow.
export async function POST(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const body = await req.json().catch(() => ({}));
    const action = String(body.action ?? '');
    const profile = await prisma.agentProfile.findUnique({ where: { accountId: ctx.accountId } });
    const task = await prisma.agentTask.findUnique({ where: { id: params.id }, include: { hirer: true, worker: true } });
    if (!task) throw new ApiError(404, 'not_found', 'task not found');

    const isHirer = profile && task.hirerId === profile.id;
    const isWorker = profile && task.workerId === profile.id;

    switch (action) {
      case 'accept': {
        requireScope(ctx, 'use');
        if (!profile) throw new ApiError(400, 'no_agent_profile', 'register an agent profile first');
        if (task.status !== 'OPEN') throw new ApiError(409, 'bad_state', `task is ${task.status}`);
        if (task.workerHandle && task.workerHandle !== profile.handle) throw new ApiError(403, 'not_invited', 'this task is directed at another agent');
        if (task.hirerId === profile.id) throw new ApiError(400, 'self_hire', 'cannot accept your own task');
        const updated = await prisma.agentTask.update({
          where: { id: task.id },
          data: { workerId: profile.id, status: 'ACCEPTED', acceptedAt: new Date() },
        });
        return ok({ task: jsonSafe(updated) });
      }
      case 'deliver': {
        requireScope(ctx, 'use');
        if (!isWorker) throw new ApiError(403, 'forbidden', 'only the worker can deliver');
        if (task.status !== 'ACCEPTED') throw new ApiError(409, 'bad_state', `task is ${task.status}`);
        const updated = await prisma.agentTask.update({
          where: { id: task.id },
          data: { status: 'DELIVERED', deliverableJson: JSON.stringify(body.deliverable ?? {}), deliveredAt: new Date() },
        });
        return ok({ task: jsonSafe(updated) });
      }
      case 'release': {
        if (!isHirer) throw new ApiError(403, 'forbidden', 'only the hirer can release');
        if (task.status !== 'DELIVERED') throw new ApiError(409, 'bad_state', `task is ${task.status} (must be DELIVERED)`);
        if (!task.workerId || !task.worker) throw new ApiError(409, 'no_worker', 'no worker assigned');
        const { fee, net } = await escrowRelease(task.worker.accountId, task.amountNanm, task.ledgerRef);
        const rating = Number.isFinite(body.rating) ? Math.max(1, Math.min(5, Number(body.rating))) : null;
        const updated = await prisma.$transaction(async (tx) => {
          const t = await tx.agentTask.update({
            where: { id: task.id }, data: { status: 'RELEASED', rating, closedAt: new Date() },
          });
          // Reputation accrual (cannot be self-set — only earned on a real release).
          const w = await tx.agentProfile.findUnique({ where: { id: task.workerId! } });
          if (w) {
            const tasksCompleted = w.tasksCompleted + 1;
            const ratingSum = w.ratingSum + (rating ?? 0);
            const ratingCount = w.ratingCount + (rating ? 1 : 0);
            const reliability = Math.round((tasksCompleted / (tasksCompleted + w.tasksFailed)) * 10000);
            await tx.agentProfile.update({
              where: { id: w.id },
              data: { tasksCompleted, ratingSum, ratingCount, reputation: reliability },
            });
          }
          return t;
        });
        return ok({ task: jsonSafe(updated), paid: { netToWorker: net.toString(), fee: fee.toString() } });
      }
      case 'dispute': {
        if (!isHirer) throw new ApiError(403, 'forbidden', 'only the hirer can dispute');
        if (!['DELIVERED', 'ACCEPTED'].includes(task.status)) throw new ApiError(409, 'bad_state', `task is ${task.status}`);
        const updated = await prisma.agentTask.update({ where: { id: task.id }, data: { status: 'DISPUTED' } });
        return ok({ task: jsonSafe(updated) });
      }
      case 'refund': {
        // Hirer can reclaim escrow on an OPEN (unaccepted) or DISPUTED task.
        if (!isHirer) throw new ApiError(403, 'forbidden', 'only the hirer can refund');
        if (!['OPEN', 'DISPUTED'].includes(task.status)) throw new ApiError(409, 'bad_state', `cannot refund a ${task.status} task`);
        await escrowRefund(task.hirer.accountId, task.amountNanm, task.ledgerRef);
        const updated = await prisma.agentTask.update({ where: { id: task.id }, data: { status: 'REFUNDED', closedAt: new Date() } });
        return ok({ task: jsonSafe(updated) });
      }
      default:
        throw new ApiError(400, 'bad_action', 'action must be accept|deliver|release|dispute|refund');
    }
  } catch (e) {
    return err(e);
  }
}
