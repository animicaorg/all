import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/ledger -> the caller's append-only ledger history.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const entries = await prisma.ledgerEntry.findMany({
      where: { accountId: ctx.accountId },
      orderBy: { createdAt: 'desc' },
      take: Math.min(Number(req.nextUrl.searchParams.get('limit') ?? 100), 500),
      select: { deltaNanm: true, kind: true, ref: true, memo: true, balanceAfter: true, createdAt: true },
    });
    return ok({ entries: jsonSafe(entries) });
  } catch (e) {
    return err(e);
  }
}
