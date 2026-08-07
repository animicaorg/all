import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError, authenticate } from '@/lib/api';
import { billingSummary } from '@/lib/plan';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The /settings/billing payload: current plan + state, every usage meter, and recent
// subscription history. Read-only; any authenticated context (key or session) may read
// its own summary.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'sign in first');
    const summary = await billingSummary(ctx.accountId);
    const history = await prisma.planSubscription.findMany({
      where: { accountId: ctx.accountId },
      orderBy: { createdAt: 'desc' },
      take: 5,
      select: {
        id: true, planKey: true, status: true, priceUsdCents: true, payerEmail: true,
        currentPeriodEnd: true, graceUntil: true, lastPaymentAt: true, lastPaymentAmountCents: true,
        canceledAt: true, createdAt: true,
      },
    });
    return ok({ ...summary, history });
  } catch (e) {
    return err(e);
  }
}
