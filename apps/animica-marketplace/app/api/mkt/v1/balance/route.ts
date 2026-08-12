import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/balance -> ledger balance for the acting account.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const acct = await prisma.account.findUnique({
      where: { id: ctx.accountId },
      select: { id: true, address: true, balanceNanm: true, isAgent: true },
    });
    if (!acct) throw new ApiError(404, 'not_found', 'account not found');
    return ok({
      address: acct.address,
      balanceNanm: acct.balanceNanm.toString(),
      balanceAnm: nanmToAnm(acct.balanceNanm),
      isAgent: acct.isAgent,
    });
  } catch (e) {
    return err(e);
  }
}
