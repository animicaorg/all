import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { getOrCreateDepositAddress } from '@/lib/deposit';
import { jsonSafe, nanmToAnm } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/balance -> the caller's custodial marketplace ledger balance + a personal
// on-chain deposit address to top it up. Auth: web session (browser wallet) or a read-scoped key.
//
// CUSTODIAL BY DESIGN (disclosed): balanceNanm is an in-app ledger balance, backed 1:1 by real ANM
// (deposits land in a per-account deposit address; the balance is withdrawable on-chain any time via
// /withdrawals). Subscriptions renew by debiting THIS balance under a signed StoreConsent — the chain
// has no pull-payment path, so recurring auto-pay is necessarily custodial, not a non-custodial pull.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const acct = await prisma.account.findUnique({
      where: { id: ctx.accountId },
      select: { balanceNanm: true, address: true },
    });
    if (!acct) throw new ApiError(404, 'not_found', 'account not found');

    // Ensure a topup deposit address exists so the wallet top-up UI can show it. First call mints a
    // wallet via the host CLI; later calls just read the row. If the node/CLI is unreachable we still
    // return the balance (depositAddress: null) rather than failing the whole read.
    let depositAddress: string | null = null;
    try {
      const da = await getOrCreateDepositAddress(ctx.accountId, 'topup');
      depositAddress = da.address;
    } catch {
      /* node/CLI unavailable — balance still returned; client can retry for the address */
    }

    return ok(jsonSafe({
      balanceNanm: acct.balanceNanm,
      balanceAnm: nanmToAnm(acct.balanceNanm),
      depositAddress,
      custodial: true,
      note: depositAddress
        ? 'Send ANM to depositAddress to top up. Credited to your marketplace balance after 12-conf finality. Balance is custodial (withdrawable on-chain any time); subscription renewals debit it under your signed consent.'
        : 'Balance is custodial (withdrawable on-chain any time). Deposit address temporarily unavailable — retry shortly.',
    }));
  } catch (e) {
    return err(e);
  }
}
