import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError, requireScope } from '@/lib/api';
import { prisma } from '@/lib/db';
import { requireWorkerAccess, runDto } from '@/lib/workers';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/workers/[id]/runs?take=50&cursor= — execution history, newest first.
// Lean rows (no output/logs — the detail route serves those); cursor = last run id.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const worker = await requireWorkerAccess(params.id, ctx.accountId, 'read');

    const take = Math.min(Math.max(Number(req.nextUrl.searchParams.get('take')) || 50, 1), 200);
    const cursor = req.nextUrl.searchParams.get('cursor')?.trim() || null;
    const runs = await prisma.workerRun.findMany({
      where: { workerId: worker.id },
      orderBy: { createdAt: 'desc' },
      take,
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
    });
    return ok({
      runs: runs.map((r) => runDto(r)),
      nextCursor: runs.length === take ? runs[runs.length - 1].id : null,
    });
  } catch (e) {
    return err(e);
  }
}
