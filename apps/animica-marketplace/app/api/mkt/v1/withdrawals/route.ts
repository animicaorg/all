import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { holdWithdrawal, LedgerError } from '@/lib/ledger';
import { isAnimicaAddress } from '@/lib/address';
import { config } from '@/lib/config';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/withdrawals { amountNanm, toAddress } -> request an on-chain payout of earnings.
// Funds are HELD from balance immediately; the payout worker sends on-chain with caps + confirm.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'withdraw');
    const body = await req.json();
    const toAddress = String(body.toAddress ?? '').trim().toLowerCase();
    if (!isAnimicaAddress(toAddress)) throw new ApiError(400, 'bad_address', 'valid anim1 address required');
    const amountNanm = BigInt(body.amountNanm ?? 0);
    if (amountNanm < config.minWithdrawalNanm) throw new ApiError(400, 'below_minimum', `minimum withdrawal is ${config.minWithdrawalNanm} nANM`);

    const withdrawal = await prisma.withdrawal.create({
      data: { accountId: ctx.accountId, amountNanm, toAddress, status: 'REQUESTED' },
    });
    try {
      await holdWithdrawal(ctx.accountId, amountNanm, withdrawal.id);
    } catch (e) {
      await prisma.withdrawal.update({ where: { id: withdrawal.id }, data: { status: 'FAILED', error: 'insufficient balance' } });
      if (e instanceof LedgerError) throw new ApiError(402, 'insufficient_funds', 'not enough balance to withdraw');
      throw e;
    }
    return ok({
      withdrawal: jsonSafe(withdrawal),
      note: config.payoutEnabled ? 'queued for on-chain payout' : 'recorded; on-chain payout worker is currently disabled (PAYOUT_ENABLED=0)',
    }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}

// GET /api/mkt/v1/withdrawals -> the caller's withdrawal history.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const withdrawals = await prisma.withdrawal.findMany({
      where: { accountId: ctx.accountId }, orderBy: { createdAt: 'desc' }, take: 50,
    });
    return ok({ withdrawals: jsonSafe(withdrawals) });
  } catch (e) {
    return err(e);
  }
}
