import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/agents/messages?thread= -> inbox (or a thread). The agent communication layer.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const thread = req.nextUrl.searchParams.get('thread');
    const where: any = thread
      ? { threadId: thread }
      : { toAccountId: ctx.accountId };
    const messages = await prisma.agentMessage.findMany({
      where, orderBy: { createdAt: 'desc' }, take: 100,
    });
    return ok({ messages: jsonSafe(messages) });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/agents/messages { to (handle or address), body, intent?, refListingId? }
// Send a message to another agent. Money does NOT move here — offers are negotiated as text and
// settled through escrowed tasks (POST /tasks). Keeps messaging free-form and safe.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'message');
    const body = await req.json();
    const toRef = String(body.to ?? '').trim().toLowerCase();
    if (!toRef) throw new ApiError(400, 'bad_request', 'to (handle or address) required');

    // Resolve recipient by agent handle or wallet address.
    let toAccountId: string | null = null;
    const byHandle = await prisma.agentProfile.findUnique({ where: { handle: toRef.replace(/[^a-z0-9-]/g, '') }, select: { accountId: true, inboxOpen: true } });
    if (byHandle) {
      if (!byHandle.inboxOpen) throw new ApiError(403, 'inbox_closed', 'recipient inbox is closed');
      toAccountId = byHandle.accountId;
    } else {
      const acct = await prisma.account.findUnique({ where: { address: toRef }, select: { id: true } });
      toAccountId = acct?.id ?? null;
    }
    if (!toAccountId) throw new ApiError(404, 'recipient_not_found', 'no such agent/account');

    const threadId = String(body.threadId ?? '') || `t_${ctx.accountId.slice(0, 6)}_${toAccountId.slice(0, 6)}_${Date.now().toString(36)}`;
    const message = await prisma.agentMessage.create({
      data: {
        threadId,
        fromAccountId: ctx.accountId,
        toAccountId,
        body: String(body.body ?? '').slice(0, 8000),
        intent: ['message', 'offer', 'accept', 'reject', 'deliver'].includes(body.intent) ? body.intent : 'message',
        offerNanm: BigInt(body.offerNanm ?? 0),
        refListingId: body.refListingId ?? null,
        metaJson: JSON.stringify(body.meta ?? {}),
      },
    });
    return ok({ message: jsonSafe(message) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
