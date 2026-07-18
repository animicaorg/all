import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { prisma } from '@/lib/db';
import { escrowHold, LedgerError } from '@/lib/ledger';
import { jsonSafe } from '@/lib/nanm';
import { randomBytes } from 'node:crypto';

export const dynamic = 'force-dynamic';

async function myProfile(accountId: string) {
  const p = await prisma.agentProfile.findUnique({ where: { accountId } });
  if (!p) throw new ApiError(400, 'no_agent_profile', 'register an agent profile first (POST /agents)');
  return p;
}

// GET /api/mkt/v1/tasks?role=hirer|worker|open&status= -> list tasks.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const sp = req.nextUrl.searchParams;
    const role = sp.get('role') ?? 'open';
    const profile = await prisma.agentProfile.findUnique({ where: { accountId: ctx.accountId } });
    let where: any = {};
    if (role === 'open') where = { status: 'OPEN' };
    else if (role === 'hirer' && profile) where = { hirerId: profile.id };
    else if (role === 'worker' && profile) where = { workerId: profile.id };
    if (sp.get('status')) where.status = sp.get('status');
    const tasks = await prisma.agentTask.findMany({ where, orderBy: { createdAt: 'desc' }, take: 100 });
    return ok({ tasks: jsonSafe(tasks) });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/tasks { title, brief, amountAnm|amountNanm, workerHandle? }
// Hire an agent: funds are escrowed from the hirer's balance immediately. If workerHandle is set,
// it's a direct offer; otherwise it's an open bounty any agent can accept.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'buy');
    const body = await req.json();
    if (!body.title) throw new ApiError(400, 'bad_request', 'title required');
    const amountNanm = BigInt(body.amountNanm ?? 0);
    if (amountNanm <= 0n) throw new ApiError(400, 'bad_amount', 'amountNanm > 0 required');

    return await withIdempotency(req, ctx, body, async () => {
      const hirer = await myProfile(ctx.accountId);
      const ledgerRef = `task_${randomBytes(9).toString('hex')}`;
      try {
        await escrowHold(ctx.accountId, amountNanm, ledgerRef);
      } catch (e) {
        if (e instanceof LedgerError) throw new ApiError(402, 'insufficient_funds', 'top up to fund escrow');
        throw e;
      }
      const task = await prisma.agentTask.create({
        data: {
          hirerId: hirer.id,
          workerHandle: body.workerHandle ? String(body.workerHandle).toLowerCase() : null,
          title: String(body.title).slice(0, 200),
          brief: String(body.brief ?? '').slice(0, 8000),
          inputJson: JSON.stringify(body.input ?? {}),
          amountNanm,
          status: 'OPEN',
          ledgerRef,
        },
      });
      return { status: 201, data: { task: jsonSafe(task) } };
    });
  } catch (e) {
    return err(e);
  }
}
