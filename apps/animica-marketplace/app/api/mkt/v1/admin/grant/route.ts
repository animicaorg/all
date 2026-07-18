import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { creditDeposit } from '@/lib/ledger';
import { ensureAccount } from '@/lib/accounts';
import { anmToNanm, jsonSafe } from '@/lib/nanm';
import { config } from '@/lib/config';
import { ok, err, ApiError } from '@/lib/api';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/admin/grant { address, amountAnm }  (header: x-admin-token)
// Seeds ledger balance for testing/promotions. Gated by MKT_ADMIN_TOKEN (disabled if unset).
// NOTE: This does NOT mint real ANM — it credits the internal marketplace ledger only. Real value
// enters via on-chain deposits (POST /deposits). Use for test flows + granted promos.
export async function POST(req: NextRequest) {
  try {
    if (!config.adminToken) throw new ApiError(404, 'disabled', 'admin endpoints disabled');
    if (req.headers.get('x-admin-token') !== config.adminToken) throw new ApiError(401, 'unauthorized', 'bad admin token');
    const body = await req.json();
    const address = String(body.address ?? '').trim().toLowerCase();
    if (!address.startsWith('anim1')) throw new ApiError(400, 'bad_address', 'anim1 address required');
    const amountNanm = anmToNanm(String(body.amountAnm ?? '0'));
    if (amountNanm <= 0n) throw new ApiError(400, 'bad_amount', 'amountAnm > 0');
    const account = await ensureAccount(address);
    const balanceAfter = await creditDeposit(account.id, amountNanm, `admin-grant-${Date.now()}`, 'admin seed (ledger-only)');
    return ok({ address, granted: body.amountAnm, balanceAfter: balanceAfter.toString(), account: jsonSafe(account) });
  } catch (e) {
    return err(e);
  }
}
