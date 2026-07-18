import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { getOrCreateDepositAddress, scanDepositAddress } from '@/lib/deposit';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/deposits/address { purpose? } -> mint (or return) a personal deposit address.
// Send ANM there from any wallet; the deposit watcher credits your ledger balance after finality.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const body = await req.json().catch(() => ({}));
    const purpose = String(body.purpose ?? 'topup').slice(0, 40);
    const addr = await getOrCreateDepositAddress(ctx.accountId, purpose);
    return ok({
      address: addr.address,
      purpose: addr.purpose,
      note: 'Send ANM here. Credited to your ledger after 12-conf finality (inclusion is not execution — we credit observed balance deltas only).',
    });
  } catch (e) {
    return err(e);
  }
}

// GET /api/mkt/v1/deposits/address -> list + rescan the caller's deposit addresses (credits new deltas).
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const addrs = await prisma.depositAddress.findMany({ where: { accountId: ctx.accountId, active: true } });
    const results = [];
    for (const a of addrs) {
      let credited = 0n;
      try { credited = (await scanDepositAddress(a.id)).credited; } catch { /* node may be unreachable */ }
      results.push({ address: a.address, purpose: a.purpose, creditedThisScan: credited.toString() });
    }
    return ok({ addresses: jsonSafe(results) });
  } catch (e) {
    return err(e);
  }
}
