import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { loadOwnedFunction, pageParams } from '../../shared';

export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/functions/[id]/executions?status=&limit=&cursor=
//
// The owner's execution history for one function: real status, duration, resource usage and
// the CUSTOMER-side money (price / fee / developer share, exact integers). Internal cost
// accounting (COGS, contribution margin) is deliberately NOT exposed here (§74).

const STATUSES = ['QUEUED', 'DISPATCHED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'REJECTED'];

export async function GET(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);

    const sp = req.nextUrl.searchParams;
    const { take, cursor } = pageParams(sp);
    const status = sp.get('status')?.toUpperCase() ?? null;
    if (status && !STATUSES.includes(status)) {
      throw new ApiError(400, 'bad_request', `status must be one of ${STATUSES.join(', ')}`);
    }

    const where: any = { functionId: fn.id };
    if (status) where.status = status;

    const rows = await prisma.cloudExecution.findMany({
      where,
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      take,
      select: {
        id: true,
        requestId: true,
        status: true,
        lane: true,
        callerKind: true,
        callerAccountId: true,
        depth: true,
        errorCode: true,
        error: true,
        httpStatus: true,
        queuedAt: true,
        startedAt: true,
        finishedAt: true,
        durationMs: true,
        cpuMs: true,
        memoryMbMs: true,
        aiTokensIn: true,
        aiTokensOut: true,
        aiCalls: true,
        egressBytes: true,
        priceNanm: true,
        platformFeeNanm: true,
        developerNanm: true,
        providerNanm: true,
        feeBps: true,
        creditNanm: true,
        freeTier: true,
        billed: true,
        version: { select: { version: true } },
        createdAt: true,
      },
    });

    return ok({
      executions: rows.map((e) => ({
        id: e.id,
        requestId: e.requestId,
        status: e.status,
        lane: e.lane,
        version: e.version.version,
        caller: {
          kind: e.callerKind,
          // The owner sees WHO called only as themselves-or-not; other accounts stay private.
          self: e.callerAccountId === ctx.accountId,
          anonymous: e.callerAccountId == null,
        },
        depth: e.depth,
        errorCode: e.errorCode,
        error: e.error,
        httpStatus: e.httpStatus,
        queuedAt: e.queuedAt,
        startedAt: e.startedAt,
        finishedAt: e.finishedAt,
        durationMs: e.durationMs,
        usage: {
          cpuMs: e.cpuMs,
          memoryMbMs: e.memoryMbMs,
          aiTokensIn: e.aiTokensIn,
          aiTokensOut: e.aiTokensOut,
          aiCalls: e.aiCalls,
          egressBytes: e.egressBytes,
        },
        cost: {
          priceNanm: e.priceNanm,
          platformFeeNanm: e.platformFeeNanm,
          developerNanm: e.developerNanm,
          providerNanm: e.providerNanm,
          creditNanm: e.creditNanm,
          feeBps: e.feeBps,
          freeTier: e.freeTier,
          billed: e.billed,
        },
        createdAt: e.createdAt,
      })),
      nextCursor: rows.length === take ? rows[rows.length - 1].id : null,
    });
  } catch (e) {
    return err(e);
  }
}
